"""Every model-backed step in DocSage, each with a deterministic fallback for offline mode."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from functools import cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field

from .providers.base import ChatModel, ImagePart, Message, user
from .providers.offline_provider import STOPWORDS, tokenize
from .retrieval.types import Context, Verdict


@cache
def prompt(name: str) -> str:
    return resources.files("docsage.prompts").joinpath(f"{name}.md").read_text().strip()


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])|\n+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s and s.strip()]


def keywords(text: str) -> list[str]:
    return [t for t in tokenize(text) if len(t) > 1]


# ---------------------------------------------------------------------------------------------
# Ingestion tasks
# ---------------------------------------------------------------------------------------------
def describe_image(llm: ChatModel, image: bytes, *, caption: str, context: str, page: int, kind: str) -> str:
    if not llm.generative or not llm.supports_vision:
        what = "Scanned page" if kind == "scan" else "Figure"
        parts = [f"{what} on page {page}."]
        if caption:
            parts.append(caption.rstrip(".") + ".")
        if context:
            parts.append(f"Nearby text: {context[:400]}")
        return " ".join(parts)
    hint = []
    if caption:
        hint.append(f"Caption: {caption}")
    if context:
        hint.append(f"Surrounding text: {context[:600]}")
    if kind == "scan":
        hint.append("This is a full scanned page; transcribe its text.")
    msg = user(ImagePart(image), "\n".join(hint) or "Describe this figure.")
    text = llm.generate([msg], system=prompt("describe_image"), max_tokens=2048).strip()
    return f"{caption}\n{text}".strip() if caption and caption not in text else text


def describe_table(llm: ChatModel, markdown: str, *, caption: str) -> str:
    if not llm.generative:
        header = markdown.splitlines()[0] if markdown else ""
        cols = [c.strip() for c in header.strip("|").split("|") if c.strip()]
        rows = max(0, len(markdown.splitlines()) - 2)
        lead = f"{caption}. " if caption else ""
        return f"{lead}Table with {rows} rows and columns: {', '.join(cols[:12])}."
    content = (f"Caption: {caption}\n\n" if caption else "") + markdown[:6000]
    return llm.generate([user(content)], system=prompt("describe_table"), max_tokens=1024).strip()


class _Entities(BaseModel):
    class Item(BaseModel):
        id: int
        entities: list[str]

    items: list[Item]


_PROPER = re.compile(
    r"\b(?:[A-Z][a-zA-Z0-9&\-]+(?:\s+(?:of\s+|for\s+|de\s+)?[A-Z][a-zA-Z0-9&\-]+)*|[A-Z]{2,}[0-9]*)\b"
)


def heuristic_entities(text: str) -> list[str]:
    found: Counter[str] = Counter()
    for match in _PROPER.finditer(text):
        phrase = match.group(0).strip()
        if phrase.lower() in STOPWORDS or len(phrase) < 3:
            continue
        found[normalize_entity(phrase)] += 1
    return [e for e, _ in found.most_common(8)]


def normalize_entity(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    return re.sub(r"^(the|a|an)\s+", "", text)


def extract_entities(llm: ChatModel, texts: Sequence[str]) -> list[list[str]]:
    if not llm.generative:
        return [heuristic_entities(t) for t in texts]
    numbered = "\n\n".join(f"[{i + 1}] {t[:1500]}" for i, t in enumerate(texts))
    out = llm.generate_json([user(numbered)], _Entities, system=prompt("entities"), max_tokens=4096)
    by_id = {item.id: [normalize_entity(e) for e in item.entities if e.strip()][:8] for item in out.items}
    return [by_id.get(i + 1, []) for i in range(len(texts))]


# ---------------------------------------------------------------------------------------------
# Query tasks
# ---------------------------------------------------------------------------------------------
class _Query(BaseModel):
    query: str


_REFERENTIAL = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those|he|she|his|her|there|same|above)\b", re.I
)


def rewrite_query(
    llm: ChatModel, question: str, history: Sequence[dict[str, str]], feedback: str = ""
) -> str:
    if not history and not feedback:
        return question
    if not llm.generative:
        query = question
        last_user = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
        if last_user and (_REFERENTIAL.search(question) or len(keywords(question)) < 3):
            query = f"{question} {' '.join(dict.fromkeys(keywords(last_user)))}"
        if feedback:
            query = f"{query} {feedback}"
        return query.strip()
    convo = "\n".join(f"{h['role'].upper()}: {h['content'][:800]}" for h in history[-6:])
    body = f"Conversation:\n{convo or '(none)'}\n\nLatest message: {question}"
    if feedback:
        body += f"\n\nThe previous search missed: {feedback}"
    result = llm.generate_json([user(body)], _Query, system=prompt("rewrite"), max_tokens=1024)
    return result.query.strip() or question


class _Grade(BaseModel):
    relevant: bool
    sufficient: bool
    missing: str = Field(default="")
    useful_sources: list[int] = Field(default_factory=list)


def coverage(question: str, contexts: Sequence[Context]) -> float:
    terms = set(keywords(question))
    if not terms:
        return 0.0
    found: set[str] = set()
    for ctx in contexts:
        found |= terms & set(tokenize(ctx.text + " " + (ctx.title or "")))
    return len(found) / len(terms)


def grade(llm: ChatModel, question: str, contexts: Sequence[Context]) -> Verdict:
    if not contexts:
        return Verdict(False, False, "no sources were retrieved", [], method="none")
    if not llm.generative:
        cov = coverage(question, contexts[:4])
        useful = [c.n for c in contexts if coverage(question, [c]) >= 0.34]
        missing = ", ".join(
            t
            for t in dict.fromkeys(keywords(question))
            if all(t not in tokenize(c.text) for c in contexts[:4])
        )
        return Verdict(
            relevant=cov >= 0.3,
            sufficient=cov >= 0.6,
            missing=missing,
            useful_sources=useful,
            method="heuristic",
        )
    sources = "\n\n".join(c.as_prompt(1500) for c in contexts)
    g = llm.generate_json(
        [user(f"Question: {question}\n\n{sources}")], _Grade, system=prompt("grade"), max_tokens=1024
    )
    return Verdict(g.relevant, g.sufficient, g.missing, g.useful_sources)


class _Scores(BaseModel):
    class Item(BaseModel):
        id: int
        score: float

    scores: list[Item]


def lexical_scores(question: str, texts: Sequence[str]) -> list[float]:
    terms = keywords(question)
    if not terms:
        return [0.0] * len(texts)
    docs = [Counter(tokenize(t)) for t in texts]
    df = Counter(term for d in docs for term in set(d))
    n = len(docs)
    out = []
    for d in docs:
        length = sum(d.values()) or 1
        score = 0.0
        for term in set(terms):
            if d[term]:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                tf = d[term] * 2.2 / (d[term] + 1.2 * (0.25 + 0.75 * length / 120))
                score += idf * tf
        out.append(score)
    return out


def rerank_scores(llm: ChatModel, question: str, contexts: Sequence[Context]) -> list[float]:
    if not llm.generative:
        return lexical_scores(question, [c.text for c in contexts])
    numbered = "\n\n".join(f"[{i + 1}] {c.text[:900]}" for i, c in enumerate(contexts))
    out = llm.generate_json(
        [user(f"Question: {question}\n\nPassages:\n{numbered}")],
        _Scores,
        system=prompt("rerank"),
        max_tokens=2048,
    )
    by_id = {s.id: s.score for s in out.scores}
    return [float(by_id.get(i + 1, 0.0)) for i in range(len(contexts))]


def answer_messages(
    question: str, contexts: Sequence[Context], history: Sequence[dict[str, str]]
) -> list[Message]:
    messages: list[Message] = []
    for h in history[-6:]:
        role: Literal["user", "assistant"] = "assistant" if h["role"] == "assistant" else "user"
        if messages and messages[-1].role == role:
            continue
        messages.append(Message(role, h["content"][:3000]))
    if messages and messages[0].role == "assistant":
        messages = messages[1:]
    if messages and messages[-1].role == "user":
        messages = messages[:-1]
    sources = "\n\n".join(c.as_prompt() for c in contexts) or "(no sources were found)"
    messages.append(user(f"Sources:\n{sources}\n\nQuestion: {question}"))
    return messages


def answer_stream(
    llm: ChatModel, question: str, contexts: Sequence[Context], history: Sequence[dict[str, str]]
) -> Iterator[str]:
    if not llm.generative:
        yield from _extractive_answer(question, contexts)
        return
    yield from llm.stream(answer_messages(question, contexts, history), system=prompt("answer"))


def _extractive_answer(question: str, contexts: Sequence[Context]) -> Iterator[str]:
    terms = set(keywords(question))
    candidates: list[tuple[float, int, str]] = []
    for rank, ctx in enumerate(contexts):
        if ctx.modality == "table":
            rows = [r for r in ctx.text.splitlines() if r.startswith("|")]
            need = min(2, len(terms))
            header_hits = len(terms & set(tokenize(rows[0]))) if rows else 0
            hits = [r for r in rows[2:] if (h := len(terms & set(tokenize(r)))) and h + header_hits >= need]
            if rows and hits:
                table = "\n".join(rows[:2] + hits[:6])
                candidates.append(
                    (
                        len(terms & set(tokenize(" ".join(hits)))) + 1.5 - rank * 0.1,
                        ctx.n,
                        "\n\n" + table + "\n",
                    )
                )
            continue
        for sent in sentences(ctx.text):
            if sent.lstrip().startswith(("|", "#")):
                continue
            clean = sent.lstrip("-* ").strip()
            if len(clean) < 25:
                continue
            overlap = len(terms & set(tokenize(clean)))
            if overlap:
                candidates.append((overlap - rank * 0.1, ctx.n, clean))
    if not candidates:
        yield "I could not find this in the indexed documents."
        return
    candidates.sort(key=lambda c: -c[0])
    picked, seen = [], set()
    for _score, n, text in candidates:
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        picked.append((n, text))
        if len(picked) == 4:
            break
    yield "Here is what the documents say:\n\n"
    for n, text in picked:
        if text.startswith("\n"):
            yield f"Matching table rows [{n}]:{text}\n"
        else:
            yield f"- {text} [{n}]\n"
    yield "\n_Offline mode: these are extracted passages, not a generated answer._"


CITATION = re.compile(r"\[(\d{1,2})\]")


def cited_numbers(answer: str) -> set[int]:
    return {int(m) for m in CITATION.findall(answer)}


# ---------------------------------------------------------------------------------------------
# Evaluation tasks
# ---------------------------------------------------------------------------------------------
class _Judge(BaseModel):
    faithfulness: float
    relevance: float
    correctness: float
    notes: str = ""


def token_f1(a: str, b: str) -> float:
    ta, tb = Counter(keywords(a)), Counter(keywords(b))
    common = sum((ta & tb).values())
    if not common:
        return 0.0
    precision, recall = common / sum(ta.values()), common / sum(tb.values())
    return 2 * precision * recall / (precision + recall)


def judge(
    llm: ChatModel, question: str, answer: str, contexts: Sequence[Context], reference: str
) -> dict[str, Any]:
    if not llm.generative:
        source_terms = set()
        for c in contexts:
            source_terms |= set(tokenize(c.text))
        claims = [s for s in sentences(CITATION.sub("", answer)) if len(keywords(s)) >= 3]
        supported = [s for s in claims if len(set(keywords(s)) & source_terms) / len(set(keywords(s))) >= 0.6]
        q_terms = set(keywords(question))
        return {
            "faithfulness": round(len(supported) / len(claims), 3) if claims else 0.0,
            "relevance": round(len(q_terms & set(keywords(answer))) / len(q_terms), 3) if q_terms else 0.0,
            "correctness": round(token_f1(answer, reference), 3) if reference else None,
            "notes": "heuristic (offline)",
            "method": "heuristic",
        }
    sources = "\n\n".join(c.as_prompt(1200) for c in contexts)
    body = (
        f"Question: {question}\n\nReference answer: {reference or '(none)'}\n\nAnswer:\n{answer}\n\n"
        f"Sources:\n{sources}"
    )
    j = llm.generate_json([user(body)], _Judge, system=prompt("judge"), max_tokens=1024)
    clamp = lambda v: round(max(0.0, min(1.0, float(v))), 3)  # noqa: E731
    return {
        "faithfulness": clamp(j.faithfulness),
        "relevance": clamp(j.relevance),
        "correctness": clamp(j.correctness) if reference else None,
        "notes": j.notes,
        "method": "llm",
    }


class _EvalQ(BaseModel):
    class Item(BaseModel):
        id: int
        question: str
        answer: str

    items: list[Item]


def generate_questions(llm: ChatModel, passages: Sequence[str]) -> list[tuple[int, str, str]]:
    if not llm.generative:
        out = []
        for i, text in enumerate(passages):
            for sent in sentences(text):
                clean = sent.lstrip("#-*| ").strip()
                ents = heuristic_entities(clean)
                if len(clean) > 40 and (ents or re.search(r"\d", clean)):
                    subject = ents[0] if ents else " ".join(keywords(clean)[:3])
                    out.append((i, f"What do the documents say about {subject}?", clean))
                    break
        return out
    numbered = "\n\n".join(f"[{i + 1}] {p[:1500]}" for i, p in enumerate(passages))
    result = llm.generate_json([user(numbered)], _EvalQ, system=prompt("eval_questions"), max_tokens=4096)
    return [
        (item.id - 1, item.question.strip(), item.answer.strip())
        for item in result.items
        if 0 < item.id <= len(passages) and item.question.strip()
    ]

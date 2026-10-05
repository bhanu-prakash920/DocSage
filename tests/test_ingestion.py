from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from docsage.errors import UnsupportedFile
from docsage.ingest.chunking import (
    _Group,
    _normalise_groups,
    agentic_chunks,
    proposition_chunks,
    recursive_chunks,
    semantic_chunks,
)
from docsage.ingest.parsers import kind_for, parse
from docsage.ingest.parsers.base import ParseContext
from docsage.ingest.schema import PageText
from docsage.providers.offline_provider import HashEmbeddings
from docsage.samples import DECK, FIELD_GUIDE, REPORT, RUNBOOK, write_samples
from tests.fakes import FakeChat

LONG = (
    "Solar power is growing quickly across Europe this decade. " * 10
    + "\n\n"
    + "The central bank raised interest rates again in March. " * 10
)


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    return {p.name: p for p in write_samples(tmp_path_factory.mktemp("files"))}


def ctx(tmp_path: Path) -> ParseContext:
    return ParseContext(data_dir=tmp_path, asset_dir=tmp_path / "assets" / "c" / "d")


def test_pdf_parser_finds_text_tables_and_figures(samples, tmp_path, env):
    parsed = parse(samples[REPORT], ctx(tmp_path), env)
    assert parsed.page_count == 3 and "412 million" in parsed.pages[0].text
    assert len(parsed.tables) == 1 and "| Storage" in parsed.tables[0].markdown.replace(
        "|Storage", "| Storage"
    )
    assert parsed.tables[0].page == 2 and parsed.tables[0].image_path
    figure = [i for i in parsed.images if i.page == 3]
    assert figure and figure[0].caption.startswith("Figure 3")
    assert (tmp_path / figure[0].image_path).exists()


def test_docx_pptx_markdown_parsers(samples, tmp_path, env):
    docx = parse(samples[FIELD_GUIDE], ctx(tmp_path), env)
    assert docx.unit == "section" and any("Dunlin" in t.markdown for t in docx.tables)
    assert len(docx.images) == 1 and "Figure 2" in docx.images[0].caption
    deck = parse(samples[DECK], ctx(tmp_path), env)
    assert deck.unit == "slide" and deck.page_count == 4
    assert any("North America" in t.markdown and "8200" in t.markdown for t in deck.tables)
    assert "Speaker notes" in deck.pages[1].text
    md = parse(samples[RUNBOOK], ctx(tmp_path), env)
    assert md.title.startswith("Incident Runbook") and "SEV2" in md.tables[0].markdown


def test_html_csv_and_image_parsers(tmp_path, env):
    buf = io.BytesIO()
    Image.new("RGB", (200, 150), "orange").save(buf, "PNG")
    html = tmp_path / "page.html"
    html.write_text(
        "<html><head><title>Ops</title><script>x()</script></head><body><h1>Status</h1><p>All good today.</p>"
        "<table><tr><th>Svc</th><th>Up</th></tr><tr><td>API</td><td>99.9</td></tr></table>"
        f"<img alt='diagram' src='data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}'></body></html>"
    )
    parsed = parse(html, ctx(tmp_path), env)
    assert parsed.title == "Ops" and "x()" not in parsed.pages[0].text
    assert parsed.tables[0].markdown.startswith("| Svc") and parsed.images[0].caption == "diagram"
    csv = tmp_path / "data.csv"
    csv.write_text("name,score\nada,9\nlin,7\n")
    assert "| ada | 9 |" in parse(csv, ctx(tmp_path), env).tables[0].markdown
    png = tmp_path / "photo.png"
    png.write_bytes(buf.getvalue())
    assert parse(png, ctx(tmp_path), env).images[0].kind == "image-file"
    with pytest.raises(UnsupportedFile):
        kind_for("virus.exe")


def test_recursive_chunks_respect_size_and_overlap():
    chunks = recursive_chunks(LONG, 1, size=400, overlap=80)
    assert all(len(c.text) <= 480 for c in chunks) and len(chunks) >= 3
    assert all(c.page == 1 for c in chunks)


def test_semantic_chunks_never_cross_pages():
    pages = [
        PageText(1, LONG),
        PageText(2, "Cats sleep for most of the day in warm places around the house."),
    ]
    chunks = semantic_chunks(pages, HashEmbeddings(), size=600)
    assert {c.page for c in chunks} == {1, 2}
    assert all("Cats" not in c.text for c in chunks if c.page == 1)


def test_normalise_groups_repairs_overlaps_and_gaps():
    groups = _normalise_groups(
        [
            _Group(title="a", start=1, end=3),
            _Group(title="b", start=2, end=5),
            _Group(title="c", start=8, end=20),
        ],
        10,
    )
    assert [(g.start, g.end) for g in groups] == [(1, 3), (4, 7), (8, 10)]


def test_agentic_and_proposition_chunkers_use_the_model():
    llm = FakeChat()
    chunks = agentic_chunks([PageText(1, LONG * 3)], llm, size=800, overlap=100)
    assert "agentic_chunk" in llm.calls and any(c.title and c.title.startswith("Topic") for c in chunks)
    llm2 = FakeChat()
    props = proposition_chunks([PageText(1, LONG), PageText(2, LONG)], llm2, size=800, overlap=0)
    assert llm2.calls.count("propositions") == 2 and props and all(c.title == "Topic" for c in props)

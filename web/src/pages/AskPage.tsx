import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, History, Layers, MessageSquarePlus, Sparkles, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Composer, type ComposerOptions } from "../components/ask/Composer";
import { SourceCard } from "../components/ask/SourceCard";
import { Turn, type TurnState } from "../components/ask/Turn";
import { Button, ButtonLink } from "../components/ui/Button";
import { Drawer } from "../components/ui/Overlay";
import { EmptyState, ErrorAlert, SkeletonRows, Tabs } from "../components/ui/primitives";
import { useToast } from "../components/ui/Toast";
import { ApiError, api, streamQuery } from "../lib/api";
import { useApp } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { useDocumentTitle, useMediaQuery } from "../lib/hooks";
import type { Conversation, QueryRecord } from "../lib/types";

function fromRecord(q: QueryRecord): TurnState {
  return {
    key: q.id,
    question: q.question,
    status: q.status === "done" ? "done" : "error",
    steps: (q.trace ?? []).map((s) => ({ ...s, status: s.status ?? "done" })),
    sources: q.sources ?? [],
    answer: q.answer ?? "",
    standalone: q.standalone_question,
    verdict: q.verdict,
    usage: { calls: 0, input_tokens: q.input_tokens, output_tokens: q.output_tokens, cost_usd: q.cost_usd },
    latencyMs: q.latency_ms,
    usedWeb: !!q.used_web,
    error: q.status === "failed" ? { message: q.error ?? "Failed" } : null,
    mode: q.mode,
  };
}

function HistoryList({
  conversations,
  activeId,
  onOpen,
  onNew,
  onDelete,
  loading,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  loading: boolean;
}) {
  return (
    <div className="history">
      <Button variant="secondary" block icon={<MessageSquarePlus aria-hidden="true" />} onClick={onNew}>
        New conversation
      </Button>
      <h2 className="eyebrow" style={{ margin: "var(--s-4) 0 var(--s-2)" }}>
        Recent
      </h2>
      {loading ? (
        <SkeletonRows rows={4} height={40} />
      ) : conversations.length === 0 ? (
        <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          Conversations you start appear here.
        </p>
      ) : (
        <ul className="history__list">
          {conversations.map((c) => (
            <li key={c.id} className="history__item" data-active={c.id === activeId || undefined}>
              <button type="button" className="history__open" onClick={() => onOpen(c.id)} aria-current={c.id === activeId}>
                <span className="truncate">{c.title}</span>
                <span className="mono muted">
                  {c.turns} · {fmtRelative(c.updated_at)}
                </span>
              </button>
              <button type="button" className="history__delete" onClick={() => onDelete(c.id)} aria-label={`Delete conversation “${c.title}”`}>
                <Trash2 aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AskPage() {
  const { current, refreshCollections, collectionsLoading } = useApp();
  const cid = current?.id;
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const conversationId = params.get("c");
  const wide = useMediaQuery("(min-width: 1180px)");
  const xwide = useMediaQuery("(min-width: 1500px)");
  useDocumentTitle("Ask");

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const documents = useQuery({ queryKey: ["documents", cid], queryFn: () => api.documents(cid!), enabled: !!cid });
  const conversations = useQuery({ queryKey: ["conversations", cid], queryFn: () => api.conversations(cid!), enabled: !!cid });
  const evalItems = useQuery({ queryKey: ["eval-items", cid], queryFn: () => api.evalItems(cid!), enabled: !!cid });

  const [turns, setTurns] = useState<TurnState[]>([]);
  const [activeTurn, setActiveTurn] = useState(0);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sourceTab, setSourceTab] = useState<"all" | "cited">("all");
  const [options, setOptions] = useState<ComposerOptions>({ mode: "agentic", modalities: [], document_ids: [], rerank: "none", web_search: true });
  const abortRef = useRef<AbortController | null>(null);
  const convRef = useRef<string | null>(conversationId);
  const threadEnd = useRef<HTMLDivElement>(null);
  const sourceRefs = useRef(new Map<number, HTMLElement>());
  const loadedFor = useRef<string | null>(null);

  useEffect(() => {
    if (settings.data) setOptions((o) => ({ ...o, mode: settings.data.values.query_mode, web_search: settings.data.values.web_search }));
  }, [settings.data]);

  // Load a saved conversation when the URL points at one we are not already showing.
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api.conversation(conversationId!),
    enabled: !!conversationId && loadedFor.current !== conversationId,
  });
  useEffect(() => {
    if (conversation.data && loadedFor.current !== conversation.data.id) {
      if (conversation.data.collection_id !== cid) return;
      loadedFor.current = conversation.data.id;
      convRef.current = conversation.data.id;
      const loaded = conversation.data.turns.map(fromRecord);
      setTurns(loaded);
      setActiveTurn(Math.max(0, loaded.length - 1));
    }
  }, [conversation.data, cid]);

  // Switching collections starts a fresh thread.
  const lastCid = useRef(cid);
  useEffect(() => {
    if (lastCid.current && cid !== lastCid.current) {
      abortRef.current?.abort();
      setTurns([]);
      convRef.current = null;
      loadedFor.current = null;
      setParams({}, { replace: true });
    }
    lastCid.current = cid;
  }, [cid, setParams]);

  const busy = turns.some((t) => t.status === "streaming");
  const readyDocs = (documents.data ?? []).filter((d) => d.status === "ready");
  const webAvailable = !!settings.data?.status.web_search_available;

  const update = useCallback((key: string, patch: (t: TurnState) => Partial<TurnState>) => {
    setTurns((ts) => ts.map((t) => (t.key === key ? { ...t, ...patch(t) } : t)));
  }, []);

  const ask = useCallback(
    async (question: string) => {
      if (!cid) return;
      const key = `t${Date.now()}`;
      setTurns((ts) => [...ts, { key, question, status: "streaming", steps: [], sources: [], answer: "", mode: options.mode }]);
      setActiveTurn(turns.length);
      setActiveCitation(null);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamQuery(
          cid,
          {
            question,
            conversation_id: convRef.current,
            mode: options.mode,
            rerank: options.rerank,
            web_search: options.mode === "agentic" && webAvailable && !!options.web_search,
            modalities: options.modalities?.length ? options.modalities : undefined,
            document_ids: options.document_ids?.length ? options.document_ids : undefined,
          },
          (ev) => {
            switch (ev.type) {
              case "start":
                if (ev.conversation_id && convRef.current !== ev.conversation_id) {
                  convRef.current = ev.conversation_id;
                  loadedFor.current = ev.conversation_id;
                  setParams({ c: ev.conversation_id }, { replace: true });
                }
                break;
              case "step":
                update(key, (t) => {
                  const steps = [...t.steps];
                  const idx = steps.map((s) => s.step).lastIndexOf(ev.step);
                  if (ev.status !== "running" && idx >= 0 && steps[idx].status === "running") steps[idx] = ev;
                  else steps.push(ev);
                  return { steps };
                });
                break;
              case "sources":
                update(key, () => ({ sources: ev.sources }));
                break;
              case "verdict":
                update(key, () => ({ verdict: ev }));
                break;
              case "token":
                update(key, (t) => ({ answer: t.answer + ev.text }));
                break;
              case "done":
                update(key, (t) => ({
                  status: "done",
                  answer: ev.answer,
                  sources: ev.sources,
                  standalone: ev.standalone_question,
                  verdict: ev.verdict,
                  usage: ev.usage,
                  latencyMs: ev.latency_ms,
                  usedWeb: ev.used_web,
                  steps: ev.trace?.length ? ev.trace.map((s) => ({ ...s, status: s.status ?? "done" })) : t.steps,
                }));
                break;
              case "error":
                update(key, () => ({ status: "error", error: { message: ev.message, hint: ev.hint } }));
                break;
            }
          },
          controller.signal,
        );
        update(key, (t) => (t.status === "streaming" ? { status: "error", error: { message: "The answer stream ended early." } } : {}));
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          update(key, (t) => ({ status: "error", error: { message: "Stopped.", hint: t.answer ? "The partial answer is shown above." : null }, steps: t.steps.map((s) => (s.status === "running" ? { ...s, status: "error" } : s)) }));
        } else {
          const e = err as ApiError;
          update(key, () => ({ status: "error", error: { message: e.message, hint: e.hint } }));
        }
      } finally {
        abortRef.current = null;
        qc.invalidateQueries({ queryKey: ["conversations", cid] });
        qc.invalidateQueries({ queryKey: ["stats", cid] });
      }
    },
    [cid, options, webAvailable, turns.length, update, setParams, qc],
  );

  // Keep the newest content in view while streaming.
  const lastAnswerLength = turns[turns.length - 1]?.answer.length ?? 0;
  useEffect(() => {
    const el = threadEnd.current;
    if (!el) return;
    const nearBottom = window.innerHeight + window.scrollY > document.body.scrollHeight - 320;
    if (nearBottom || lastAnswerLength === 0) el.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length, lastAnswerLength]);

  const newConversation = () => {
    abortRef.current?.abort();
    setTurns([]);
    convRef.current = null;
    loadedFor.current = null;
    setParams({});
    setHistoryOpen(false);
  };
  const openConversation = (id: string) => {
    abortRef.current?.abort();
    loadedFor.current = null;
    setTurns([]);
    setParams({ c: id });
    setHistoryOpen(false);
  };
  const del = useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["conversations", cid] });
      if (id === convRef.current) newConversation();
      toast("ok", "Conversation deleted");
    },
    onError: (e: ApiError) => toast("err", "Could not delete the conversation", e.message),
  });

  const samples = useMutation({
    mutationFn: api.loadSamples,
    onSuccess: () => {
      refreshCollections();
      toast("ok", "Sample collection created", "Indexing four documents. This takes a few seconds.");
    },
    onError: (e: ApiError) => toast("err", "Could not create the sample collection", e.message),
  });

  const focusCitation = (turnIndex: number, n: number) => {
    setActiveTurn(turnIndex);
    setActiveCitation(n);
    setSourceTab("all");
    if (!wide) setSourcesOpen(true);
    window.setTimeout(() => {
      const el = sourceRefs.current.get(n);
      el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      el?.focus({ preventScroll: true });
    }, wide ? 0 : 320);
  };

  const shownTurn = turns[Math.min(activeTurn, turns.length - 1)];
  const shownSources = useMemo(
    () => (shownTurn?.sources ?? []).filter((s) => sourceTab === "all" || s.cited),
    [shownTurn, sourceTab],
  );
  const suggestions = (evalItems.data ?? []).slice(0, 4).map((i) => i.question);

  const sourcesPanel = (
    <div className="sources">
      <Tabs
        label="Source filter"
        value={sourceTab}
        onChange={setSourceTab}
        tabs={[
          { value: "all", label: "All sources", count: shownTurn?.sources.length ?? 0 },
          { value: "cited", label: "Cited", count: shownTurn?.sources.filter((s) => s.cited).length ?? 0 },
        ]}
      />
      {!shownTurn ? (
        <p className="muted sources__empty">Sources for each answer appear here, with page numbers and figure previews.</p>
      ) : shownSources.length === 0 ? (
        <p className="muted sources__empty">
          {shownTurn.status === "streaming" ? "Searching…" : sourceTab === "cited" ? "This answer cites no sources." : "No sources were retrieved."}
        </p>
      ) : (
        <div className="sources__list">
          {shownSources.map((s) => (
            <SourceCard
              key={`${shownTurn.key}-${s.n}`}
              source={s}
              active={activeCitation === s.n}
              ref={(el) => {
                if (el) sourceRefs.current.set(s.n, el);
                else sourceRefs.current.delete(s.n);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );

  if (collectionsLoading) return <SkeletonRows rows={4} height={72} />;
  if (!current) {
    return (
      <EmptyState icon={<BookOpen />} title="Create your first collection" actions={
        <>
          <Button variant="primary" icon={<Sparkles aria-hidden="true" />} loading={samples.isPending} onClick={() => samples.mutate()}>
            Load sample collection
          </Button>
          <ButtonLink to="/library" icon={<Upload aria-hidden="true" />}>Upload documents</ButtonLink>
        </>
      }>
        DocSage answers questions across your documents and cites the exact page, table or figure it used. Try the sample
        collection to see it work in seconds.
      </EmptyState>
    );
  }

  const noDocs = documents.isSuccess && readyDocs.length === 0;
  const processing = (documents.data ?? []).some((d) => d.status === "queued" || d.status === "processing");

  return (
    <div className={`ask ${xwide ? "ask--xwide" : ""} ${wide ? "ask--wide" : ""}`}>
      {xwide && (
        <aside className="ask__history" aria-label="Conversations">
          <HistoryList
            conversations={conversations.data ?? []}
            activeId={convRef.current}
            onOpen={openConversation}
            onNew={newConversation}
            onDelete={(id) => del.mutate(id)}
            loading={conversations.isLoading}
          />
        </aside>
      )}

      <section className="ask__thread" aria-label="Conversation">
        <header className="ask__head">
          <div className="stack" style={{ gap: 2, minWidth: 0 }}>
            <span className="eyebrow">01 · Ask</span>
            <h1 className="ask__title truncate">{current.name}</h1>
          </div>
          <span className="spacer" />
          {!xwide && (
            <Button size="sm" variant="ghost" icon={<History aria-hidden="true" />} onClick={() => setHistoryOpen(true)}>
              History
            </Button>
          )}
          {!wide && turns.length > 0 && (
            <Button size="sm" variant="secondary" icon={<Layers aria-hidden="true" />} onClick={() => setSourcesOpen(true)}>
              Sources · {shownTurn?.sources.length ?? 0}
            </Button>
          )}
          {turns.length > 0 && (
            <Button size="sm" variant="secondary" icon={<MessageSquarePlus aria-hidden="true" />} onClick={newConversation}>
              New
            </Button>
          )}
        </header>

        {conversation.error && <ErrorAlert error={conversation.error} title="Could not open that conversation" />}

        {turns.length === 0 && !conversation.isLoading ? (
          <div className="ask__intro">
            <p className="ask__kicker mono">{readyDocs.length} indexed {readyDocs.length === 1 ? "document" : "documents"}</p>
            <h2 className="ask__hero">
              Ask across text, <span>tables</span> and figures.
            </h2>
            <p className="secondary ask__lede">
              Every answer cites the page it came from. Agentic mode checks whether the evidence is enough and searches again
              before it answers.
            </p>
            {noDocs ? (
              <EmptyState
                icon={<Upload />}
                title={processing ? "Your documents are being indexed" : "No indexed documents in this collection"}
                actions={
                  <>
                    <ButtonLink to="/library" variant="primary" icon={<Upload aria-hidden="true" />}>
                      {processing ? "View progress" : "Upload documents"}
                    </ButtonLink>
                    {!processing && (
                      <Button icon={<Sparkles aria-hidden="true" />} loading={samples.isPending} onClick={() => samples.mutate()}>
                        Load sample collection
                      </Button>
                    )}
                  </>
                }
              >
                {processing ? "Questions unlock as soon as the first document is ready." : "Add PDFs, Word files, slides, web pages, Markdown or images."}
              </EmptyState>
            ) : (
              suggestions.length > 0 && (
                <div className="suggestions">
                  <span className="eyebrow">Try asking</span>
                  <ul>
                    {suggestions.map((s) => (
                      <li key={s}>
                        <button type="button" className="suggestion" onClick={() => ask(s)} disabled={busy}>
                          {s}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            )}
          </div>
        ) : conversation.isLoading && turns.length === 0 ? (
          <SkeletonRows rows={3} height={90} />
        ) : (
          <div className="thread">
            {turns.map((t, i) => (
              <Turn
                key={t.key}
                turn={t}
                index={i}
                active={i === activeTurn}
                activeCitation={activeCitation}
                onSelect={() => setActiveTurn(i)}
                onCite={(n) => focusCitation(i, n)}
                onRetry={() => ask(t.question)}
              />
            ))}
          </div>
        )}
        <div ref={threadEnd} className="thread__end" />

        <div className="ask__composer">
          <Composer
            busy={busy}
            onSubmit={ask}
            onStop={() => abortRef.current?.abort()}
            options={options}
            setOptions={setOptions}
            documents={readyDocs}
            webAvailable={webAvailable}
            disabled={noDocs}
            autoFocus={!noDocs && wide}
          />
        </div>
      </section>

      {wide && (
        <aside className="ask__sources" aria-label="Sources">
          <div className="ask__sources-head">
            <span className="eyebrow">Evidence</span>
            {shownTurn && (
              <span className="mono muted truncate" style={{ fontSize: "var(--fs-2xs)" }}>
                Q.{String(Math.min(activeTurn, turns.length - 1) + 1).padStart(2, "0")}
              </span>
            )}
          </div>
          {sourcesPanel}
        </aside>
      )}
      {!wide && (
        <Drawer open={sourcesOpen} onClose={() => setSourcesOpen(false)} title="Sources" eyebrow="Evidence">
          {sourcesPanel}
        </Drawer>
      )}
      {!xwide && (
        <Drawer open={historyOpen} onClose={() => setHistoryOpen(false)} title="Conversations" eyebrow={current.name}>
          <HistoryList
            conversations={conversations.data ?? []}
            activeId={convRef.current}
            onOpen={openConversation}
            onNew={newConversation}
            onDelete={(id) => del.mutate(id)}
            loading={conversations.isLoading}
          />
        </Drawer>
      )}
    </div>
  );
}

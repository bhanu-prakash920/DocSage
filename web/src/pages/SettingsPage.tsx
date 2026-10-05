import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, RotateCcw, Save, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button } from "../components/ui/Button";
import { Alert, Badge, ErrorAlert, Field, PageHeader, Panel, PanelHead, Segmented, SkeletonRows, Switch } from "../components/ui/primitives";
import { useToast } from "../components/ui/Toast";
import { api, getToken, setToken, type ApiError } from "../lib/api";
import { useApp } from "../lib/app";
import { useDocumentTitle } from "../lib/hooks";
import type { SettingsValues } from "../lib/types";

const LABELS: Record<string, string> = {
  auto: "Automatic",
  anthropic: "Anthropic (Claude)",
  google: "Google (Gemini)",
  openai: "OpenAI",
  ollama: "Ollama (local)",
  offline: "Offline (no model)",
  local: "Local (sentence-transformers)",
  hash: "Feature hashing (no model)",
  pymupdf: "PyMuPDF (local)",
  llamaparse: "LlamaParse (cloud)",
  docling: "Docling (local, layout-aware)",
  recursive: "Recursive: fast baseline",
  semantic: "Semantic: embedding breakpoints",
  agentic: "Agentic: one model call per page",
  proposition: "Proposition: most expensive",
  none: "Off",
  llm: "Model re-ranking",
  "cross-encoder": "Local cross-encoder",
  simple: "Fast",
};

function Section({ id, title, eyebrow, children }: { id: string; title: string; eyebrow: string; children: ReactNode }) {
  return (
    <Panel aria-labelledby={id}>
      <PanelHead id={id} title={title} eyebrow={eyebrow} />
      <div className="panel__body settings-grid">{children}</div>
    </Panel>
  );
}

export function SettingsPage() {
  const { theme, setTheme } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  useDocumentTitle("Settings");
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<SettingsValues | null>(null);
  const [token, setTokenDraft] = useState(getToken() ?? "");
  useEffect(() => {
    if (settings.data) setDraft(settings.data.values);
  }, [settings.data]);

  const changes = useMemo(() => {
    if (!draft || !settings.data) return {};
    const out: Partial<SettingsValues> = {};
    for (const [k, v] of Object.entries(draft) as [keyof SettingsValues, unknown][]) {
      if (JSON.stringify(v) !== JSON.stringify(settings.data.values[k])) (out as Record<string, unknown>)[k] = v;
    }
    return out;
  }, [draft, settings.data]);
  const dirty = Object.keys(changes).length > 0;

  const save = useMutation({
    mutationFn: () => api.saveSettings(changes),
    onSuccess: (res) => {
      qc.setQueryData(["settings"], res);
      qc.invalidateQueries({ queryKey: ["health"] });
      toast("ok", "Settings saved", "New ingestion jobs and questions use them right away.");
    },
    onError: (e: ApiError) => toast("err", "Settings were not saved", e.message),
  });

  if (settings.isLoading || !draft) {
    return (
      <>
        <PageHeader index="05" title="Settings" />
        {settings.error ? <ErrorAlert error={settings.error} retry={() => settings.refetch()} /> : <SkeletonRows rows={6} height={80} />}
      </>
    );
  }
  const { status, options } = settings.data!;
  const set = <K extends keyof SettingsValues>(key: K, value: SettingsValues[K]) => setDraft({ ...draft, [key]: value });
  const select = (key: keyof SettingsValues, label: string, hint?: string) => (
    <Field label={label} htmlFor={`s-${key}`} hint={hint}>
      <select id={`s-${key}`} className="select" value={String(draft[key] ?? "")} onChange={(e) => set(key, e.target.value as never)}>
        {(options[key] ?? []).map((o) => (
          <option key={o} value={o}>
            {LABELS[o] ?? o}
          </option>
        ))}
      </select>
    </Field>
  );
  const text = (key: keyof SettingsValues, label: string, placeholder: string, hint?: string) => (
    <Field label={label} htmlFor={`s-${key}`} hint={hint}>
      <input
        id={`s-${key}`}
        className="input input--mono"
        value={(draft[key] as string | null) ?? ""}
        placeholder={placeholder}
        onChange={(e) => set(key, (e.target.value.trim() || null) as never)}
        spellCheck={false}
      />
    </Field>
  );
  const number = (key: keyof SettingsValues, label: string, min: number, max: number, step = 1, hint?: string) => (
    <Field label={label} htmlFor={`s-${key}`} hint={hint}>
      <input
        id={`s-${key}`}
        className="input input--mono"
        type="number"
        min={min}
        max={max}
        step={step}
        value={draft[key] as number}
        onChange={(e) => set(key, (e.target.value === "" ? min : Number(e.target.value)) as never)}
      />
    </Field>
  );
  const keyRow = (name: string, label: string, env: string) => (
    <li key={name} className="key-row">
      {status.keys[name] ? <Check aria-hidden="true" className="key-row__ok" /> : <X aria-hidden="true" className="key-row__no" />}
      <span>{label}</span>
      <span className="mono muted">{env}</span>
      <Badge tone={status.keys[name] ? "ok" : "neutral"}>{status.keys[name] ? "Set" : "Not set"}</Badge>
    </li>
  );

  return (
    <>
      <PageHeader index="05" title="Settings" meta={<span className="mono muted" style={{ fontSize: "var(--fs-xs)" }}>data: {settings.data!.data_dir}</span>} />
      <div className="settings">
        <div className="settings__main stack" style={{ gap: "var(--s-5)" }}>
          {status.error && <Alert tone="err" title="The selected provider cannot start">{status.error}</Alert>}
          {!status.generative && !status.error && (
            <Alert tone="warn" title="Running in offline mode">
              No language model is configured, so answers are extracted passages and figures are described from their captions.
              Add a key to <span className="mono">.env</span> and restart the server.
            </Alert>
          )}
          <Section id="sec-models" title="Language models" eyebrow="Answers · ingestion · grading">
            {select("llm_provider", "Provider", "Automatic picks the first provider with a key: Anthropic, Google, then OpenAI.")}
            {select("anthropic_effort", "Claude effort", "Applies to Claude models that support effort.")}
            {text("llm_model", "Answer model", status.llm_model ?? "provider default", "Leave empty for the provider default.")}
            {text("llm_fast_model", "Fast model", status.llm_fast_model ?? "provider default", "Used for figure descriptions, grading and query rewriting.")}
          </Section>
          <Section id="sec-embed" title="Embeddings" eyebrow="Applies to new collections">
            {select("embedding_provider", "Provider")}
            {text("embedding_model", "Model", status.embedding.model)}
            <Field label="Dimensions" htmlFor="s-embedding_dim" hint="Empty uses the model default.">
              <input
                id="s-embedding_dim"
                className="input input--mono"
                type="number"
                min={64}
                max={4096}
                value={draft.embedding_dim ?? ""}
                placeholder={String(status.embedding.dim)}
                onChange={(e) => set("embedding_dim", e.target.value ? Number(e.target.value) : null)}
              />
            </Field>
            <div className="settings-grid__full">
              <Alert tone="info">Existing collections keep the embedding model they were created with, so their indexes stay valid.</Alert>
            </div>
          </Section>
          <Section id="sec-ingest" title="Ingestion" eyebrow="Parsing · chunking · enrichment">
            {select("parser", "Parser")}
            {select("chunking", "Chunking strategy")}
            {number("chunk_size", "Chunk size (characters)", 200, 8000, 50)}
            {number("chunk_overlap", "Overlap (characters)", 0, 2000, 10)}
            <div className="settings-grid__full switch-list">
              <Switch checked={draft.summarize_images} onChange={(v) => set("summarize_images", v)} label="Describe figures with the vision model" description="Charts and diagrams become searchable text." />
              <Switch checked={draft.summarize_tables} onChange={(v) => set("summarize_tables", v)} label="Summarise tables" description="Adds a one-line description above each table." />
              <Switch checked={draft.ocr_tables} onChange={(v) => set("ocr_tables", v)} label="OCR tables in scans and images" description="Needs the optional 'ocr' extra." />
              <Switch checked={draft.graph_rag} onChange={(v) => set("graph_rag", v)} label="Entity graph (GraphRAG, experimental)" description="Extracts entities at ingestion and expands retrieval through them." />
            </div>
          </Section>
          <Section id="sec-retrieval" title="Retrieval and answering" eyebrow="Defaults for new questions">
            <Field label="Default mode" htmlFor="s-mode">
              <Segmented
                label="Default mode"
                value={draft.query_mode}
                onChange={(v) => set("query_mode", v)}
                options={[
                  { value: "agentic", label: "Agentic" },
                  { value: "simple", label: "Fast" },
                ]}
              />
            </Field>
            {select("rerank", "Re-ranking")}
            {number("top_k", "Sources per answer", 1, 30)}
            {number("agent_max_retries", "Search retries (agentic)", 0, 4)}
            <div className="settings-grid__full switch-list">
              <Switch checked={draft.hybrid} onChange={(v) => set("hybrid", v)} label="Hybrid search" description="Combine keyword (BM25) and vector search." />
              <Switch checked={draft.parent_expansion} onChange={(v) => set("parent_expansion", v)} label="Page context" description="Send the surrounding page, not just the matched chunk." />
              <Switch
                checked={draft.web_search}
                onChange={(v) => set("web_search", v)}
                label="Web search fallback"
                description={status.web_search_available ? "Used when documents do not contain the answer." : "Needs TAVILY_API_KEY."}
              />
            </div>
          </Section>
          <Section id="sec-budget" title="Budgets" eyebrow="Hard limits">
            {number("max_ingest_cost_usd", "Per-document ingestion limit (USD)", 0, 1000, 0.25, "Ingestion stops if a document would cost more. 0 disables.")}
            {number("max_query_cost_usd", "Per-question limit (USD)", 0, 100, 0.05, "0 disables.")}
          </Section>
        </div>

        <aside className="settings__side stack" style={{ gap: "var(--s-5)" }}>
          <Panel variant="glass" aria-labelledby="sec-keys">
            <PanelHead id="sec-keys" title="API keys" eyebrow="Read from .env" />
            <div className="panel__body stack">
              <ul className="key-list">
                {keyRow("anthropic", "Anthropic", "ANTHROPIC_API_KEY")}
                {keyRow("google", "Google Gemini", "GOOGLE_API_KEY")}
                {keyRow("openai", "OpenAI", "OPENAI_API_KEY")}
                {keyRow("tavily", "Tavily web search", "TAVILY_API_KEY")}
                {keyRow("llama_cloud", "LlamaParse", "LLAMA_CLOUD_API_KEY")}
              </ul>
              <p className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                Keys are never sent to the browser or written by DocSage. Edit <span className="mono">.env</span> and restart to change them.
              </p>
            </div>
          </Panel>
          <Panel aria-labelledby="sec-appearance">
            <PanelHead id="sec-appearance" title="Appearance" eyebrow="This browser" />
            <div className="panel__body">
              <Segmented
                label="Theme"
                value={theme}
                onChange={setTheme}
                options={[
                  { value: "light", label: "Light" },
                  { value: "system", label: "System" },
                  { value: "dark", label: "Dark" },
                ]}
              />
            </div>
          </Panel>
          <Panel aria-labelledby="sec-token">
            <PanelHead id="sec-token" title="Server access token" eyebrow="Optional" />
            <div className="panel__body stack">
              <Field label="Token" htmlFor="s-token" hint="Only needed when the server sets DOCSAGE_API_TOKEN.">
                <input id="s-token" className="input input--mono" type="password" value={token} onChange={(e) => setTokenDraft(e.target.value)} autoComplete="off" />
              </Field>
              <Button
                icon={<KeyRound aria-hidden="true" />}
                onClick={() => {
                  setToken(token.trim() || null);
                  qc.invalidateQueries();
                  toast("ok", token.trim() ? "Token saved in this browser" : "Token removed");
                }}
              >
                Save token
              </Button>
            </div>
          </Panel>
        </aside>
      </div>

      <div className="savebar glass glass--strong" data-visible={dirty || undefined} role="region" aria-label="Unsaved changes" aria-hidden={!dirty}>
        <span>
          <strong>{Object.keys(changes).length}</strong> unsaved {Object.keys(changes).length === 1 ? "change" : "changes"}
        </span>
        <span className="spacer" />
        <Button icon={<RotateCcw aria-hidden="true" />} onClick={() => setDraft(settings.data!.values)} tabIndex={dirty ? 0 : -1}>
          Discard
        </Button>
        <Button variant="primary" icon={<Save aria-hidden="true" />} loading={save.isPending} onClick={() => save.mutate()} tabIndex={dirty ? 0 : -1}>
          Save changes
        </Button>
      </div>
    </>
  );
}

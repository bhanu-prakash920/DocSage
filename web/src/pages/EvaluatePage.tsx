import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileJson, FlaskConical, ListPlus, Play, Sparkles, Trash2, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { BarList, ChartFrame } from "../components/charts";
import { Button } from "../components/ui/Button";
import { Modal } from "../components/ui/Overlay";
import {
  Alert,
  Badge,
  EmptyState,
  ErrorAlert,
  Field,
  PageHeader,
  Panel,
  PanelHead,
  Progress,
  SkeletonRows,
  StatusBadge,
  Tabs,
} from "../components/ui/primitives";
import { useToast } from "../components/ui/Toast";
import { api, type ApiError } from "../lib/api";
import { useApp } from "../lib/app";
import { fmtCost, fmtDateTime, fmtInt, fmtMs, fmtPct, fmtScore } from "../lib/format";
import { useDocumentTitle } from "../lib/hooks";
import type { EvalItem, EvalRun, EvalSummaryRow } from "../lib/types";

const PRESET_INFO: Record<string, string> = {
  dense: "Vector search only.",
  hybrid: "Vector + BM25 keyword search fused, with page context.",
  "hybrid+rerank": "Hybrid, then the model re-ranks the shortlist.",
  agentic: "Hybrid with evidence grading and query refinement.",
};
const CHUNKING = ["recursive", "semantic", "agentic", "proposition"] as const;

type Metric = "hit_rate" | "mrr" | "faithfulness" | "relevance" | "correctness" | "latency_ms" | "cost_usd";
const METRICS: { value: Metric; label: string; format: (v: number) => string; better: "higher" | "lower"; max?: number }[] = [
  { value: "hit_rate", label: "Hit rate", format: fmtPct, better: "higher", max: 1 },
  { value: "mrr", label: "MRR", format: fmtScore, better: "higher", max: 1 },
  { value: "faithfulness", label: "Faithfulness", format: fmtScore, better: "higher", max: 1 },
  { value: "relevance", label: "Relevance", format: fmtScore, better: "higher", max: 1 },
  { value: "correctness", label: "Correctness", format: fmtScore, better: "higher", max: 1 },
  { value: "latency_ms", label: "Latency", format: fmtMs, better: "lower" },
  { value: "cost_usd", label: "Cost", format: (v) => fmtCost(v, { precise: true }), better: "lower" },
];

function AddItemModal({ cid, open, onClose }: { cid: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ question: "", reference_answer: "", expected_document: "", expected_page: "" });
  const docs = useQuery({ queryKey: ["documents", cid], queryFn: () => api.documents(cid), enabled: open });
  const add = useMutation({
    mutationFn: () =>
      api.addEvalItem(cid, {
        question: form.question.trim(),
        reference_answer: form.reference_answer.trim(),
        expected_document: form.expected_document || null,
        expected_page: form.expected_page ? Number(form.expected_page) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-items", cid] });
      toast("ok", "Question added");
      setForm({ question: "", reference_answer: "", expected_document: "", expected_page: "" });
      onClose();
    },
  });
  const tooShort = form.question.trim().length < 3;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add a test question"
      description="The expected document and page let DocSage measure retrieval hit rate, not just answer quality."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={tooShort} loading={add.isPending} onClick={() => add.mutate()}>
            Add question
          </Button>
        </>
      }
    >
      <div className="stack">
        {add.error && <ErrorAlert error={add.error} title="Could not add the question" />}
        <Field label="Question" htmlFor="ev-q">
          <textarea id="ev-q" className="textarea" rows={2} value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} data-autofocus />
        </Field>
        <Field label="Reference answer" htmlFor="ev-a" hint="Used to score correctness. Optional.">
          <textarea id="ev-a" className="textarea" rows={2} value={form.reference_answer} onChange={(e) => setForm({ ...form, reference_answer: e.target.value })} />
        </Field>
        <div className="form-grid">
          <Field label="Expected document" htmlFor="ev-d">
            <select id="ev-d" className="select" value={form.expected_document} onChange={(e) => setForm({ ...form, expected_document: e.target.value })}>
              <option value="">Any document</option>
              {(docs.data ?? [])
                .filter((d) => d.status === "ready")
                .map((d) => (
                  <option key={d.id} value={d.filename}>
                    {d.filename}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Expected page" htmlFor="ev-p" hint="Page, slide or section number.">
            <input id="ev-p" className="input input--mono" type="number" min={1} value={form.expected_page} onChange={(e) => setForm({ ...form, expected_page: e.target.value })} />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

function ImportModal({ cid, open, onClose }: { cid: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (items: Partial<EvalItem>[]) => api.importEvalItems(cid, items),
    onSuccess: (items) => {
      qc.invalidateQueries({ queryKey: ["eval-items", cid] });
      toast("ok", `Imported ${items.length} questions`);
      setText("");
      onClose();
    },
  });
  const submit = () => {
    try {
      const raw = text.trim().startsWith("[") ? JSON.parse(text) : text.trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
      if (!Array.isArray(raw) || raw.some((r) => typeof r?.question !== "string")) throw new Error("Each item needs a “question” string.");
      setParseError(null);
      run.mutate(raw);
    } catch (e) {
      setParseError((e as Error).message);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title="Import a golden set"
      description="Paste a JSON array or JSON Lines. Fields: question, reference_answer, expected_document, expected_page, tag."
      footer={
        <>
          <label className="btn btn--ghost" style={{ marginRight: "auto" }}>
            <FileJson aria-hidden="true" />
            Load file
            <input
              type="file"
              accept=".json,.jsonl,application/json"
              className="sr-only"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (f) setText(await f.text());
              }}
            />
          </label>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!text.trim()} loading={run.isPending} onClick={submit}>
            Import
          </Button>
        </>
      }
    >
      <div className="stack">
        {(parseError || run.error) && <ErrorAlert error={parseError ? { message: parseError } : run.error} title="Could not import" />}
        <Field label="Items" htmlFor="ev-json">
          <textarea
            id="ev-json"
            className="textarea input--mono"
            rows={12}
            value={text}
            spellCheck={false}
            placeholder='[{"question": "What was revenue in 2025?", "reference_answer": "412 million euros", "expected_document": "report.pdf", "expected_page": 1}]'
            onChange={(e) => setText(e.target.value)}
            data-autofocus
          />
        </Field>
      </div>
    </Modal>
  );
}

function Results({ run }: { run: EvalRun }) {
  const [metric, setMetric] = useState<Metric>("hit_rate");
  const [itemFilter, setItemFilter] = useState<string>("all");
  const meta = METRICS.find((m) => m.value === metric)!;
  const summary = run.results?.summary ?? [];
  const items = (run.results?.items ?? []).filter((i) => itemFilter === "all" || i.config === itemFilter);
  const bestOf = (key: keyof EvalSummaryRow, better: "higher" | "lower") => {
    const vals = summary.map((s) => s[key]).filter((v): v is number => typeof v === "number");
    if (vals.length < 2 || new Set(vals).size < 2) return null;
    return better === "higher" ? Math.max(...vals) : Math.min(...vals);
  };
  return (
    <div className="stack" style={{ gap: "var(--s-5)" }}>
      <div className="panel table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Configuration</th>
              {METRICS.map((m) => (
                <th key={m.value} className="num">
                  {m.label}
                </th>
              ))}
              <th className="num">Errors</th>
            </tr>
          </thead>
          <tbody>
            {summary.map((s) => (
              <tr key={s.config}>
                <td style={{ fontWeight: 600 }}>{s.config}</td>
                {METRICS.map((m) => {
                  const v = s[m.value as keyof EvalSummaryRow] as number | null;
                  const best = v != null && v === bestOf(m.value as keyof EvalSummaryRow, m.better);
                  return (
                    <td key={m.value} className="num" data-best={best || undefined}>
                      {v == null ? "–" : m.format(v)}
                      {best && <span className="best-mark" aria-label="best"> ▲</span>}
                    </td>
                  );
                })}
                <td className="num">{s.errors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: "calc(-1 * var(--s-3))" }}>
        ▲ marks the best value in each column. Judge: <span className="mono">{run.results?.judge}</span> · total cost {fmtCost(run.cost_usd, { precise: true })}
      </p>

      <Panel>
        <div className="panel__body">
          <ChartFrame
            title={`${meta.label} by configuration`}
            subtitle={meta.better === "higher" ? "Higher is better" : "Lower is better"}
            legend={
              <Tabs label="Metric" value={metric} onChange={setMetric} tabs={METRICS.map((m) => ({ value: m.value, label: m.label }))} />
            }
            table={
              <table className="table">
                <thead>
                  <tr>
                    <th>Configuration</th>
                    <th className="num">{meta.label}</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.map((s) => (
                    <tr key={s.config}>
                      <td>{s.config}</td>
                      <td className="num">{s[metric] == null ? "–" : meta.format(s[metric] as number)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }
          >
            <BarList
              label={`${meta.label} by configuration`}
              rows={summary.map((s) => ({ key: s.config, label: s.config, value: s[metric] as number | null }))}
              max={meta.max}
              format={meta.format}
              better={meta.better}
            />
          </ChartFrame>
        </div>
      </Panel>

      <section className="stack" style={{ gap: 8 }}>
        <div className="row row--wrap">
          <h3 className="section-title" style={{ margin: 0 }}>
            Per-question results
          </h3>
          <span className="spacer" />
          <label className="sr-only" htmlFor="ev-filter">
            Filter by configuration
          </label>
          <select id="ev-filter" className="select" style={{ width: "auto" }} value={itemFilter} onChange={(e) => setItemFilter(e.target.value)}>
            <option value="all">All configurations</option>
            {summary.map((s) => (
              <option key={s.config} value={s.config}>
                {s.config}
              </option>
            ))}
          </select>
        </div>
        <div className="panel table-wrap" style={{ maxHeight: 520 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Question</th>
                <th>Config</th>
                <th>Found</th>
                <th className="num">Faith.</th>
                <th className="num">Rel.</th>
                <th className="num">Corr.</th>
                <th className="num">Latency</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={`${i.config}-${i.item_id}`}>
                  <td style={{ minWidth: 260 }}>
                    <details className="answer-details">
                      <summary>{i.question}</summary>
                      <p className="secondary">{i.error ?? (i.answer || "No answer")}</p>
                      {i.notes && <p className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>{i.notes}</p>}
                    </details>
                  </td>
                  <td className="mono" style={{ fontSize: "var(--fs-xs)", whiteSpace: "nowrap" }}>
                    {i.config}
                  </td>
                  <td>{i.hit == null ? <span className="muted">n/a</span> : i.hit ? <Badge tone="ok">Yes</Badge> : <Badge tone="err">No</Badge>}</td>
                  <td className="num">{fmtScore(i.faithfulness)}</td>
                  <td className="num">{fmtScore(i.relevance)}</td>
                  <td className="num">{fmtScore(i.correctness)}</td>
                  <td className="num">{fmtMs(i.latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export function EvaluatePage() {
  const { current, collectionsLoading } = useApp();
  const cid = current?.id;
  const qc = useQueryClient();
  const toast = useToast();
  useDocumentTitle("Evaluate");
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [presets, setPresets] = useState<string[]>(["dense", "hybrid", "agentic"]);
  const [chunking, setChunking] = useState<string[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);

  const items = useQuery({ queryKey: ["eval-items", cid], queryFn: () => api.evalItems(cid!), enabled: !!cid });
  const runs = useQuery({
    queryKey: ["eval-runs", cid],
    queryFn: () => api.evalRuns(cid!),
    enabled: !!cid,
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 1500 : false),
  });
  const active = runs.data?.find((r) => r.status === "queued" || r.status === "running");
  const job = useQuery({
    queryKey: ["job", active?.job_id],
    queryFn: () => api.jobs(cid, true),
    enabled: !!active,
    refetchInterval: 1200,
    select: (jobs) => jobs.find((j) => j.id === active?.job_id),
  });
  useEffect(() => {
    setSelectedRun(null);
  }, [cid]);
  const shown = useMemo(() => {
    const done = (runs.data ?? []).filter((r) => r.status !== "queued" && r.status !== "running");
    return done.find((r) => r.id === selectedRun) ?? done[0] ?? null;
  }, [runs.data, selectedRun]);

  const del = useMutation({
    mutationFn: (id: string) => api.deleteEvalItem(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-items", cid] }),
  });
  const generate = useMutation({
    mutationFn: () => api.generateEvalItems(cid!, 6),
    onSuccess: (xs) => {
      qc.invalidateQueries({ queryKey: ["eval-items", cid] });
      toast("ok", `Generated ${xs.length} questions`, "Review them before relying on the scores.");
    },
    onError: (e: ApiError) => toast("err", "Could not generate questions", e.message),
  });
  const start = useMutation({
    mutationFn: () => api.startEval(cid!, presets, chunking.length ? chunking : null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-runs", cid] });
      qc.invalidateQueries({ queryKey: ["jobs", cid, "active"] });
      toast("ok", "Evaluation started");
    },
    onError: (e: ApiError) => toast("err", "Could not start the evaluation", e.message),
  });

  if (collectionsLoading) return <SkeletonRows rows={5} height={64} />;
  if (!current) {
    return (
      <>
        <PageHeader index="04" title="Evaluate" />
        <EmptyState icon={<FlaskConical />} title="No collection selected">
          Create a collection in the library first.
        </EmptyState>
      </>
    );
  }

  const n = items.data?.length ?? 0;
  const runCount = (presets.length || 0) * Math.max(1, chunking.length);
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <>
      <PageHeader
        index="04"
        title="Evaluate"
        meta={
          <span>
            Measure retrieval and answer quality on <strong style={{ color: "var(--ink)" }}>{current.name}</strong> with a golden question set.
          </span>
        }
      />
      <div className="eval">
        <Panel className="eval__set" aria-labelledby="golden-title">
          <PanelHead
            id="golden-title"
            title="Golden set"
            eyebrow={`${fmtInt(n)} questions`}
            actions={
              <div className="row" style={{ gap: 4 }}>
                <Button size="sm" variant="ghost" icon={<FileJson aria-hidden="true" />} onClick={() => setImporting(true)}>
                  Import
                </Button>
                <Button size="sm" variant="ghost" icon={<Wand2 aria-hidden="true" />} loading={generate.isPending} onClick={() => generate.mutate()}>
                  Generate
                </Button>
                <Button size="sm" icon={<ListPlus aria-hidden="true" />} onClick={() => setAdding(true)}>
                  Add
                </Button>
              </div>
            }
          />
          {items.isLoading ? (
            <div className="panel__body"><SkeletonRows rows={5} height={40} /></div>
          ) : items.error ? (
            <div className="panel__body"><ErrorAlert error={items.error} retry={() => items.refetch()} /></div>
          ) : n === 0 ? (
            <div className="panel__body">
              <EmptyState
                icon={<ListPlus />}
                title="No test questions yet"
                actions={
                  <>
                    <Button variant="primary" icon={<ListPlus aria-hidden="true" />} onClick={() => setAdding(true)}>
                      Add a question
                    </Button>
                    <Button icon={<Sparkles aria-hidden="true" />} loading={generate.isPending} onClick={() => generate.mutate()}>
                      Generate from documents
                    </Button>
                  </>
                }
              >
                Questions with known answers turn chunking and retrieval choices into numbers you can compare.
              </EmptyState>
            </div>
          ) : (
            <ul className="golden">
              {items.data!.map((it, i) => (
                <li key={it.id} className="golden__item">
                  <span className="mono muted golden__i">{String(i + 1).padStart(2, "0")}</span>
                  <div className="stack" style={{ gap: 2, minWidth: 0, flex: 1 }}>
                    <span className="break" style={{ fontWeight: 600, fontSize: "var(--fs-sm)" }}>{it.question}</span>
                    {it.reference_answer && <span className="secondary break" style={{ fontSize: "var(--fs-xs)" }}>{it.reference_answer}</span>}
                    <span className="mono muted break" style={{ fontSize: "var(--fs-2xs)" }}>
                      {it.expected_document ?? "any document"}
                      {it.expected_page ? ` · p.${it.expected_page}` : ""}
                      {it.tag ? ` · ${it.tag}` : ""} · {it.source}
                    </span>
                  </div>
                  <Button size="sm" variant="ghost" iconOnly icon={<Trash2 aria-hidden="true" />} onClick={() => del.mutate(it.id)}>
                    Delete question {i + 1}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel variant="shadow" className="eval__run" aria-labelledby="run-title">
          <PanelHead id="run-title" title="Run a benchmark" eyebrow="Configure" />
          <div className="panel__body stack" style={{ gap: "var(--s-5)" }}>
            <fieldset className="fieldset">
              <legend className="field__label">Retrieval configurations</legend>
              <div className="preset-grid">
                {Object.entries(PRESET_INFO).map(([key, info]) => (
                  <label key={key} className="preset" data-checked={presets.includes(key) || undefined}>
                    <input type="checkbox" checked={presets.includes(key)} onChange={() => setPresets(toggle(presets, key))} />
                    <span className="mono preset__name">{key}</span>
                    <span className="preset__info">{info}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset className="fieldset">
              <legend className="field__label">Compare chunking strategies (optional)</legend>
              <div className="row row--wrap" style={{ gap: 6 }}>
                {CHUNKING.map((c) => (
                  <label key={c} className="chip-check">
                    <input type="checkbox" checked={chunking.includes(c)} onChange={() => setChunking(toggle(chunking, c))} />
                    <span className="mono">{c}</span>
                  </label>
                ))}
              </div>
              <span className="field__hint">Each strategy re-ingests every document into a temporary index, which costs model calls.</span>
            </fieldset>
            {chunking.includes("proposition") && (
              <Alert tone="warn" title="Proposition chunking is expensive">It makes roughly two model calls per sentence.</Alert>
            )}
            <div className="row row--wrap">
              <span className="mono muted" style={{ fontSize: "var(--fs-xs)" }}>
                {runCount} {runCount === 1 ? "configuration" : "configurations"} × {n} questions = {fmtInt(runCount * n)} runs
              </span>
              <span className="spacer" />
              <Button
                variant="primary"
                icon={<Play aria-hidden="true" />}
                disabled={!presets.length || n === 0 || !!active}
                loading={start.isPending}
                onClick={() => start.mutate()}
              >
                Run evaluation
              </Button>
            </div>
            {active && (
              <div className="stack" style={{ gap: 6 }} role="status">
                <div className="row">
                  <StatusBadge status={active.status === "queued" ? "queued" : "running"} kind="job" />
                  <span className="mono muted truncate" style={{ fontSize: "var(--fs-xs)" }}>{job.data?.message ?? "Starting…"}</span>
                </div>
                <Progress value={job.data?.progress ?? 0} indeterminate={!job.data} label="Evaluation progress" />
              </div>
            )}
          </div>
        </Panel>
      </div>

      <section aria-labelledby="results-title" style={{ marginTop: "var(--s-6)" }}>
        <div className="row row--wrap" style={{ marginBottom: "var(--s-3)" }}>
          <h2 id="results-title" className="section-title" style={{ margin: 0 }}>
            Results
          </h2>
          <span className="spacer" />
          {(runs.data ?? []).filter((r) => r.status !== "queued" && r.status !== "running").length > 1 && (
            <>
              <label className="sr-only" htmlFor="run-pick">
                Choose a run
              </label>
              <select id="run-pick" className="select" style={{ width: "auto" }} value={shown?.id ?? ""} onChange={(e) => setSelectedRun(e.target.value)}>
                {(runs.data ?? [])
                  .filter((r) => r.status !== "queued" && r.status !== "running")
                  .map((r) => (
                    <option key={r.id} value={r.id}>
                      {fmtDateTime(r.created_at)} · {r.configs.length} configs · {r.status}
                    </option>
                  ))}
              </select>
            </>
          )}
        </div>
        {runs.isLoading ? (
          <SkeletonRows rows={3} height={60} />
        ) : !shown ? (
          <EmptyState icon={<FlaskConical />} title="No results yet">
            Run an evaluation to compare configurations side by side. Results are saved so you can track changes over time.
          </EmptyState>
        ) : shown.status === "failed" || shown.status === "cancelled" ? (
          <Alert tone={shown.status === "failed" ? "err" : "warn"} title={`Run ${shown.status}`}>
            {shown.error ?? "No details."}
          </Alert>
        ) : (
          <Results run={shown} />
        )}
      </section>

      <AddItemModal cid={current.id} open={adding} onClose={() => setAdding(false)} />
      <ImportModal cid={current.id} open={importing} onClose={() => setImporting(false)} />
    </>
  );
}

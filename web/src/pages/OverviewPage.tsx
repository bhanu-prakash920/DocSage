import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, FileText, Image as ImageIcon, MessageSquareText, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChartFrame, ColumnChart, CompositionBar } from "../components/charts";
import { ButtonLink } from "../components/ui/Button";
import { Alert, Badge, EmptyState, ErrorAlert, PageHeader, Panel, PanelHead, Segmented, Skeleton, SkeletonRows, Stat, StatusBadge } from "../components/ui/primitives";
import { api } from "../lib/api";
import { useApp } from "../lib/app";
import { fmtCompact, fmtCost, fmtInt, fmtMs, fmtPct, fmtRelative } from "../lib/format";
import { useDocumentTitle } from "../lib/hooks";
import type { Stats } from "../lib/types";

function lastDays(daily: Stats["daily"], n = 14) {
  const byDay = new Map(daily.map((d) => [d.day, d]));
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const date = new Date();
    date.setUTCDate(date.getUTCDate() - i);
    const key = date.toISOString().slice(0, 10);
    const row = byDay.get(key);
    out.push({
      key,
      label: date.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" }),
      value: row?.queries ?? 0,
      detail: row ? `${fmtCost(row.cost)} · avg ${fmtMs(row.avg_latency)}` : "no questions",
    });
  }
  return out;
}

export function OverviewPage() {
  const { current, collectionsLoading } = useApp();
  const [scope, setScope] = useState<"collection" | "all">("collection");
  useDocumentTitle("Overview");
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const collectionStats = useQuery({
    queryKey: ["stats", current?.id],
    queryFn: () => api.stats(current!.id),
    enabled: !!current && scope === "collection",
    refetchInterval: 15_000,
  });
  const globalStats = useQuery({ queryKey: ["overview"], queryFn: api.overview, enabled: scope === "all", refetchInterval: 15_000 });
  const q = scope === "collection" ? collectionStats : globalStats;
  const stats = q.data;
  const days = useMemo(() => lastDays(stats?.daily ?? []), [stats]);
  const status = settings.data?.status;

  const header = (
    <PageHeader
      index="03"
      title="Overview"
      meta={<span>{scope === "collection" ? current?.name ?? "No collection" : "All collections"}</span>}
      actions={
        <Segmented
          label="Scope"
          value={scope}
          onChange={setScope}
          options={[
            { value: "collection", label: "This collection" },
            { value: "all", label: "All collections" },
          ]}
        />
      }
    />
  );

  if (collectionsLoading) return <SkeletonRows rows={5} height={64} />;
  if (!current && scope === "collection") {
    return (
      <>
        {header}
        <EmptyState icon={<FileText />} title="No collection selected" actions={<ButtonLink to="/library" variant="primary">Open library</ButtonLink>}>
          Create a collection and add documents to see activity here.
        </EmptyState>
      </>
    );
  }

  return (
    <>
      {header}
      {q.error && <ErrorAlert error={q.error} title="Could not load statistics" retry={() => q.refetch()} />}
      <div className="dash">
        {/* PRIMARY: is the knowledge base ready, and what is in it */}
        <Panel variant="shadow" className="dash__hero" aria-labelledby="kb-title">
          <PanelHead id="kb-title" title="Knowledge base" eyebrow="Primary" />
          <div className="panel__body stack" style={{ gap: "var(--s-5)" }}>
            {!stats ? (
              <div className="stack">
                <Skeleton height={72} width={180} />
                <Skeleton height={22} />
              </div>
            ) : (
              <>
                <div className="dash__hero-figures">
                  <div className="stack" style={{ gap: 4 }}>
                    <span className="hero-figure">{fmtInt(stats.documents.ready)}</span>
                    <span className="secondary">
                      documents ready to answer from
                      {stats.documents.total > stats.documents.ready && <span className="muted"> of {fmtInt(stats.documents.total)}</span>}
                    </span>
                  </div>
                  <div className="dash__hero-side">
                    <Stat label="Pages, slides and sections" value={fmtCompact(stats.documents.pages)} />
                    <Stat label="Chunks indexed" value={fmtCompact(stats.chunks.total)} />
                  </div>
                </div>
                <div className="stack" style={{ gap: 8 }}>
                  <span className="eyebrow">What the index is made of</span>
                  <CompositionBar
                    parts={[
                      { key: "text", label: "Text passages", value: stats.chunks.text, color: "var(--chart-text)", icon: <FileText aria-hidden="true" /> },
                      { key: "table", label: "Tables", value: stats.chunks.table, color: "var(--chart-table)", icon: <Table2 aria-hidden="true" /> },
                      { key: "image", label: "Figures", value: stats.chunks.image, color: "var(--chart-image)", icon: <ImageIcon aria-hidden="true" /> },
                    ]}
                  />
                </div>
                {(stats.documents.failed > 0 || stats.documents.processing > 0 || stats.documents.staged > 0) && (
                  <div className="stack" style={{ gap: 8 }}>
                    {stats.documents.processing > 0 && (
                      <Alert tone="info" title={`${stats.documents.processing} indexing now`}>
                        <Link to="/library">Follow progress in the library</Link>
                      </Alert>
                    )}
                    {stats.documents.staged > 0 && (
                      <Alert tone="info" title={`${stats.documents.staged} staged and waiting for review`}>
                        <Link to="/library">Review the estimate and start ingestion</Link>
                      </Alert>
                    )}
                    {stats.documents.failed > 0 && (
                      <Alert tone="err" title={`${stats.documents.failed} failed to ingest`}>
                        <Link to="/library">See the errors and retry</Link>
                      </Alert>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </Panel>

        <Panel variant="glass" className="dash__system" aria-labelledby="sys-title">
          <PanelHead id="sys-title" title="Models" eyebrow="System" actions={<Link to="/settings" className="mono" style={{ fontSize: "var(--fs-xs)" }}>Configure</Link>} />
          <div className="panel__body">
            {!status ? (
              <SkeletonRows rows={4} height={22} />
            ) : (
              <dl className="kv">
                <div>
                  <dt>Answers</dt>
                  <dd className="mono break">
                    {status.llm_provider}:{status.llm_model}
                  </dd>
                </div>
                <div>
                  <dt>Ingestion & grading</dt>
                  <dd className="mono break">{status.llm_fast_model}</dd>
                </div>
                <div>
                  <dt>Embeddings</dt>
                  <dd className="mono break">
                    {(scope === "collection" && current ? `${current.embedding_provider}:${current.embedding_model}` : `${status.embedding.provider}:${status.embedding.model}`)}
                  </dd>
                </div>
                <div>
                  <dt>Web search</dt>
                  <dd>{status.web_search_available ? <Badge tone="ok">Available</Badge> : <Badge>Off</Badge>}</dd>
                </div>
              </dl>
            )}
            {status && !status.generative && (
              <div style={{ marginTop: "var(--s-4)" }}>
                <Alert tone="warn" title="Offline mode">
                  Answers are extracted passages. Add an Anthropic, Google or OpenAI key for generated answers and figure descriptions.
                </Alert>
              </div>
            )}
            {status?.error && (
              <div style={{ marginTop: "var(--s-4)" }}>
                <Alert tone="err" title="Provider problem">{status.error}</Alert>
              </div>
            )}
          </div>
        </Panel>

        {/* SECONDARY: question activity */}
        <Panel className="dash__activity" aria-labelledby="act-title">
          <PanelHead id="act-title" title="Questions" eyebrow="Secondary · last 14 days" />
          <div className="panel__body">
            {!stats ? (
              <Skeleton height={200} />
            ) : (
              <ChartFrame
                title="Questions per day"
                subtitle={`${fmtInt(days.reduce((a, d) => a + d.value, 0))} in the last 14 days`}
                table={
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Day</th>
                        <th className="num">Questions</th>
                        <th>Spend · latency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {days.map((d) => (
                        <tr key={d.key}>
                          <td>{d.label}</td>
                          <td className="num">{d.value}</td>
                          <td className="mono muted">{d.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
              >
                <ColumnChart data={days} integer label="Questions per day, last 14 days" format={(v) => fmtInt(v)} />
              </ChartFrame>
            )}
          </div>
        </Panel>

        <Panel className="dash__kpis" aria-labelledby="kpi-title">
          <PanelHead id="kpi-title" title="Answering" eyebrow="Secondary" />
          <div className="panel__body kpi-grid">
            {!stats ? (
              <SkeletonRows rows={3} height={48} />
            ) : (
              <>
                <Stat label="Questions answered" value={fmtInt(stats.queries.total)} sub={stats.queries.failed ? `${stats.queries.failed} failed` : "no failures"} />
                <Stat label="Median latency" value={fmtMs(stats.queries.p50_latency_ms)} sub={`p95 ${fmtMs(stats.queries.p95_latency_ms)}`} />
                <Stat label="Answer spend" value={fmtCost(stats.queries.cost)} sub={stats.queries.total ? `${fmtCost(stats.queries.cost / stats.queries.total)} per question` : "–"} />
                <Stat label="Used web search" value={fmtPct(stats.queries.total ? stats.queries.web / stats.queries.total : null)} sub={`${fmtInt(stats.queries.web)} questions`} />
              </>
            )}
          </div>
        </Panel>

        {/* SUPPORTING */}
        <Panel className="dash__recent" aria-labelledby="recent-title">
          <PanelHead id="recent-title" title="Recent questions" eyebrow="Supporting" actions={<Link to="/" className="mono" style={{ fontSize: "var(--fs-xs)" }}>Ask</Link>} />
          {!stats ? (
            <div className="panel__body"><SkeletonRows rows={4} height={36} /></div>
          ) : (stats.recent_queries ?? []).length === 0 ? (
            <div className="panel__body">
              <p className="muted">{scope === "all" ? "Switch to a collection to see its questions." : "No questions yet."}</p>
            </div>
          ) : (
            <ul className="feed">
              {(stats.recent_queries ?? []).map((r) => (
                <li key={r.id}>
                  <Link to={r.conversation_id ? `/?c=${r.conversation_id}` : "/"} className="feed__item">
                    <MessageSquareText aria-hidden="true" />
                    <span className="truncate" style={{ flex: 1 }}>{r.question}</span>
                    {r.status === "failed" && <Badge tone="err" icon={<AlertTriangle aria-hidden="true" />}>Failed</Badge>}
                    <span className="mono muted feed__meta">{fmtMs(r.latency_ms)} · {fmtRelative(r.created_at)}</span>
                    <ArrowRight aria-hidden="true" className="feed__arrow" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel className="dash__jobs" aria-labelledby="jobs-title">
          <PanelHead id="jobs-title" title="Recent jobs" eyebrow="Supporting" actions={<Link to="/library" className="mono" style={{ fontSize: "var(--fs-xs)" }}>Library</Link>} />
          {!stats ? (
            <div className="panel__body"><SkeletonRows rows={4} height={36} /></div>
          ) : (stats.recent_jobs ?? []).length === 0 ? (
            <div className="panel__body"><p className="muted">No ingestion or evaluation jobs yet.</p></div>
          ) : (
            <ul className="feed">
              {(stats.recent_jobs ?? []).map((j) => (
                <li key={j.id} className="feed__item feed__item--static">
                  <StatusBadge status={j.status} kind="job" />
                  <span className="truncate" style={{ flex: 1 }}>
                    {j.kind === "eval" ? "Evaluation run" : j.filename ?? "Ingestion"}
                  </span>
                  <span className="mono muted feed__meta">{fmtRelative(j.finished_at ?? j.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
          {stats && (
            <div className="panel__foot row" style={{ justifyContent: "space-between" }}>
              <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>Ingestion spend to date</span>
              <strong className="mono">{fmtCost(stats.documents.ingest_cost, { precise: true })}</strong>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, fileUrl } from "../../lib/api";
import { fmtBytes, fmtCost, fmtDateTime, fmtInt, pageLabel } from "../../lib/format";
import type { Modality } from "../../lib/types";
import { Markdown } from "../ask/Markdown";
import { Drawer } from "../ui/Overlay";
import { Alert, ErrorAlert, ModalityBadge, SkeletonRows, StatusBadge, Tabs } from "../ui/primitives";

export function DocumentDrawer({ docId, onClose }: { docId: string | null; onClose: () => void }) {
  const [tab, setTab] = useState<"all" | Modality>("all");
  const doc = useQuery({ queryKey: ["document", docId], queryFn: () => api.document(docId!), enabled: !!docId });
  const d = doc.data;
  const unit = d?.usage?.unit;
  const chunks = useMemo(() => (d?.chunks ?? []).filter((c) => tab === "all" || c.modality === tab), [d, tab]);
  const figures = (d?.chunks ?? []).filter((c) => c.asset_path);
  return (
    <Drawer open={!!docId} onClose={onClose} title={d?.filename ?? "Document"} eyebrow={d ? d.kind.toUpperCase() : "Loading"}>
      {doc.isLoading && <SkeletonRows rows={6} />}
      {doc.error && <ErrorAlert error={doc.error} title="Could not load the document" retry={() => doc.refetch()} />}
      {d && (
        <div className="stack" style={{ gap: "var(--s-5)" }}>
          <dl className="facts">
            <div>
              <dt>Status</dt>
              <dd>
                <StatusBadge status={d.status} />
              </dd>
            </div>
            <div>
              <dt>{unit === "slide" ? "Slides" : unit === "section" ? "Sections" : "Pages"}</dt>
              <dd className="mono">{fmtInt(d.pages)}</dd>
            </div>
            <div>
              <dt>Chunks</dt>
              <dd className="mono">
                {d.n_text} text · {d.n_table} tables · {d.n_image} figures
              </dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd className="mono">{fmtBytes(d.size_bytes)}</dd>
            </div>
            <div>
              <dt>Ingestion cost</dt>
              <dd className="mono">
                {fmtCost(d.cost_usd, { precise: true })} · {fmtInt(d.usage?.calls ?? 0)} calls
              </dd>
            </div>
            <div>
              <dt>Processed</dt>
              <dd className="mono">
                {d.duration_s != null ? `${d.duration_s}s · ` : ""}
                {fmtDateTime(d.updated_at)}
              </dd>
            </div>
            <div>
              <dt>Pipeline</dt>
              <dd className="mono break">
                {d.parser} · {d.chunking}
                {d.usage?.llm ? ` · ${d.usage.llm}` : ""}
              </dd>
            </div>
            <div>
              <dt>Embeddings</dt>
              <dd className="mono break">{d.usage?.embedding ?? "–"}</dd>
            </div>
          </dl>
          {d.error && <Alert tone="err" title="Ingestion failed">{d.error}</Alert>}
          {(d.usage?.warnings ?? []).map((w) => (
            <Alert key={w} tone="warn">
              {w}
            </Alert>
          ))}
          {figures.length > 0 && (
            <section className="stack" style={{ gap: 8 }}>
              <h3 className="eyebrow">Extracted figures and tables</h3>
              <div className="gallery">
                {figures.map((c) => (
                  <a key={c.id} href={fileUrl(c.asset_path!)} target="_blank" rel="noreferrer noopener" className="gallery__item">
                    <img src={fileUrl(c.asset_path!)} alt={c.title || `${c.modality} on ${pageLabel(c.page, unit)}`} loading="lazy" />
                    <span className="mono">
                      {c.modality === "image" ? "Figure" : "Table"} · {pageLabel(c.page, unit)}
                    </span>
                  </a>
                ))}
              </div>
            </section>
          )}
          <section className="stack" style={{ gap: 8 }}>
            <Tabs
              label="Chunk type"
              value={tab}
              onChange={setTab}
              tabs={[
                { value: "all", label: "All chunks", count: d.chunks.length },
                { value: "text", label: "Text", count: d.n_text },
                { value: "table", label: "Tables", count: d.n_table },
                { value: "image", label: "Figures", count: d.n_image },
              ]}
            />
            {chunks.length === 0 ? (
              <p className="muted">No chunks of this type.</p>
            ) : (
              <ol className="chunks">
                {chunks.map((c) => (
                  <li key={c.id} className="chunk">
                    <div className="row row--wrap" style={{ gap: 6 }}>
                      <ModalityBadge modality={c.modality} />
                      <span className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>
                        {pageLabel(c.page, unit)} · #{c.ordinal + 1}
                      </span>
                      {c.title && <strong className="truncate" style={{ fontSize: "var(--fs-sm)" }}>{c.title}</strong>}
                    </div>
                    {c.modality === "image" ? <p className="chunk__text">{c.text}</p> : <Markdown text={c.text} />}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </Drawer>
  );
}

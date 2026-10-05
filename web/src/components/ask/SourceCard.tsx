import { ChevronDown, ExternalLink, Quote } from "lucide-react";
import { forwardRef, useState } from "react";
import { fileUrl } from "../../lib/api";
import { plainText } from "../../lib/format";
import type { Source } from "../../lib/types";
import { Badge, ModalityBadge } from "../ui/primitives";
import { Markdown } from "./Markdown";

function signalText(s: Source): string {
  const sig = s.signals as Record<string, number | boolean | null | undefined>;
  const parts: string[] = [];
  if (sig.dense_rank) parts.push(`dense #${sig.dense_rank}`);
  if (sig.bm25_rank) parts.push(`bm25 #${sig.bm25_rank}`);
  if (sig.graph) parts.push("graph");
  if (sig.rerank != null) parts.push(`rerank ${Number(sig.rerank).toFixed(2)}`);
  if (sig.expanded) parts.push("page context");
  return parts.join(" · ");
}

export const SourceCard = forwardRef<HTMLElement, { source: Source; active?: boolean; defaultOpen?: boolean }>(function SourceCard(
  { source, active, defaultOpen = false },
  ref,
) {
  const [open, setOpen] = useState(defaultOpen);
  const isWeb = source.modality === "web";
  const location = isWeb ? source.url : `${source.filename}${source.page ? ` · p.${source.page}` : ""}`;
  const signals = signalText(source);
  return (
    <article ref={ref} className="source" data-active={active || undefined} data-cited={source.cited || undefined} tabIndex={-1}>
      <header className="source__head">
        <span className="source__n mono" aria-label={`Source ${source.n}`}>
          {source.n}
        </span>
        <div className="stack" style={{ gap: 4, minWidth: 0, flex: 1 }}>
          <div className="row row--wrap" style={{ gap: 6 }}>
            <ModalityBadge modality={source.modality} />
            {source.cited && (
              <Badge tone="accent" icon={<Quote aria-hidden="true" />}>
                Cited
              </Badge>
            )}
          </div>
          {isWeb && source.url ? (
            <a className="source__loc break" href={source.url} target="_blank" rel="noreferrer noopener">
              {source.title || source.url} <ExternalLink size={12} aria-hidden="true" style={{ display: "inline" }} />
            </a>
          ) : (
            <span className="source__loc break" title={location ?? undefined}>
              {location}
            </span>
          )}
        </div>
      </header>
      {source.asset_path && (
        <a className="source__thumb" href={fileUrl(source.asset_path)} target="_blank" rel="noreferrer noopener">
          <img src={fileUrl(source.asset_path)} alt={source.title || `${source.modality} from ${source.filename}`} loading="lazy" />
        </a>
      )}
      {source.title && !isWeb && <p className="source__title">{source.title}</p>}
      <div className={`source__text ${open ? "is-open" : ""}`}>
        {open && !isWeb ? <Markdown text={source.text} /> : <p>{isWeb ? source.text : plainText(source.text)}</p>}
      </div>
      <footer className="source__foot">
        {signals && <span className="mono muted source__signals">{signals}</span>}
        <button type="button" className="btn btn--ghost btn--sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Less" : "More"}
          <ChevronDown aria-hidden="true" style={{ transform: open ? "rotate(180deg)" : undefined }} />
        </button>
      </footer>
    </article>
  );
});

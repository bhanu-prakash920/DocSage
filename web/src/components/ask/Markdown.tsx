import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { linkCitations } from "../../lib/citations";

export const Markdown = memo(function Markdown({
  text,
  citations,
  onCite,
  activeCitation,
}: {
  text: string;
  citations?: Set<number>;
  onCite?: (n: number) => void;
  activeCitation?: number | null;
}) {
  const source = citations ? linkCitations(text, citations) : text;
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            const m = href?.match(/^#cite-(\d+)$/);
            if (m) {
              const n = Number(m[1]);
              return (
                <button
                  type="button"
                  className="cite"
                  data-active={activeCitation === n || undefined}
                  onClick={() => onCite?.(n)}
                  aria-label={`Show source ${n}`}
                >
                  {n}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
          table({ children }) {
            return (
              <div className="md-table">
                <table>{children}</table>
              </div>
            );
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
});

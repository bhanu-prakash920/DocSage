import { ArrowUp, Filter, Globe, Square, Sparkles, Zap } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { DocumentRow, QueryOptions } from "../../lib/types";
import { Button } from "../ui/Button";
import { Popover } from "../ui/Overlay";
import { MODALITY_META, Segmented, Switch } from "../ui/primitives";

export interface ComposerOptions extends QueryOptions {
  mode: "simple" | "agentic";
}

export function Composer({
  busy,
  onSubmit,
  onStop,
  options,
  setOptions,
  documents,
  webAvailable,
  disabled,
  autoFocus,
}: {
  busy: boolean;
  onSubmit: (q: string) => void;
  onStop: () => void;
  options: ComposerOptions;
  setOptions: (o: ComposerOptions) => void;
  documents: DocumentRow[];
  webAvailable: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const [value, setValue] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);
  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        e.preventDefault();
        ref.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const submit = () => {
    const q = value.trim();
    if (!q || busy || disabled) return;
    onSubmit(q);
    setValue("");
  };
  const filterCount = (options.modalities?.length ?? 0) + (options.document_ids?.length ?? 0) + (options.rerank && options.rerank !== "none" ? 1 : 0);
  const toggle = <T,>(list: T[] | undefined, item: T) => {
    const set = new Set(list ?? []);
    if (set.has(item)) set.delete(item);
    else set.add(item);
    return [...set];
  };

  return (
    <form
      className="composer glass glass--strong"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label htmlFor={id} className="sr-only">
        Ask a question about your documents
      </label>
      <textarea
        id={id}
        ref={ref}
        className="composer__input"
        rows={1}
        value={value}
        disabled={disabled}
        placeholder={disabled ? "Index documents to start asking" : "Ask about your documents…"}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer__bar">
        <Segmented
          label="Answer mode"
          value={options.mode}
          onChange={(mode) => setOptions({ ...options, mode })}
          options={[
            { value: "agentic", label: "Agentic", icon: <Sparkles aria-hidden="true" />, title: "Checks evidence, refines the search and can use the web" },
            { value: "simple", label: "Fast", icon: <Zap aria-hidden="true" />, title: "Single search, then answer" },
          ]}
        />
        <div style={{ position: "relative" }}>
          <Button
            size="sm"
            variant={filterCount ? "secondary" : "ghost"}
            icon={<Filter aria-hidden="true" />}
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((v) => !v)}
          >
            Filters{filterCount ? ` · ${filterCount}` : ""}
          </Button>
          <Popover open={filtersOpen} onClose={() => setFiltersOpen(false)} label="Search filters" width={320}>
            <div className="stack" style={{ padding: 8, gap: 14 }}>
              <fieldset className="fieldset">
                <legend className="eyebrow">Content types</legend>
                <div className="row row--wrap" style={{ gap: 6 }}>
                  {(["text", "table", "image"] as const).map((m) => {
                    const meta = MODALITY_META[m];
                    const Icon = meta.icon;
                    return (
                      <label key={m} className="chip-check">
                        <input
                          type="checkbox"
                          checked={options.modalities?.includes(m) ?? false}
                          onChange={() => setOptions({ ...options, modalities: toggle(options.modalities, m) })}
                        />
                        <span>
                          <Icon aria-hidden="true" />
                          {meta.label}
                        </span>
                      </label>
                    );
                  })}
                </div>
                <span className="field__hint">None selected searches everything.</span>
              </fieldset>
              <fieldset className="fieldset">
                <legend className="eyebrow">Documents</legend>
                <div className="doc-filter">
                  {documents.length === 0 && <span className="muted">No indexed documents.</span>}
                  {documents.map((d) => (
                    <label key={d.id} className="doc-filter__item">
                      <input
                        type="checkbox"
                        checked={options.document_ids?.includes(d.id) ?? false}
                        onChange={() => setOptions({ ...options, document_ids: toggle(options.document_ids, d.id) })}
                      />
                      <span className="truncate">{d.filename}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <Switch
                checked={options.rerank === "llm"}
                onChange={(v) => setOptions({ ...options, rerank: v ? "llm" : "none" })}
                label="Re-rank with the model"
                description="More precise, one extra model call."
              />
              {filterCount > 0 && (
                <Button size="sm" variant="ghost" onClick={() => setOptions({ ...options, modalities: [], document_ids: [], rerank: "none" })}>
                  Clear filters
                </Button>
              )}
            </div>
          </Popover>
        </div>
        <label className={`web-toggle ${!webAvailable ? "is-disabled" : ""}`} title={webAvailable ? "Allow web search when documents fall short" : "Add TAVILY_API_KEY to enable web search"}>
          <input
            type="checkbox"
            checked={!!options.web_search && webAvailable}
            disabled={!webAvailable || options.mode !== "agentic"}
            onChange={(e) => setOptions({ ...options, web_search: e.target.checked })}
          />
          <Globe aria-hidden="true" />
          <span>Web</span>
        </label>
        <span className="spacer" />
        <span className="composer__hint mono muted">
          <kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> newline
        </span>
        {busy ? (
          <Button variant="secondary" icon={<Square aria-hidden="true" />} onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" icon={<ArrowUp aria-hidden="true" />} disabled={!value.trim() || disabled}>
            Ask
          </Button>
        )}
      </div>
    </form>
  );
}

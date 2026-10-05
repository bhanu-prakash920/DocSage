import { AlertTriangle, Check, Copy, Globe, RotateCcw, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { citedNumbers } from "../../lib/citations";
import { fmtCost, fmtMs } from "../../lib/format";
import type { Source, TraceStep, Usage, Verdict } from "../../lib/types";
import { Button } from "../ui/Button";
import { Badge, ErrorAlert, Tooltip } from "../ui/primitives";
import { Markdown } from "./Markdown";
import { TraceSteps } from "./TraceSteps";

export interface TurnState {
  key: string;
  question: string;
  status: "streaming" | "done" | "error";
  steps: TraceStep[];
  sources: Source[];
  answer: string;
  standalone?: string | null;
  verdict?: Verdict | null;
  usage?: Usage | null;
  latencyMs?: number | null;
  usedWeb?: boolean;
  error?: { message: string; hint?: string | null } | null;
  mode?: string;
}

export function Turn({
  turn,
  index,
  active,
  activeCitation,
  onCite,
  onSelect,
  onRetry,
}: {
  turn: TurnState;
  index: number;
  active: boolean;
  activeCitation: number | null;
  onCite: (n: number) => void;
  onSelect: () => void;
  onRetry: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const valid = new Set(turn.sources.map((s) => s.n));
  const cited = citedNumbers(turn.answer).filter((n) => valid.has(n));
  const live = turn.status === "streaming";
  const writing = live && turn.steps.some((s) => s.step === "generate" && s.status === "running");
  return (
    <article className="turn" data-active={active || undefined} aria-busy={live} onFocus={onSelect} onMouseDown={onSelect}>
      <header className="turn__q">
        <span className="turn__index mono">Q.{String(index + 1).padStart(2, "0")}</span>
        <h2 className="turn__question">{turn.question}</h2>
      </header>
      {turn.standalone && turn.standalone !== turn.question && (
        <p className="turn__rewrite mono">
          <span className="muted">searched for</span> {turn.standalone}
        </p>
      )}
      <TraceSteps steps={turn.steps} live={live} />
      {turn.error ? (
        <ErrorAlert error={turn.error} title="The question could not be answered" retry={onRetry} />
      ) : (
        <div className="turn__answer" aria-live={live ? "polite" : undefined}>
          {turn.answer ? (
            <Markdown text={turn.answer} citations={valid} onCite={onCite} activeCitation={active ? activeCitation : null} />
          ) : (
            live && (
              <div className="stack" style={{ gap: 8 }} aria-hidden="true">
                <span className="skeleton" style={{ height: 14, width: "92%" }} />
                <span className="skeleton" style={{ height: 14, width: "78%" }} />
                <span className="skeleton" style={{ height: 14, width: "64%" }} />
              </div>
            )
          )}
          {writing && <span className="caret" aria-hidden="true" />}
        </div>
      )}
      {turn.status === "done" && (
        <footer className="turn__meta">
          {turn.verdict &&
            (turn.verdict.sufficient ? (
              <Badge tone="ok" icon={<ShieldCheck aria-hidden="true" />}>
                Evidence sufficient
              </Badge>
            ) : (
              <Tooltip content={turn.verdict.missing ? `Missing: ${turn.verdict.missing}` : "The sources may not fully answer this."}>
                <Badge tone="warn" icon={<AlertTriangle aria-hidden="true" />}>
                  Partial evidence
                </Badge>
              </Tooltip>
            ))}
          {turn.usedWeb && (
            <Badge tone="info" icon={<Globe aria-hidden="true" />}>
              Web search used
            </Badge>
          )}
          <span className="mono muted turn__stats">
            {cited.length} cited · {fmtMs(turn.latencyMs)} · {turn.usage?.calls ?? 0} model calls · {fmtCost(turn.usage?.cost_usd)}
          </span>
          <span className="spacer" />
          <Button
            size="sm"
            variant="ghost"
            icon={copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(turn.answer);
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1500);
              } catch {
                /* clipboard blocked */
              }
            }}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
          <Button size="sm" variant="ghost" icon={<RotateCcw aria-hidden="true" />} onClick={onRetry}>
            Ask again
          </Button>
        </footer>
      )}
    </article>
  );
}

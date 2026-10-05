import { Check, CircleAlert } from "lucide-react";
import type { TraceStep } from "../../lib/types";
import { fmtMs } from "../../lib/format";

const ORDER: TraceStep["step"][] = ["rewrite", "retrieve", "grade", "web", "generate"];

export function TraceSteps({ steps, live }: { steps: TraceStep[]; live: boolean }) {
  if (!steps.length && !live) return null;
  // Collapse repeated attempts into the latest status per step, but keep the attempt count.
  const attempts: Record<string, number> = {};
  const latest = new Map<string, TraceStep>();
  for (const s of steps) {
    if (s.status === "running") attempts[s.step] = (attempts[s.step] ?? 0) + 1;
    latest.set(s.step, s);
  }
  const shown = ORDER.filter((k) => latest.has(k));
  return (
    <ol className="trace" aria-label="Agent steps">
      {shown.map((key, i) => {
        const s = latest.get(key)!;
        const n = attempts[key] ?? 1;
        return (
          <li key={key} className="trace__step" data-status={s.status}>
            <span className="trace__icon" aria-hidden="true">
              {s.status === "running" ? <span className="spinner" /> : s.status === "error" ? <CircleAlert /> : <Check />}
            </span>
            <span className="trace__label">
              <span className="mono trace__i">{String(i + 1).padStart(2, "0")}</span>
              {s.label}
              {n > 1 && <span className="mono muted"> ×{n}</span>}
            </span>
            <span className="mono trace__ms">{s.status === "running" ? "…" : fmtMs(s.ms)}</span>
            <span className="sr-only">{s.status === "running" ? "in progress" : s.status === "error" ? "failed" : "done"}</span>
          </li>
        );
      })}
    </ol>
  );
}

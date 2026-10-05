import { Table2 } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/* Shared rules (dataviz method): one baseline, bars <= 24px thick with a 4px rounded data end,
   2px surface gaps, hairline recessive grid, text in ink tokens (never the series color),
   hover + keyboard tooltips, and a table view for every chart. */

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function niceTicks(max: number, count = 4, integer = false): number[] {
  if (max <= 0) return [0, 1];
  const raw = integer ? Math.max(1, max / count) : max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const steps = integer ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10];
  const step = steps.map((m) => Math.max(integer ? 1 : 0, m * mag)).find((s) => s >= raw) ?? raw;
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

function columnPath(x: number, y: number, w: number, base: number, r = 4): string {
  const h = base - y;
  const rr = Math.min(r, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${base} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${base} Z`;
}

function rowPath(x: number, y: number, w: number, h: number, r = 4): string {
  const rr = Math.min(r, h / 2, w);
  if (w <= 0) return "";
  return `M${x},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h - rr} Q${x + w},${y + h} ${x + w - rr},${y + h} H${x} Z`;
}

export function ChartFrame({
  title,
  subtitle,
  table,
  children,
  legend,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  table: ReactNode;
  children: ReactNode;
  legend?: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  const id = useId();
  return (
    <figure className="chart" aria-labelledby={id}>
      <figcaption className="chart__head">
        <div className="stack" style={{ gap: 2, minWidth: 0 }}>
          <span className="chart__title" id={id}>
            {title}
          </span>
          {subtitle && <span className="chart__sub">{subtitle}</span>}
        </div>
        <button type="button" className="btn btn--ghost btn--sm" aria-pressed={showTable} onClick={() => setShowTable((v) => !v)}>
          <Table2 aria-hidden="true" />
          {showTable ? "Chart" : "Table"}
        </button>
      </figcaption>
      {legend}
      {showTable ? <div className="table-wrap chart__table">{table}</div> : children}
    </figure>
  );
}

/* ---------------- Column chart (single series over time) ---------------- */
export function ColumnChart({
  data,
  color = "var(--chart-accent)",
  height = 200,
  format = (v: number) => String(v),
  label,
  integer = false,
}: {
  integer?: boolean;
  data: { key: string; label: string; value: number; detail?: string }[];
  color?: string;
  height?: number;
  format?: (v: number) => string;
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 12, right: 8, bottom: 26, left: 40 };
  const max = Math.max(0, ...data.map((d) => d.value));
  const ticks = niceTicks(max, 4, integer);
  const top = ticks[ticks.length - 1] || 1;
  const innerW = Math.max(0, width - pad.left - pad.right);
  const innerH = height - pad.top - pad.bottom;
  const slot = data.length ? innerW / data.length : 0;
  const barW = Math.max(2, Math.min(24, slot - 2));
  const base = pad.top + innerH;
  const y = (v: number) => pad.top + innerH - (v / top) * innerH;
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(innerW / 56))));
  const h = hover != null ? data[hover] : null;
  return (
    <div ref={ref} className="chart__plot" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" strokeWidth={1} />
              <text x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="chart__tick">
                {format(t)}
              </text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = pad.left + i * slot + (slot - barW) / 2;
            return (
              <g key={d.key}>
                <path d={columnPath(x, y(d.value), barW, base)} fill={color} opacity={hover == null || hover === i ? 1 : 0.45} />
                <rect
                  x={pad.left + i * slot}
                  y={pad.top}
                  width={slot}
                  height={innerH}
                  fill="transparent"
                  tabIndex={0}
                  role="button"
                  aria-label={`${d.label}: ${format(d.value)}${d.detail ? `, ${d.detail}` : ""}`}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  className="chart__hit"
                />
                {i % labelEvery === 0 && (
                  <text x={pad.left + i * slot + slot / 2} y={height - 8} textAnchor="middle" className="chart__tick">
                    {d.label}
                  </text>
                )}
              </g>
            );
          })}
          <line x1={pad.left} x2={width - pad.right} y1={base} y2={base} stroke="var(--line)" strokeWidth={1} />
        </svg>
      )}
      {h && hover != null && (
        <div
          className="chart__tooltip glass glass--strong"
          style={{ left: Math.min(Math.max(pad.left + hover * slot + slot / 2, 80), width - 80), top: Math.max(0, y(h.value) - 8) }}
          role="status"
        >
          <span className="mono muted">{h.label}</span>
          <strong>{format(h.value)}</strong>
          {h.detail && <span className="muted">{h.detail}</span>}
        </div>
      )}
    </div>
  );
}

/* ---------------- Composition (100% stacked bar) ---------------- */
export function CompositionBar({
  parts,
  height = 22,
}: {
  parts: { key: string; label: string; value: number; color: string; icon?: ReactNode }[];
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const total = parts.reduce((a, p) => a + p.value, 0);
  const visible = parts.filter((p) => p.value > 0);
  const gap = 2;
  const usable = Math.max(0, width - gap * Math.max(0, visible.length - 1));
  let x = 0;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div ref={ref} style={{ height }}>
        {width > 0 && total > 0 && (
          <svg width={width} height={height} role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}>
            {visible.map((p, i) => {
              const w = Math.max(2, (p.value / total) * usable);
              const segX = x;
              x += w + gap;
              const last = i === visible.length - 1;
              return (
                <g
                  key={p.key}
                  fill={p.color}
                  opacity={hover && hover !== p.key ? 0.45 : 1}
                  onMouseEnter={() => setHover(p.key)}
                  onMouseLeave={() => setHover(null)}
                >
                  {last ? <path d={rowPath(segX, 0, w, height)} /> : <rect x={segX} y={0} width={w} height={height} />}
                  <title>
                    {p.label}: {p.value} ({Math.round((p.value / total) * 100)}%)
                  </title>
                </g>
              );
            })}
          </svg>
        )}
        {total === 0 && <div className="chart__empty-bar" style={{ height }} />}
      </div>
      <ul className="legend" aria-label="Legend">
        {parts.map((p) => (
          <li key={p.key} onMouseEnter={() => setHover(p.key)} onMouseLeave={() => setHover(null)}>
            <span className="swatch" style={{ background: p.color }} aria-hidden="true" />
            {p.icon}
            <span>{p.label}</span>
            <strong className="mono num">{p.value.toLocaleString()}</strong>
            <span className="mono muted">{total ? Math.round((p.value / total) * 100) : 0}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------------- Ranked bars (one metric across configurations) ---------------- */
export function BarList({
  rows,
  max,
  format,
  better = "higher",
  label,
}: {
  rows: { key: string; label: string; value: number | null }[];
  max?: number;
  format: (v: number) => string;
  better?: "higher" | "lower";
  label: string;
}) {
  const values = rows.map((r) => r.value).filter((v): v is number => v != null);
  const top = max ?? Math.max(1e-9, ...values);
  const best = values.length ? (better === "higher" ? Math.max(...values) : Math.min(...values)) : null;
  return (
    <ul className="barlist" aria-label={label}>
      {rows.map((r) => {
        const isBest = r.value != null && r.value === best && new Set(values).size > 1;
        const pct = r.value == null ? 0 : Math.max(0.5, (r.value / top) * 100);
        return (
          <li key={r.key} className="barlist__row" data-best={isBest || undefined}>
            <span className="barlist__label">
              <span className="truncate">{r.label}</span>
              {isBest && <span className="badge badge--accent">Best</span>}
            </span>
            <span className="barlist__track">
              <span
                className="barlist__bar"
                style={{ width: `${pct}%`, background: isBest ? "var(--chart-accent)" : "var(--chart-neutral)" }}
              />
              <span className="barlist__value mono num">{r.value == null ? "–" : format(r.value)}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

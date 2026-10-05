import {
  AlertTriangle,
  CheckCircle2,
  CircleAlert,
  FileText,
  Globe,
  Image as ImageIcon,
  Info,
  Table2,
  X,
} from "lucide-react";
import { useId, type HTMLAttributes, type ReactNode } from "react";
import type { DocStatus, JobStatus, Modality } from "../../lib/types";

/* ---------------- Panel ---------------- */
export function Panel({
  variant,
  className = "",
  children,
  ...rest
}: HTMLAttributes<HTMLElement> & { variant?: "glass" | "flat" | "sunken" | "inverse" | "shadow"; as?: string }) {
  return (
    <section className={`panel ${variant ? `panel--${variant}` : ""} ${className}`} {...rest}>
      {children}
    </section>
  );
}

export function PanelHead({ title, eyebrow, actions, id }: { title: ReactNode; eyebrow?: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <header className="panel__head">
      <div className="stack" style={{ gap: 0, minWidth: 0 }}>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2 className="panel__title" id={id}>
          {title}
        </h2>
      </div>
      <span className="spacer" />
      {actions}
    </header>
  );
}

/* ---------------- Page header ---------------- */
export function PageHeader({
  index,
  title,
  meta,
  actions,
}: {
  index: string;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <span className="page-head__index" aria-hidden="true">
        {index}
      </span>
      <h1 className="page-head__title">{title}</h1>
      {meta ? <div className="page-head__meta">{meta}</div> : <div />}
      {actions && <div className="page-head__actions">{actions}</div>}
    </header>
  );
}

/* ---------------- Badges ---------------- */
type Tone = "neutral" | "ok" | "warn" | "err" | "info" | "accent" | "solid";
export function Badge({ tone = "neutral", icon, children, title }: { tone?: Tone; icon?: ReactNode; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone !== "neutral" ? `badge--${tone}` : ""}`} title={title}>
      {icon}
      <span className="truncate">{children}</span>
    </span>
  );
}

export const MODALITY_META: Record<Modality, { label: string; icon: typeof FileText; color: string }> = {
  text: { label: "Text", icon: FileText, color: "var(--chart-text)" },
  table: { label: "Table", icon: Table2, color: "var(--chart-table)" },
  image: { label: "Figure", icon: ImageIcon, color: "var(--chart-image)" },
  web: { label: "Web", icon: Globe, color: "var(--ink-3)" },
};

export function ModalityBadge({ modality }: { modality: Modality }) {
  const meta = MODALITY_META[modality];
  const Icon = meta.icon;
  return (
    <span className="badge">
      <span className="swatch" style={{ background: meta.color }} aria-hidden="true" />
      <Icon aria-hidden="true" />
      {meta.label}
    </span>
  );
}

const DOC_STATUS: Record<DocStatus, { tone: Tone; label: string }> = {
  staged: { tone: "info", label: "Staged" },
  queued: { tone: "neutral", label: "Queued" },
  processing: { tone: "warn", label: "Processing" },
  ready: { tone: "ok", label: "Ready" },
  failed: { tone: "err", label: "Failed" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};
const JOB_STATUS: Record<JobStatus, { tone: Tone; label: string }> = {
  queued: { tone: "neutral", label: "Queued" },
  running: { tone: "warn", label: "Running" },
  succeeded: { tone: "ok", label: "Done" },
  failed: { tone: "err", label: "Failed" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};

export function StatusBadge({ status, kind = "doc" }: { status: DocStatus | JobStatus; kind?: "doc" | "job" }) {
  const meta = kind === "doc" ? DOC_STATUS[status as DocStatus] : JOB_STATUS[status as JobStatus];
  const icon =
    meta.tone === "ok" ? (
      <CheckCircle2 aria-hidden="true" />
    ) : meta.tone === "err" ? (
      <CircleAlert aria-hidden="true" />
    ) : meta.tone === "warn" ? (
      <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} aria-hidden="true" />
    ) : null;
  return (
    <Badge tone={meta.tone} icon={icon}>
      {meta.label}
    </Badge>
  );
}

/* ---------------- Alert ---------------- */
export function Alert({
  tone = "info",
  title,
  children,
  action,
  onDismiss,
}: {
  tone?: "info" | "ok" | "warn" | "err";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
}) {
  const Icon = tone === "ok" ? CheckCircle2 : tone === "err" ? CircleAlert : tone === "warn" ? AlertTriangle : Info;
  return (
    <div className={`alert alert--${tone}`} role={tone === "err" ? "alert" : "status"}>
      <Icon aria-hidden="true" />
      <div className="stack" style={{ gap: 2, minWidth: 0 }}>
        {title && <div className="alert__title">{title}</div>}
        {children && <div className="secondary break">{children}</div>}
        {action && <div style={{ marginTop: 6 }}>{action}</div>}
      </div>
      {onDismiss ? (
        <button className="btn btn--ghost btn--sm btn--icon" onClick={onDismiss} aria-label="Dismiss">
          <X aria-hidden="true" />
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}

export function ErrorAlert({ error, title = "Something went wrong", retry }: { error: unknown; title?: string; retry?: () => void }) {
  const err = error as { message?: string; hint?: string | null };
  return (
    <Alert
      tone="err"
      title={title}
      action={
        retry && (
          <button className="btn btn--secondary btn--sm" onClick={retry}>
            Try again
          </button>
        )
      }
    >
      {err?.message ?? String(error)}
      {err?.hint && <div className="muted">{err.hint}</div>}
    </Alert>
  );
}

/* ---------------- Empty / loading ---------------- */
export function EmptyState({ icon, title, children, actions }: { icon: ReactNode; title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty__icon" aria-hidden="true">
        {icon}
      </span>
      <h3 className="empty__title">{title}</h3>
      {children && <p className="empty__text">{children}</p>}
      {actions && <div className="row row--wrap">{actions}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading", large }: { label?: string; large?: boolean }) {
  return (
    <span role="status" className="row" style={{ gap: 8 }}>
      <span className={`spinner ${large ? "spinner--lg" : ""}`} aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <span className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function SkeletonRows({ rows = 4, height = 44 }: { rows?: number; height?: number }) {
  return (
    <div className="stack" style={{ gap: 8 }} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={height} />
      ))}
    </div>
  );
}

export function Progress({ value, indeterminate, label }: { value?: number; indeterminate?: boolean; label: string }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div
      className={`progress ${indeterminate ? "progress--indeterminate" : ""}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : pct}
    >
      <div className="progress__bar" style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ---------------- Form controls ---------------- */
export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {error ? (
        <span className="field__error" role="alert">
          <CircleAlert size={14} aria-hidden="true" />
          {error}
        </span>
      ) : (
        hint && <span className="field__hint">{hint}</span>
      )}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label className="switch" htmlFor={id}>
      <input id={id} type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch__track" aria-hidden="true" />
      <span className="switch__text">
        <span style={{ fontWeight: 600, fontSize: "var(--fs-sm)" }}>{label}</span>
        {description && <span className="field__hint">{description}</span>}
      </span>
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: ReactNode; icon?: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  tabs,
  onChange,
  label,
}: {
  value: T;
  tabs: { value: T; label: ReactNode; count?: number }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          className="tab"
          aria-selected={value === t.value}
          tabIndex={value === t.value ? 0 : -1}
          onClick={() => onChange(t.value)}
          onKeyDown={(e) => {
            const i = tabs.findIndex((x) => x.value === value);
            if (e.key === "ArrowRight") onChange(tabs[(i + 1) % tabs.length].value);
            if (e.key === "ArrowLeft") onChange(tabs[(i - 1 + tabs.length) % tabs.length].value);
          }}
        >
          {t.label}
          {t.count != null && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <span className="tip" aria-describedby={id}>
      {children}
      <span className="tip__bubble" role="tooltip" id={id}>
        {content}
      </span>
    </span>
  );
}

export function Stat({ label, value, sub }: { label: ReactNode; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <span className="stat__label">{label}</span>
      <span className="stat__value">{value}</span>
      {sub && <span className="stat__sub">{sub}</span>}
    </div>
  );
}

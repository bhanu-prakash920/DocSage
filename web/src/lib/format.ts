const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en");

export const fmtInt = (n: number | null | undefined) => (n == null ? "–" : integer.format(Math.round(n)));
export const fmtCompact = (n: number | null | undefined) => (n == null ? "–" : n < 10000 ? integer.format(n) : compact.format(n));

export function fmtCost(usd: number | null | undefined, { precise = false } = {}): string {
  if (usd == null) return "–";
  if (usd === 0) return "$0";
  if (usd < 0.01 && !precise) return "<$0.01";
  return `$${usd.toFixed(usd < 1 || precise ? (usd < 0.1 ? 4 : 3) : 2)}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtPct(v: number | null | undefined): string {
  return v == null ? "–" : `${Math.round(v * 100)}%`;
}

export function fmtScore(v: number | null | undefined): string {
  return v == null ? "–" : v.toFixed(2);
}

export function parseDate(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "–";
  const diff = (Date.now() - parseDate(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} d ago`;
  return parseDate(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return parseDate(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function pageLabel(page: number | null | undefined, unit?: string | null): string {
  if (page == null) return "";
  const prefix = unit === "slide" ? "Slide" : unit === "section" ? "§" : "p.";
  return unit === "section" ? `${prefix}${page}` : `${prefix}${unit === "slide" ? " " : ""}${page}`;
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

/** Strip markdown syntax for compact previews (headings, emphasis, tables, links). */
export function plainText(md: string): string {
  return md
    .split("\n")
    .filter((line) => !/^\s*\|?\s*:?-{2,}/.test(line))
    .map((line) =>
      line
        .replace(/^\s{0,3}#{1,6}\s+/, "")
        .replace(/^\s*[-*+]\s+/, "• ")
        .replace(/^\s*\|(.*)\|\s*$/, (_, row: string) => row.split("|").map((c) => c.trim()).filter(Boolean).join(" · ")),
    )
    .join("\n")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/(^|\s)[*_]([^*_\n]+)[*_](?=\s|$|[.,;:])/g, "$1$2")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

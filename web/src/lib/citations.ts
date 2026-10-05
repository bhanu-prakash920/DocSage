/** Split answer markdown into text and citation tokens so [n] can render as interactive chips. */
export const CITATION_RE = /\[(\d{1,2})\]/g;

export function citedNumbers(answer: string): number[] {
  const out = new Set<number>();
  for (const m of answer.matchAll(CITATION_RE)) out.add(Number(m[1]));
  return [...out].sort((a, b) => a - b);
}

/**
 * Replace "[n]" with a markdown link "[n](#cite-n)" that the renderer turns into a chip.
 * Leaves code spans and existing links untouched.
 */
export function linkCitations(answer: string, valid: Set<number>): string {
  return answer
    .split(/(`[^`]*`)/g)
    .map((part) =>
      part.startsWith("`")
        ? part
        : part.replace(/\[(\d{1,2})\](?!\()/g, (whole, n) => (valid.has(Number(n)) ? `[${n}](#cite-${n})` : whole)),
    )
    .join("");
}

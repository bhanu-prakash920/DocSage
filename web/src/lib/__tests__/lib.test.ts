import { describe, expect, it } from "vitest";
import { niceTicks } from "../../components/charts";
import { citedNumbers, linkCitations } from "../citations";
import { fmtCost, fmtMs, pageLabel, plainText, pluralize } from "../format";

describe("citations", () => {
  it("finds cited source numbers", () => {
    expect(citedNumbers("Revenue rose [2] and costs fell [1][2].")).toEqual([1, 2]);
  });
  it("links only valid citations and leaves code alone", () => {
    const out = linkCitations("See [1] and [9] but not `arr[1]`.", new Set([1]));
    expect(out).toBe("See [1](#cite-1) and [9] but not `arr[1]`.");
  });
});

describe("format", () => {
  it("formats costs, durations and plurals", () => {
    expect(fmtCost(0)).toBe("$0");
    expect(fmtCost(0.004)).toBe("<$0.01");
    expect(fmtCost(0.004, { precise: true })).toBe("$0.0040");
    expect(fmtMs(850)).toBe("850 ms");
    expect(fmtMs(2400)).toBe("2.4 s");
    expect(pluralize(1, "section")).toBe("1 section");
    expect(pluralize(3, "slide")).toBe("3 slides");
    expect(pageLabel(4, "slide")).toBe("Slide 4");
  });
  it("strips markdown for previews", () => {
    const md = "## **Segment results**\n\n| A | B |\n|---|---|\n| 1 | 2 |\n- item";
    expect(plainText(md)).toBe("Segment results\n\nA · B\n1 · 2\n• item");
  });
});

describe("chart ticks", () => {
  it("never repeats integer ticks for small counts", () => {
    const ticks = niceTicks(2, 4, true);
    expect(new Set(ticks).size).toBe(ticks.length);
    expect(ticks.every(Number.isInteger)).toBe(true);
  });
  it("produces clean decimal ticks", () => {
    expect(niceTicks(1, 4)).toEqual([0, 0.25, 0.5, 0.75, 1]);
  });
});

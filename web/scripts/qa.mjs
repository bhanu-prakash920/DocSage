// Visual + functional QA walkthrough. Usage: node qa.mjs <baseUrl> <outDir> [phase]
import { chromium } from "@playwright/test";
const [base, out, phase = "all"] = process.argv.slice(2);
const issues = [];
const browser = await chromium.launch();

async function page(width, height, theme = "light") {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: theme, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  p.on("console", (m) => m.type() === "error" && issues.push(`[console ${width}] ${m.text()}`));
  p.on("pageerror", (e) => issues.push(`[pageerror ${width}] ${e.message}`));
  return p;
}
async function overflow(p, label) {
  const o = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (o > 1) issues.push(`[overflow] ${label}: ${o}px horizontal scroll`);
}
async function shot(p, name, full = false) {
  await p.waitForTimeout(350);
  await p.screenshot({ path: `${out}/${name}.png`, fullPage: full });
}
async function waitReady() {
  for (let i = 0; i < 120; i++) {
    const cols = await (await fetch(`${base}/api/collections`)).json();
    if (cols.length) {
      const docs = await (await fetch(`${base}/api/collections/${cols[0].id}/documents`)).json();
      if (docs.length && docs.every((d) => d.status === "ready" || d.status === "failed")) return cols[0].id;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("documents never became ready");
}

if (phase === "all" || phase === "empty") {
  const p = await page(1440, 900);
  await p.goto(base + "/");
  await shot(p, "01-empty-desktop");
  await overflow(p, "empty desktop");
  await p.getByRole("button", { name: "Load sample collection" }).first().click();
  await p.waitForTimeout(800);
  await shot(p, "02-after-samples");
  await p.close();
}
const cid = await waitReady();

if (phase === "all" || phase === "desktop") {
  const p = await page(1440, 900);
  await p.goto(base + "/");
  await p.getByText("Try asking").waitFor();
  await shot(p, "03-ask-intro-desktop");
  await p.locator(".suggestion").nth(1).click();
  await p.locator(".turn__meta").first().waitFor({ timeout: 30000 });
  await shot(p, "04-ask-answer-desktop");
  await p.locator(".cite").first().click();
  await shot(p, "05-ask-citation-desktop");
  await p.locator("textarea").fill("Who owns the certification delay risk?");
  await p.keyboard.press("Enter");
  await p.locator(".turn__meta").nth(1).waitFor({ timeout: 30000 });
  await shot(p, "06-ask-followup-desktop", true);
  await overflow(p, "ask desktop");
  // filters popover
  await p.getByRole("button", { name: /Filters/ }).click();
  await shot(p, "07-filters-popover");
  await p.keyboard.press("Escape");

  await p.goto(base + "/library");
  await p.locator("table.table").waitFor();
  await shot(p, "08-library-desktop");
  await overflow(p, "library desktop");
  await p.locator(".doc-link").filter({ hasText: "annual-report" }).click();
  await p.locator(".gallery").waitFor();
  await shot(p, "09-document-drawer");
  await p.keyboard.press("Escape");

  await p.goto(base + "/overview");
  await p.locator(".hero-figure").waitFor();
  await shot(p, "10-overview-desktop", true);
  await overflow(p, "overview desktop");

  await p.goto(base + "/evaluate");
  await p.locator(".golden__item").first().waitFor();
  await shot(p, "11-evaluate-before");
  await p.getByRole("button", { name: "Run evaluation" }).click();
  await p.locator("table.table td[data-best]").first().waitFor({ timeout: 120000 });
  await shot(p, "12-evaluate-results", true);
  await overflow(p, "evaluate desktop");

  await p.goto(base + "/settings");
  await p.locator(".settings-grid").first().waitFor();
  await p.locator("#s-top_k").fill("8");
  await shot(p, "13-settings-dirty", false);
  await p.getByRole("button", { name: "Discard" }).click();
  await overflow(p, "settings desktop");
  await p.goto(base + "/nope");
  await shot(p, "14-not-found");
  await p.close();
}

if (phase === "all" || phase === "dark") {
  const p = await page(1440, 900, "dark");
  await p.goto(base + "/");
  await p.locator(".suggestion").first().click();
  await p.locator(".turn__meta").first().waitFor({ timeout: 30000 });
  await shot(p, "15-ask-dark");
  await p.goto(base + "/overview");
  await p.locator(".hero-figure").waitFor();
  await shot(p, "16-overview-dark", true);
  await p.goto(base + "/library");
  await p.locator("table.table").waitFor();
  await shot(p, "17-library-dark");
  await p.close();
}

if (phase === "all" || phase === "mobile") {
  const p = await page(390, 844);
  await p.goto(base + "/");
  await shot(p, "18-ask-mobile");
  await overflow(p, "ask mobile");
  await p.locator(".suggestion").first().click();
  await p.locator(".turn__meta").first().waitFor({ timeout: 30000 });
  await shot(p, "19-ask-answer-mobile", true);
  await overflow(p, "ask answer mobile");
  await p.getByRole("button", { name: /Sources/ }).click();
  await shot(p, "20-sources-drawer-mobile");
  await p.keyboard.press("Escape");
  await p.getByRole("button", { name: "Open menu" }).click();
  await shot(p, "21-menu-mobile");
  await p.getByRole("link", { name: /Library/ }).click();
  await p.locator(".doc-cards").waitFor();
  await shot(p, "22-library-mobile", true);
  await overflow(p, "library mobile");
  for (const route of ["overview", "evaluate", "settings"]) {
    await p.goto(`${base}/${route}`);
    await p.waitForTimeout(900);
    await shot(p, `23-${route}-mobile`, true);
    await overflow(p, `${route} mobile`);
  }
  await p.close();

  const t = await page(820, 1180);
  for (const route of ["", "library", "overview"]) {
    await t.goto(`${base}/${route}`);
    await t.waitForTimeout(900);
    await shot(t, `24-${route || "ask"}-tablet`);
    await overflow(t, `${route || "ask"} tablet`);
  }
  await t.close();
  const w = await page(1920, 1080);
  await w.goto(base + "/");
  await w.waitForTimeout(800);
  await shot(w, "25-ask-1920");
  await w.close();
}

await browser.close();
console.log(issues.length ? issues.join("\n") : "NO ISSUES");

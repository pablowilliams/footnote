// Record the demo video against a running server (default http://127.0.0.1:8765).
//   npm i -D playwright && node scripts/record_demo.mjs
// Writes docs/media/demo.webm; scripts converts it to mp4 with ffmpeg if available.
import { chromium } from "playwright";
import { mkdirSync, renameSync, readdirSync } from "node:fs";

const BASE = process.env.FOOTNOTE_URL || "http://127.0.0.1:8765";
const OUT = "docs/media";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  colorScheme: "light",
  recordVideo: { dir: OUT, size: { width: 1280, height: 800 } },
});
const page = await context.newPage();
const pause = (ms) => page.waitForTimeout(ms);

async function caption(text) {
  await page.evaluate((t) => {
    let el = document.getElementById("demo-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-caption";
      el.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:900px;background:#1d1f23;color:#fff;" +
        "font:500 18px Inter,system-ui,sans-serif;padding:12px 20px;border-radius:8px;z-index:99;box-shadow:0 6px 24px rgba(0,0,0,.25);text-align:center";
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

async function askQuestion(q, note) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await pause(600);
  await page.fill("#q", "");
  await page.type("#q", q, { delay: 28 });
  if (note) await caption(note);
  await page.click("#ask button");
  await page.waitForSelector("#result:not([hidden])");
  await pause(2500);
}

async function openPanel(id) {
  await page.evaluate((i) => { const d = document.getElementById(i); d.open = true; d.scrollIntoView({ behavior: "smooth", block: "center" }); }, id);
  await pause(2600);
}

await page.goto(BASE);
await caption("Footnote answers questions about fifteen companies' SEC annual reports, and shows its working.");
await pause(4000);

await askQuestion("How did Apple's revenue and operating margin change between 2023 and 2025?",
  "Figures come from the filings; the change is computed in code, not by a model.");
await openPanel("sources-panel");
await caption("Every figure is cited to the statement and printed page, with a link to the filing on sec.gov.");
await pause(1500);
await openPanel("calc-panel");
await caption("Each calculation shows its formula and cited inputs.");
await pause(1200);
await openPanel("checks-panel");
await caption("The verifier re-derives every figure before the answer is released.");
await pause(2500);

await askQuestion("Compare free cash flow across Apple, Microsoft and Alphabet in 2024.",
  "Comparisons rank companies on the same measure and say when fiscal years differ.");
await pause(1500);

await askQuestion("Why did Intel's operating margin decline in 2024?",
  "Why questions combine the computed change with verbatim quotations from Item 7.");
await pause(2500);
await page.click("#evidence-btn");
await caption("A follow-up shows the full evidence behind the previous answer.");
await pause(4500);

await askQuestion("What was Apple's profit in 2024?", "An ambiguous question gets a question back, not a guess.");
await pause(1500);
await page.click("#options button:nth-child(2)");
await caption("Choosing an option answers the question that was meant.");
await pause(4000);

await askQuestion("What will Nvidia's revenue be in 2028?", "Forecasts, advice and companies outside the corpus are refused, with a reason.");
await pause(2500);
await askQuestion("What was Tesla's revenue in 2024?", "Tesla is not in the corpus, so there is nothing to answer from.");
await pause(3000);

await caption("Code, evaluation report and failure analysis: github.com/pablowilliams/footnote");
await pause(4000);

await context.close();
await browser.close();
const video = readdirSync(OUT).filter((f) => f.endsWith(".webm") && f !== "demo.webm").sort().pop();
renameSync(`${OUT}/${video}`, `${OUT}/demo.webm`);
console.log(`wrote ${OUT}/demo.webm`);

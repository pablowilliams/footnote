"use strict";

const EXAMPLES = [
  "How did Apple's revenue and operating margin change between 2023 and 2025?",
  "Which risks did Nvidia mention in its latest annual report?",
  "Compare free cash flow across Apple, Microsoft and Alphabet in 2024.",
  "Why did Intel's operating margin decline in 2024?",
  "What does Apple say about tariffs?",
  "How many employees does Costco have?",
  "What was Tesla's revenue in 2024?",
  "What will Nvidia's revenue be in 2028?",
  "What was Apple's profit in 2024?",
];
const STATUS_TEXT = {
  verified: "Verified",
  partially_verified: "Partially verified",
  ambiguous: "Needs clarification",
  insufficient_evidence: "Insufficient evidence",
  refused: "Refused",
};
const ROUTE_TEXT = {
  sql: "structured query",
  sql_calculation: "structured query and calculation",
  sql_and_document: "structured query and document search",
  comparison: "multi-company query",
  document: "document search",
  evidence: "evidence for earlier answer",
  clarify: "clarification",
  refuse: "refusal",
};

const isStatic = document.querySelector('meta[name="footnote-mode"]').content === "static";
let recorded = null;
let lastAnswer = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

document.querySelectorAll(isStatic ? "[data-live-only]" : "[data-static-only]").forEach((el) => el.remove());
if (isStatic) $("static-notice").hidden = false;

for (const q of EXAMPLES) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = q;
  b.onclick = () => { $("q").value = q; submit(q); };
  $("examples").appendChild(b);
}

$("ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("q").value.trim(); if (q) submit(q); });
$("evidence-btn").onclick = () => lastAnswer && submit("Show the evidence for that answer", lastAnswer.answer_id);

let engine = null;
if (isStatic) {
  engine = import("./engine.js").then((m) => { m.start().catch(engineFailed); return m; }).catch(engineFailed);
  window.addEventListener("footnote-progress", (e) => {
    const el = $("engine-status");
    el.hidden = !e.detail;
    el.textContent = e.detail;
    if (!e.detail) $("static-notice").textContent =
      "The engine is running in your browser: the same Python pipeline as the server, on the same snapshot of the filings. Ask anything within scope.";
  });
}
let engineBroken = false;
function engineFailed(err) {
  engineBroken = true;
  const el = $("engine-status");
  el.hidden = false;
  el.textContent = `The in-browser engine could not start (${err.message}). Example questions will replay recorded answers.`;
}

async function fetchAnswer(question, previous) {
  if (isStatic && !engineBroken) {
    const m = await engine;
    if (m) return m.ask(question, previous);
  }
  if (isStatic) {
    recorded = recorded || await (await fetch("recorded.json")).json();
    const key = previous ? `evidence:${recorded.byId[previous] || ""}` : question.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    const hit = recorded.answers[key];
    if (!hit) {
      return { status: "ambiguous", route: "clarify", latency_ms: 0, citations: [], checks: [], calculations: [], table: [], segments: [],
        answer: "This hosted page can only replay the example questions. Run Footnote locally to ask anything else.", clarification_options: [] };
    }
    return hit;
  }
  const res = await fetch("ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, previous_answer_id: previous || null }),
  });
  if (res.status === 429) throw new Error("Too many questions in a minute. Please wait and try again.");
  if (!res.ok) throw new Error(`The server returned ${res.status}.`);
  return res.json();
}

async function submit(question, previous) {
  const btn = document.querySelector("#ask button");
  btn.disabled = true;
  try {
    render(await fetchAnswer(question, previous), !!previous);
  } catch (err) {
    render({ status: "insufficient_evidence", route: "refuse", answer: err.message, citations: [], checks: [], calculations: [], table: [], segments: [] });
  } finally {
    btn.disabled = false;
  }
}

function cite(text) {
  return esc(text).replace(/\[(\d+)\]/g, '<sup class="cite" data-id="$1">[$1]</sup>');
}

function renderAnswer(a) {
  if (!a.segments || !a.segments.length || ["refused", "ambiguous"].includes(a.status) || a.route === "evidence") {
    return a.answer.split("\n").map((l) => `<p>${cite(l)}</p>`).join("");
  }
  if (a.status === "insufficient_evidence" && !a.citations.length) return `<p>${esc(a.answer)}</p>`;
  let html = "", para = [], list = [];
  const flushPara = () => { if (para.length) { html += `<p>${para.join(" ")}</p>`; para = []; } };
  const flushList = () => { if (list.length) { html += `<ul>${list.join("")}</ul>`; list = []; } };
  for (const s of a.segments) {
    if (s.bullet) {
      flushPara();
      const marks = s.citation_ids.map((i) => `[${i}]`).join("");
      list.push(`<li>“${esc(s.text)}” ${cite(marks)}</li>`);
    } else {
      flushList();
      para.push(cite(s.text));
    }
  }
  flushPara(); flushList();
  return html;
}

function fmtNumber(v, unit) {
  if (unit === "ratio") return `${(v * 100).toFixed(2)}%`;
  if (unit === "percentage_points") return `${(v * 100).toFixed(2)} pp`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 })} million`;
  return v.toLocaleString("en-US");
}

function render(a, isEvidence) {
  lastAnswer = isEvidence ? lastAnswer : a;
  $("result").hidden = false;
  const st = $("status");
  st.className = `status ${a.status}`;
  st.textContent = STATUS_TEXT[a.status] || a.status;
  $("route").textContent = `Route: ${ROUTE_TEXT[a.route] || a.route}`;
  $("latency").textContent = a.latency_ms ? `${a.latency_ms} ms` : "";
  $("answer").innerHTML = renderAnswer(a);

  $("options").innerHTML = "";
  for (const opt of a.clarification_options || []) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = opt;
    b.onclick = () => { const q = `${$("q").value.trim()} (${opt})`; $("q").value = q; submit(q); };
    $("options").appendChild(b);
  }
  $("evidence-btn").hidden = isEvidence || !a.citations || !a.citations.length;

  $("sources").innerHTML = (a.citations || []).map((c) => {
    const label = `${esc(c.company)}, Form ${esc(c.form || "10-K")} for fiscal ${c.fiscal_year}${c.filed ? ` (filed ${esc(c.filed)})` : ""}, ${esc(c.location)}${c.page ? `, page ${esc(c.page)}` : ""}`;
    const link = c.url ? ` <a href="${esc(c.url)}" target="_blank" rel="noopener">SEC filing</a>` : "";
    const tag = c.xbrl_tag ? ` <span class="snippet">XBRL ${esc(c.xbrl_tag)}</span>` : "";
    const snip = c.snippet && c.kind !== "document" ? `<span class="snippet">“${esc(c.snippet)}”</span>` : "";
    return `<li id="src-${c.id}" value="${c.id}">${label}.${link}${tag}${snip}</li>`;
  }).join("");
  $("sources-panel").hidden = !(a.citations || []).length;

  $("calcs").innerHTML = (a.calculations || []).map((c) =>
    `<div class="calc"><strong>${esc(c.description)}</strong><br><code>${esc(c.formula)}</code> = ${fmtNumber(c.result, c.unit)}<br>` +
    c.inputs.map((i) => `${esc(i.label)}: ${fmtNumber(i.value, c.formula === "end - start" ? "ratio" : "USD")} [${i.citation_id}]`).join("; ") + "</div>").join("");
  $("calc-panel").hidden = !(a.calculations || []).length;

  $("checks").innerHTML = (a.checks || []).map((c) => `<li class="${c.passed ? "pass" : "fail"}"><strong>${esc(c.name.replace(/_/g, " "))}</strong>: ${esc(c.detail)}</li>`).join("");
  $("checks-panel").hidden = !(a.checks || []).length;

  const rows = a.table || [];
  $("table").innerHTML = rows.length
    ? "<tr><th>Company</th><th>Measure</th><th>Fiscal year</th><th>Value</th></tr>" +
      rows.map((r) => `<tr><td>${esc(r.company)}</td><td>${esc(r.metric)}</td><td>${r.fiscal_year}</td><td class="num">${esc(r.display)}</td></tr>`).join("")
    : "";
  $("table-panel").hidden = !rows.length;

  document.querySelectorAll("sup.cite").forEach((s) => {
    s.onclick = () => {
      $("sources-panel").open = true;
      const li = $(`src-${s.dataset.id}`);
      if (li) { li.scrollIntoView({ behavior: "smooth", block: "center" }); li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1200); }
    };
  });
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

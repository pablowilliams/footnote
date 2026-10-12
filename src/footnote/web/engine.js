// The Footnote engine in the browser: the Python pipeline under Pyodide, the
// embedding model under transformers.js, the snapshot in memory. Used by the
// GitHub Pages build, where there is no server.
import { pipeline, env } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const SNAPSHOT = ["companies.jsonl", "filings.jsonl", "metric_values.jsonl", "chunks.jsonl.gz", "embeddings.npz"];

env.allowLocalModels = false;
let ready = null;
let py = null;
let embedder = null;

function progress(text) {
  window.dispatchEvent(new CustomEvent("footnote-progress", { detail: text }));
}

async function load() {
  progress("Loading Python (Pyodide)…");
  await new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = PYODIDE + "pyodide.js";
    s.onload = resolve;
    s.onerror = () => reject(new Error("Could not load Pyodide."));
    document.head.appendChild(s);
  });
  py = await globalThis.loadPyodide({ indexURL: PYODIDE });
  progress("Loading NumPy, Pydantic and SQLite…");
  await py.loadPackage(["numpy", "pydantic", "sqlite3", "micropip"]);
  const micropip = py.pyimport("micropip");
  progress("Installing sqlglot, rank-bm25 and structlog…");
  await micropip.install(["sqlglot==30.22.0", "rank-bm25==0.2.2", "structlog==25.5.0"]);

  progress("Loading the Footnote engine and the filings snapshot…");
  const [pkg, ...files] = await Promise.all([
    fetch("engine/footnote.zip").then((r) => r.arrayBuffer()),
    ...SNAPSHOT.map((f) => fetch(`snapshot/${f}`).then((r) => { if (!r.ok) throw new Error(`Missing ${f}`); return r.arrayBuffer(); })),
  ]);
  py.unpackArchive(pkg, "zip", { extractDir: "/engine" });
  py.FS.mkdirTree("/snapshot");
  SNAPSHOT.forEach((f, i) => py.FS.writeFile(`/snapshot/${f}`, new Uint8Array(files[i])));
  py.runPython("import sys; sys.path.insert(0, '/engine')");

  progress("Loading the embedding model (all-MiniLM-L6-v2)…");
  embedder = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", { dtype: "fp32" });

  progress("Indexing 5,618 passages…");
  py.runPython("from footnote import browser; browser.setup('/snapshot')");
  progress("");
}

export function start() {
  ready = ready || load();
  return ready;
}

async function embed(texts) {
  const out = [];
  for (let i = 0; i < texts.length; i += 32) {
    const t = await embedder(texts.slice(i, i + 32), { pooling: "mean", normalize: true });
    out.push(...t.tolist());
  }
  return out;
}

export async function ask(question, previousAnswerId) {
  await start();
  const browser = py.pyimport("footnote.browser");
  const started = performance.now();
  for (let round = 0; round < 5; round++) {
    const result = JSON.parse(browser.ask(question, previousAnswerId || null));
    if (!result.need) {
      result.latency_ms = Math.round(performance.now() - started);
      return result;
    }
    browser.add_embeddings(JSON.stringify(result.need), JSON.stringify(await embed(result.need)));
  }
  throw new Error("The engine did not settle on an answer.");
}

export async function vectors(texts) {
  await start();
  return embed(texts);
}

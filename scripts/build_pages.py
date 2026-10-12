"""Build the static site for GitHub Pages into site/.

The hosted page is the real web interface in replay mode: it serves answers the
system recorded for the example questions (data/recorded.json), next to the case
study, the design documents and the evaluation report rendered as HTML.
No database or model is needed to build it.
"""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DOCS = ["case-study", "evaluation", "failure-analysis", "design-note", "architecture", "data-model", "api"]
TITLES = {
    "case-study": "Case study",
    "evaluation": "Evaluation report",
    "failure-analysis": "Failure analysis",
    "design-note": "Design note",
    "architecture": "Architecture",
    "data-model": "Data model",
    "api": "API reference",
}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Footnote</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../static/styles.css">
<style>
  .doc {{ max-width: 860px; margin: 0 auto; padding: 20px 20px 60px; }}
  .doc h1 {{ font-family: var(--serif); font-size: 32px; margin-top: 28px; }}
  .doc h2 {{ font-family: var(--serif); margin-top: 34px; }}
  .doc table {{ margin: 14px 0; font-size: 14px; }}
  .doc pre {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 13px; }}
  .doc code {{ font-size: 0.92em; }}
  .doc a {{ color: var(--accent); }}
  .doc .mermaid {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 12px; }}
  .docnav {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 14px; padding-top: 14px; }}
  .docnav a {{ color: var(--muted); text-decoration: none; }}
  .docnav a.on {{ color: var(--ink); font-weight: 600; }}
</style></head>
<body>
<header class="top"><div class="wrap"><div class="brand"><a href="../" style="color:inherit;text-decoration:none"><span class="mark">¹</span> Footnote</a></div>
<nav><a href="../">Demo</a><a href="https://github.com/pablowilliams/footnote">Source</a></nav></div></header>
<main class="doc"><div class="docnav">{nav}</div>{body}</main>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true, theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "neutral" }});
</script>
</body></html>
"""


def render(name: str) -> str:
    text = (ROOT / "docs" / f"{name}.md").read_text()
    blocks: list[str] = []

    def keep(m: re.Match[str]) -> str:
        blocks.append(m.group(1))
        return f"MERMAIDBLOCK{len(blocks) - 1}"

    text = re.sub(r"```mermaid\n(.*?)```", keep, text, flags=re.S)
    # Links between documents point at the rendered pages.
    text = re.sub(r"\]\(([a-z\-]+)\.md\)", r"](\1.html)", text)
    text = text.replace("](../eval/questions.yaml)", "](https://github.com/pablowilliams/footnote/blob/main/eval/questions.yaml)")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    for i, block in enumerate(blocks):
        body = body.replace(f"<p>MERMAIDBLOCK{i}</p>", f'<pre class="mermaid">{html.escape(block)}</pre>')
    nav = "".join(f'<a href="{d}.html"{" class=on" if d == name else ""}>{TITLES[d]}</a>' for d in DOCS)
    return PAGE.format(title=TITLES[name], nav=nav, body=body)


def main() -> None:
    shutil.rmtree(SITE, ignore_errors=True)
    (SITE / "docs").mkdir(parents=True)
    shutil.copytree(ROOT / "src/footnote/web", SITE / "static")
    index = (ROOT / "src/footnote/web/index.html").read_text()
    index = index.replace('content="live"', 'content="static"')
    (SITE / "index.html").write_text(index)
    shutil.copy(ROOT / "data/recorded.json", SITE / "recorded.json")
    for name in DOCS:
        (SITE / "docs" / f"{name}.html").write_text(render(name))
    (SITE / "docs" / "index.html").write_text(render("case-study"))
    media = ROOT / "docs" / "media"
    if media.exists():
        shutil.copytree(media, SITE / "docs" / "media")
    print(f"built {SITE}")


if __name__ == "__main__":
    main()

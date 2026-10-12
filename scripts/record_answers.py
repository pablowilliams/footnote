"""Record the system's answers to the example questions for the static demo page.

Run with the database loaded:  PYTHONPATH=src python scripts/record_answers.py
Writes data/recorded.json, which the Pages build serves so the hosted page can
replay real answers without a server. Also writes docs/openapi.json and docs/api.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from footnote.api import app
from footnote.graph import ask

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = re.findall(r'^  "(.+)",$', (ROOT / "src/footnote/web/app.js").read_text(), re.M)


def key(question: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()


def record() -> None:
    answers: dict[str, object] = {}
    by_id: dict[str, str] = {}
    for q in EXAMPLES:
        a = ask(q, persist=True)
        answers[key(q)] = json.loads(a.model_dump_json())
        if a.citations:
            evidence = ask("Show the evidence for that answer", previous_answer_id=a.answer_id, persist=False)
            answers[f"evidence:{key(q)}"] = json.loads(evidence.model_dump_json())
            by_id[a.answer_id] = key(q)
    (ROOT / "data/recorded.json").write_text(json.dumps({"answers": answers, "byId": by_id}, indent=1))
    print(f"recorded {len(EXAMPLES)} questions")


def api_reference() -> None:
    spec = app.openapi()
    (ROOT / "docs/openapi.json").write_text(json.dumps(spec, indent=2))
    lines = [
        "# API reference",
        "",
        f"Generated from the OpenAPI schema of Footnote {spec['info']['version']} by `scripts/record_answers.py`. "
        "A running server also serves interactive documentation at `/docs` and the raw schema at `/openapi.json`.",
        "",
        spec["info"]["description"],
        "",
    ]
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            lines += [f"## `{method.upper()} {path}`", "", op.get("description") or op.get("summary", ""), ""]
            params = op.get("parameters", [])
            if params:
                lines += ["| Parameter | In | Required | Type |", "|---|---|---|---|"]
                for p in params:
                    schema = p.get("schema", {})
                    typ = schema.get("type") or " or ".join(x.get("type", "") for x in schema.get("anyOf", []) if x.get("type"))
                    lines.append(f"| `{p['name']}` | {p['in']} | {'yes' if p.get('required') else 'no'} | {typ} |")
                lines.append("")
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            if "$ref" in body:
                name = body["$ref"].split("/")[-1]
                props = spec["components"]["schemas"][name]["properties"]
                lines += [f"Request body (`{name}`): " + ", ".join(f"`{k}`" for k in props), ""]
    lines += [
        "## Answer object",
        "",
        "| Field | Meaning |",
        "|---|---|",
        "| `status` | `verified`, `partially_verified`, `ambiguous`, `insufficient_evidence` or `refused` |",
        "| `route` | Which path answered the question: `sql`, `sql_calculation`, `comparison`, `document`, `sql_and_document`, `evidence`, `clarify` or `refuse` |",
        "| `answer` | The answer text, with citation markers such as `[1]` |",
        "| `segments` | The answer sentence by sentence, each with the citation ids behind it; `kind` is `computed`, `quoted` or `context` |",
        "| `citations` | Filing, statement or section, printed page, accession number and sec.gov link for each source |",
        "| `calculations` | Formula, cited inputs and result for every computed figure |",
        "| `checks` | Each verification check, whether it passed, and why |",
        "| `table` | The figures behind a structured answer, one row per company, measure and year |",
        "| `clarification_options` | Choices offered when the question was ambiguous |",
        "| `reason` | Why the system declined, when it did |",
        "| `sql` | The query that produced the figures, for structured answers |",
        "",
    ]
    (ROOT / "docs/api.md").write_text("\n".join(lines))
    print("wrote docs/api.md")


if __name__ == "__main__":
    record()
    api_reference()

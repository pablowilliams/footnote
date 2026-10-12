"""Run the hand-written evaluation set and write a report by question category.

Scoring is deterministic. No model grades another model here: each question
states what a correct answer must contain (a figure, an order, a direction, a
status, or a passage matching a pattern from the right company and section), and
the scorer checks for exactly that.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from collections import defaultdict
from typing import Any

import yaml

from footnote import calc
from footnote.answers import _strip_entities, _text_years
from footnote.catalog import METRIC_BY_KEY
from footnote.config import ROOT
from footnote.graph import ask
from footnote.question import parse
from footnote.retrieval import search
from footnote.schemas import Answer

QUESTIONS = ROOT / "eval" / "questions.yaml"
REPORT_JSON = ROOT / "reports" / "evaluation.json"
REPORT_MD = ROOT / "docs" / "evaluation.md"
DECLINE = {"refused", "insufficient_evidence", "ambiguous"}


def _gold_text(g: dict[str, Any]) -> str:
    unit = METRIC_BY_KEY[g["metric"]].unit
    value = g["value"] if unit in ("USD/share", "people") else g["value"] * 1e6
    return calc.fmt_value(value, unit)


def score(q: dict[str, Any], a: Answer) -> dict[str, Any]:
    reasons: list[str] = []
    if a.status.value not in q["expect_status"]:
        reasons.append(f"status {a.status.value}, expected {'/'.join(q['expect_status'])}")
    route_ok = a.route.value == q["expect_route"]
    if not route_ok:
        reasons.append(f"route {a.route.value}, expected {q['expect_route']}")
    text = a.answer
    for g in q.get("gold", []):
        want = _gold_text(g)
        if want not in text:
            reasons.append(f"missing {want} ({g['ticker']} {g['metric']} {g['year']})")
    if "gold_change" in q:
        want = calc.fmt_change(q["gold_change"])
        if want not in text:
            reasons.append(f"missing change {want}")
    if "gold_ratio" in q:
        want = calc.fmt_value(q["gold_ratio"], "ratio")
        if want not in text:
            reasons.append(f"missing ratio {want}")
    if "order" in q:
        positions = [text.find(name) for name in q["order"]]
        if any(p < 0 for p in positions) or positions != sorted(positions):
            reasons.append(f"order wrong, expected {' > '.join(q['order'])}")
    if "direction" in q:
        lead = next((s.text for s in a.segments if s.calc_index is not None), "")
        if q["direction"] not in lead:
            reasons.append(f"direction not '{q['direction']}'")
    if "claim" in q:
        expected = "is consistent with" if q["claim"] else "does not match"
        if expected not in text:
            reasons.append(f"claim check missing '{expected}'")

    ev = q.get("evidence")
    citation_precision = None
    evidence_found = None
    if ev and a.status.value not in DECLINE:
        docs = [c for c in a.citations if c.kind == "document"]
        good = [
            c
            for c in docs
            if c.ticker == ev["ticker"]
            and ("year" not in ev or c.fiscal_year == ev["year"])
            and ("item" not in ev or c.location.startswith(f"Item {ev['item']},"))
        ]
        citation_precision = len(good) / len(docs) if docs else 0.0
        pattern = re.compile(ev["pattern"], re.I)
        quoted = [s.text for s in a.segments if s.kind == "quoted"]
        evidence_found = any(pattern.search(t) for t in quoted)
        if not docs:
            reasons.append("no document citations")
        elif citation_precision < 1:
            reasons.append(f"citation precision {citation_precision:.2f}")
        if not evidence_found:
            reasons.append("no quoted passage matches the expected topic")
    elif a.citations and a.status.value not in DECLINE:
        # A citation is precise if it belongs to a company the question asked about.
        tickers = set(parse(q["question"]).tickers)
        citation_precision = sum(c.ticker in tickers for c in a.citations) / len(a.citations)

    completeness = None
    if q.get("gold") and a.status.value not in DECLINE:
        cited = {(c.ticker, c.fiscal_year) for c in a.citations}
        completeness = sum((g["ticker"], g["year"]) in cited for g in q["gold"]) / len(q["gold"])

    return {
        "id": q["id"],
        "category": q["category"],
        "question": q["question"],
        "status": a.status.value,
        "route": a.route.value,
        "route_correct": route_ok,
        "correct": not reasons,
        "reasons": reasons,
        "citation_precision": citation_precision,
        "citation_completeness": completeness,
        "evidence_found": evidence_found,
        "latency_ms": a.latency_ms,
        "checks_failed": [c.name for c in a.checks if not c.passed],
        "answer": a.answer,
    }


def retrieval_recall(questions: list[dict[str, Any]], k: int = 8) -> dict[str, Any]:
    """Rank of the first passage on the expected topic, for each retriever alone and combined.

    Search is filtered to the right company, year and section, so finding something
    on topic within eight passages is easy; the useful comparison is how high the
    first relevant passage ranks (hit at 1, hit at 3, and mean reciprocal rank).
    """
    rows = []
    for q in questions:
        ev = q.get("evidence")
        if not ev:
            continue
        parsed = parse(q["question"])
        year = ev.get("year") or max(_text_years([ev["ticker"]]).get(ev["ticker"], [0]))
        items = [ev["item"]] if "item" in ev else None
        query = _strip_entities(q["question"], parsed)
        pattern = re.compile(ev["pattern"], re.I)
        row: dict[str, Any] = {"id": q["id"]}
        for mode in ("bm25", "dense", "hybrid"):
            hits = search(query, [ev["ticker"]], [year], items, k=k, mode=mode)
            row[mode] = next((i + 1 for i, h in enumerate(hits) if pattern.search(h.text)), None)
        rows.append(row)

    def summarise(mode: str) -> dict[str, float]:
        ranks = [r[mode] for r in rows]
        return {
            "hit_at_1": round(sum(1 for x in ranks if x == 1) / len(ranks), 3),
            "hit_at_3": round(sum(1 for x in ranks if x and x <= 3) / len(ranks), 3),
            "recall_at_8": round(sum(1 for x in ranks if x) / len(ranks), 3),
            "mrr": round(sum(1 / x for x in ranks if x) / len(ranks), 3),
        }

    return {"k": k, "questions": len(rows), **{mode: summarise(mode) for mode in ("bm25", "dense", "hybrid")}, "rows": rows}


def _rate(values: list[bool | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 3) if vals else None


def run(write: bool = True) -> dict[str, Any]:
    questions = yaml.safe_load(QUESTIONS.read_text())
    results = []
    sql_runs = sql_errors = 0
    for q in questions:
        try:
            answer = ask(q["question"], persist=False)
        except Exception as err:  # a crash is a wrong answer, and it is counted
            results.append(
                {
                    "id": q["id"],
                    "category": q["category"],
                    "question": q["question"],
                    "correct": False,
                    "reasons": [f"error: {err}"],
                    "status": "error",
                    "route": "error",
                    "latency_ms": 0,
                    "route_correct": False,
                    "citation_precision": None,
                    "citation_completeness": None,
                    "evidence_found": None,
                    "checks_failed": [],
                    "answer": "",
                }
            )
            if q["expect_route"].startswith("sql") or q["expect_route"] == "comparison":
                sql_runs += 1
                sql_errors += 1
            continue
        if answer.sql is not None:
            sql_runs += 1
        results.append(score(q, answer))

    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    categories = {
        cat: {
            "questions": len(rs),
            "answer_correct": _rate([r["correct"] for r in rs]),
            "route_correct": _rate([r["route_correct"] for r in rs]),
            "citation_precision": _mean([r["citation_precision"] for r in rs]),
        }
        for cat, rs in by_cat.items()
    }
    should_decline = [
        r
        for r in results
        if r["category"] in ("ambiguous", "unanswerable")
        or (
            (r["category"] == "adversarial" and r["status"] != "verified")
            or r["id"] in ("X01", "X02", "X03", "X04", "X05", "X08")
        )
    ]
    should_decline_ids = {r["id"] for r in results if r["category"] in ("ambiguous", "unanswerable")} | {
        "X01",
        "X02",
        "X03",
        "X04",
        "X05",
        "X08",
    }
    declined = {r["id"] for r in results if r["status"] in DECLINE}
    tp = len(declined & should_decline_ids)
    del should_decline
    latencies = defaultdict(list)
    for r in results:
        latencies[r["route"]].append(r["latency_ms"])
    all_lat = sorted(r["latency_ms"] for r in results)
    started = time.perf_counter()
    retrieval = retrieval_recall(questions)
    report = {
        "generated": time.strftime("%Y-%m-%d"),
        "overall": {
            "questions": len(results),
            "answer_correct": _rate([r["correct"] for r in results]),
            "route_correct": _rate([r["route_correct"] for r in results]),
            "citation_precision": _mean([r["citation_precision"] for r in results]),
            "citation_completeness": _mean([r["citation_completeness"] for r in results]),
            "refusal_precision": round(tp / len(declined), 3) if declined else None,
            "refusal_recall": round(tp / len(should_decline_ids), 3),
            "sql_execution_success": round(1 - sql_errors / sql_runs, 3) if sql_runs else None,
            "retrieval_mrr_hybrid": retrieval["hybrid"]["mrr"],
            "latency_ms_p50": all_lat[len(all_lat) // 2],
            "latency_ms_p95": all_lat[min(len(all_lat) - 1, int(len(all_lat) * 0.95))],
            "cost_per_query_usd": 0.0,
        },
        "categories": categories,
        "retrieval": retrieval,
        "latency_by_route": {k: {"p50": sorted(v)[len(v) // 2], "max": max(v), "n": len(v)} for k, v in latencies.items()},
        "results": results,
        "retrieval_seconds": round(time.perf_counter() - started, 1),
    }
    if write:
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(report, indent=2, default=str))
        REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
        REPORT_MD.write_text(render_markdown(report))
    return report


_CATEGORY_TEXT = {
    "factual": "Factual: one reported figure",
    "numerical": "Numerical: a calculation from reported figures",
    "comparison": "Comparison across companies",
    "multi_hop": "Multi-hop: figures plus the company's explanation",
    "document": "Document: what a filing says",
    "ambiguous": "Ambiguous: should ask for clarification",
    "unanswerable": "Unanswerable: outside the corpus",
    "adversarial": "Adversarial: designed to provoke a wrong answer",
}


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render_markdown(r: dict[str, Any]) -> str:
    o = r["overall"]
    lines = [
        "# Evaluation report",
        "",
        f"Generated {r['generated']} by `make evaluate` from [`eval/questions.yaml`](../eval/questions.yaml). "
        "Scoring is deterministic: each question states what a correct answer must contain, and nothing is graded by a language model.",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Questions | {o['questions']} |",
        f"| Answer correctness | {_pct(o['answer_correct'])} |",
        f"| Routing accuracy | {_pct(o['route_correct'])} |",
        f"| Citation precision | {_pct(o['citation_precision'])} |",
        f"| Citation completeness | {_pct(o['citation_completeness'])} |",
        f"| Refusal precision | {_pct(o['refusal_precision'])} |",
        f"| Refusal recall | {_pct(o['refusal_recall'])} |",
        f"| SQL execution success | {_pct(o['sql_execution_success'])} |",
        f"| Retrieval, mean reciprocal rank (hybrid) | {o['retrieval_mrr_hybrid']:.3f} |",
        f"| Latency, median / 95th percentile | {o['latency_ms_p50']} ms / {o['latency_ms_p95']} ms |",
        f"| Model cost per question | ${o['cost_per_query_usd']:.2f} (template narrator; no paid model is called) |",
        "",
        "## By category",
        "",
        "| Category | Questions | Correct | Routing | Citation precision |",
        "|---|---|---|---|---|",
    ]
    for cat, c in r["categories"].items():
        lines.append(
            f"| {_CATEGORY_TEXT.get(cat, cat)} | {c['questions']} | {_pct(c['answer_correct'])} | {_pct(c['route_correct'])} | {_pct(c['citation_precision'])} |"
        )
    ret = r["retrieval"]
    lines += [
        "",
        "## Retrieval: each retriever alone and combined",
        "",
        f"For the {ret['questions']} questions that name a company and a topic, search is filtered to that company, year and section, "
        "and a passage counts as relevant when it contains the expected topic. The table shows how high the first relevant passage ranks.",
        "",
        "| Retriever | First result relevant | Relevant in top 3 | Relevant in top 8 | Mean reciprocal rank |",
        "|---|---|---|---|---|",
        *[
            f"| {label} | {_pct(ret[m]['hit_at_1'])} | {_pct(ret[m]['hit_at_3'])} | {_pct(ret[m]['recall_at_8'])} | {ret[m]['mrr']:.3f} |"
            for m, label in (
                ("bm25", "BM25 only"),
                ("dense", "Dense vectors only"),
                ("hybrid", "Hybrid (reciprocal rank fusion)"),
            )
        ],
        "",
        "## Latency by route",
        "",
        "| Route | Questions | Median | Slowest |",
        "|---|---|---|---|",
    ]
    for route, v in sorted(r["latency_by_route"].items()):
        lines.append(f"| {route} | {v['n']} | {v['p50']} ms | {v['max']} ms |")
    wrong = [x for x in r["results"] if not x["correct"]]
    lines += ["", "## Questions answered incorrectly", ""]
    if not wrong:
        lines.append("None.")
    for x in wrong:
        lines.append(f"- **{x['id']}** ({x['category']}) {x['question']}  ")
        lines.append(f"  {'; '.join(x['reasons'])}")
    lines += ["", "## Every question", "", "| ID | Category | Status | Route | Correct |", "|---|---|---|---|---|"]
    for x in r["results"]:
        lines.append(f"| {x['id']} | {x['category']} | {x['status']} | {x['route']} | {'yes' if x['correct'] else 'no'} |")
    return "\n".join(lines) + "\n"

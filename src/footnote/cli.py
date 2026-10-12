"""Command line: footnote {ingest,migrate,load,ask,search,serve,evaluate}."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="footnote", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="download filings from SEC EDGAR and rebuild the snapshot")
    sub.add_parser("migrate", help="apply database migrations")
    load = sub.add_parser("load", help="load the snapshot into PostgreSQL and embed passages")
    load.add_argument("--no-embed", action="store_true")
    a = sub.add_parser("ask", help="ask a question")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    s = sub.add_parser("search", help="hybrid search over passages")
    s.add_argument("query")
    s.add_argument("--ticker", action="append")
    s.add_argument("--year", type=int, action="append")
    s.add_argument("--item", action="append")
    s.add_argument("-k", type=int, default=5)
    srv = sub.add_parser("serve", help="run the API")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    ev = sub.add_parser("evaluate", help="run the evaluation set and write the report")
    ev.add_argument("--min-correct", type=float, default=0.0, help="exit non-zero below this overall correctness")
    args = parser.parse_args(argv)

    if args.command == "ingest":
        from footnote.ingest.pipeline import ingest

        print(json.dumps(ingest()["totals"], indent=2))
    elif args.command == "migrate":
        from footnote.db import migrate

        print("applied:", migrate() or "nothing to apply")
    elif args.command == "load":
        from footnote.db import load_snapshot

        print(json.dumps(load_snapshot(embed=not args.no_embed), indent=2))
    elif args.command == "ask":
        from footnote.graph import ask

        answer = ask(args.question)
        if args.json:
            print(answer.model_dump_json(indent=2))
        else:
            print(f"[{answer.status.value} | {answer.route.value} | {answer.latency_ms} ms]\n")
            print(answer.answer)
            if answer.citations:
                print()
                for c in answer.citations:
                    print(f"[{c.id}] {c.label}")
    elif args.command == "search":
        from footnote.retrieval import search

        for hit in search(args.query, args.ticker, args.year, args.item, k=args.k):
            print(f"{hit.score:.4f}  {hit.ticker} FY{hit.fiscal_year} Item {hit.item} p.{hit.page}  {hit.text[:140]}")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("footnote.api:app", host=args.host, port=args.port)
    elif args.command == "evaluate":
        from footnote.evaluation import run

        report = run()
        overall = report["overall"]["answer_correct"]
        print(json.dumps(report["overall"], indent=2))
        if overall < args.min_correct:
            print(f"correctness {overall:.3f} is below the threshold {args.min_correct}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Footnote: common tasks. `make demo` is the shortest path from a clone to a working system.
PY ?= python
export PYTHONPATH := src

.PHONY: setup db load demo serve test lint typecheck evaluate ingest pages video clean

setup:            ## install the package with development tools
	$(PY) -m pip install -e ".[dev]"

db:               ## create the database (local PostgreSQL with pgvector) and apply migrations
	createdb footnote 2>/dev/null || true
	$(PY) -m footnote.cli migrate

load: db          ## load the committed snapshot into PostgreSQL
	$(PY) -m footnote.cli load

demo: load serve  ## load the data and start the API and web interface on http://127.0.0.1:8000

serve:
	$(PY) -m footnote.cli serve

ingest:           ## re-download filings from SEC EDGAR and rebuild the snapshot
	$(PY) -m footnote.cli ingest

test:
	$(PY) -m pytest --cov --cov-report=term-missing:skip-covered

lint:
	ruff check src tests
	ruff format --check src tests

typecheck:
	mypy

evaluate:         ## run the 67-question evaluation and write docs/evaluation.md
	$(PY) -m footnote.cli evaluate

pages:            ## build the static site in site/ (case study, evaluation, recorded answers)
	$(PY) scripts/build_pages.py

video:            ## record the demo video into docs/media/ (needs a running server)
	node scripts/record_demo.mjs

clean:
	rm -rf site .pytest_cache .mypy_cache .ruff_cache .coverage

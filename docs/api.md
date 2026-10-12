# API reference

Generated from the OpenAPI schema of Footnote 1.0.0 by `scripts/record_answers.py`. A running server also serves interactive documentation at `/docs` and the raw schema at `/openapi.json`.

Answers questions about fifteen companies' SEC annual reports, with citations, recomputed figures and explicit refusals when the evidence is insufficient.

## `GET /health`

Liveness and data readiness: the database answers and the corpus is loaded.

## `POST /ask`

Answer a question. Pass `previous_answer_id` to ask for the evidence behind an earlier answer.

Request body (`AskRequest`): `question`, `previous_answer_id`

## `GET /search`

Hybrid search over the annual-report passages, with optional company, year and section filters.

| Parameter | In | Required | Type |
|---|---|---|---|
| `q` | query | yes | string |
| `ticker` | query | no | array or null |
| `year` | query | no | array or null |
| `item` | query | no | array or null |
| `k` | query | no | integer |

## `GET /companies`

Companies

## `GET /scope`

What the system will and will not answer about.

## `GET /history`

History

| Parameter | In | Required | Type |
|---|---|---|---|
| `limit` | query | no | integer |

## Answer object

| Field | Meaning |
|---|---|
| `status` | `verified`, `partially_verified`, `ambiguous`, `insufficient_evidence` or `refused` |
| `route` | Which path answered the question: `sql`, `sql_calculation`, `comparison`, `document`, `sql_and_document`, `evidence`, `clarify` or `refuse` |
| `answer` | The answer text, with citation markers such as `[1]` |
| `segments` | The answer sentence by sentence, each with the citation ids behind it; `kind` is `computed`, `quoted` or `context` |
| `citations` | Filing, statement or section, printed page, accession number and sec.gov link for each source |
| `calculations` | Formula, cited inputs and result for every computed figure |
| `checks` | Each verification check, whether it passed, and why |
| `table` | The figures behind a structured answer, one row per company, measure and year |
| `clarification_options` | Choices offered when the question was ambiguous |
| `reason` | Why the system declined, when it did |
| `sql` | The query that produced the figures, for structured answers |

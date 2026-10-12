# Evaluation report

Generated 2026-10-12 by `make evaluate` from [`eval/questions.yaml`](../eval/questions.yaml). Scoring is deterministic: each question states what a correct answer must contain, and nothing is graded by a language model.

## Summary

| Measure | Result |
|---|---|
| Questions | 72 |
| Answer correctness | 98.6% |
| Routing accuracy | 100.0% |
| Citation precision | 100.0% |
| Citation completeness | 98.1% |
| Refusal precision | 100.0% |
| Refusal recall | 100.0% |
| SQL execution success | 100.0% |
| Retrieval, mean reciprocal rank (hybrid) | 0.958 |
| Latency, median / 95th percentile | 7 ms / 425 ms |
| Model cost per question | $0.00 (template narrator; no paid model is called) |

## By category

| Category | Questions | Correct | Routing | Citation precision |
|---|---|---|---|---|
| Factual: one reported figure | 16 | 100.0% | 100.0% | 100.0% |
| Numerical: a calculation from reported figures | 11 | 100.0% | 100.0% | 100.0% |
| Comparison across companies | 7 | 100.0% | 100.0% | 100.0% |
| Multi-hop: figures plus the company's explanation | 6 | 100.0% | 100.0% | 100.0% |
| Document: what a filing says | 12 | 91.7% | 100.0% | 100.0% |
| Ambiguous: should ask for clarification | 5 | 100.0% | 100.0% | n/a |
| Unanswerable: outside the corpus | 7 | 100.0% | 100.0% | n/a |
| Adversarial: designed to provoke a wrong answer | 8 | 100.0% | 100.0% | 100.0% |

## Retrieval: each retriever alone and combined

For the 18 questions that name a company and a topic, search is filtered to that company, year and section, and a passage counts as relevant when it contains the expected topic. The table shows how high the first relevant passage ranks.

| Retriever | First result relevant | Relevant in top 3 | Relevant in top 8 | Mean reciprocal rank |
|---|---|---|---|---|
| BM25 only | 88.9% | 94.4% | 100.0% | 0.926 |
| Dense vectors only | 100.0% | 100.0% | 100.0% | 1.000 |
| Hybrid (reciprocal rank fusion) | 94.4% | 94.4% | 100.0% | 0.958 |

## Latency by route

| Route | Questions | Median | Slowest |
|---|---|---|---|
| clarify | 4 | 2 ms | 2 ms |
| comparison | 7 | 7 ms | 8 ms |
| document | 13 | 312 ms | 474 ms |
| evidence | 1 | 1 ms | 1 ms |
| refuse | 10 | 1 ms | 2 ms |
| sql | 23 | 7 ms | 70 ms |
| sql_and_document | 6 | 237 ms | 11811 ms |
| sql_calculation | 8 | 7 ms | 8 ms |

## Questions answered incorrectly

- **D06** (document) Who does Amazon identify as competitors for AWS?  
  no quoted passage matches the expected topic

## Every question

| ID | Category | Status | Route | Correct |
|---|---|---|---|---|
| F01 | factual | verified | sql | yes |
| F02 | factual | verified | sql | yes |
| F03 | factual | verified | sql | yes |
| F04 | factual | verified | sql | yes |
| F05 | factual | verified | sql | yes |
| F06 | factual | verified | sql | yes |
| F07 | factual | verified | sql | yes |
| F08 | factual | verified | sql | yes |
| F09 | factual | verified | sql | yes |
| F10 | factual | verified | sql | yes |
| F11 | factual | verified | sql | yes |
| F12 | factual | verified | sql | yes |
| F13 | factual | verified | sql | yes |
| F14 | factual | verified | sql | yes |
| N01 | numerical | verified | sql_calculation | yes |
| N02 | numerical | verified | sql_calculation | yes |
| N03 | numerical | verified | sql_calculation | yes |
| N04 | numerical | verified | sql | yes |
| N05 | numerical | verified | sql | yes |
| N06 | numerical | verified | sql | yes |
| N07 | numerical | verified | sql_calculation | yes |
| N08 | numerical | verified | sql_calculation | yes |
| N09 | numerical | verified | sql_calculation | yes |
| C01 | comparison | verified | comparison | yes |
| C02 | comparison | verified | comparison | yes |
| C03 | comparison | verified | comparison | yes |
| C04 | comparison | verified | comparison | yes |
| C05 | comparison | verified | comparison | yes |
| C06 | comparison | verified | comparison | yes |
| M01 | multi_hop | verified | sql_and_document | yes |
| M02 | multi_hop | verified | sql_and_document | yes |
| M03 | multi_hop | verified | sql_and_document | yes |
| M04 | multi_hop | verified | sql_and_document | yes |
| M05 | multi_hop | verified | sql_and_document | yes |
| M06 | multi_hop | verified | sql_and_document | yes |
| D01 | document | verified | document | yes |
| D02 | document | verified | document | yes |
| D03 | document | verified | document | yes |
| D04 | document | verified | document | yes |
| D05 | document | verified | document | yes |
| D06 | document | verified | document | no |
| D07 | document | verified | document | yes |
| D08 | document | verified | document | yes |
| D09 | document | verified | document | yes |
| D10 | document | verified | document | yes |
| D11 | document | verified | document | yes |
| D12 | document | verified | document | yes |
| A01 | ambiguous | ambiguous | clarify | yes |
| A02 | ambiguous | ambiguous | clarify | yes |
| A03 | ambiguous | ambiguous | clarify | yes |
| A04 | ambiguous | ambiguous | clarify | yes |
| A05 | ambiguous | ambiguous | evidence | yes |
| U01 | unanswerable | refused | refuse | yes |
| U02 | unanswerable | insufficient_evidence | sql | yes |
| U03 | unanswerable | refused | refuse | yes |
| U04 | unanswerable | insufficient_evidence | sql | yes |
| U05 | unanswerable | refused | refuse | yes |
| U06 | unanswerable | refused | refuse | yes |
| U07 | unanswerable | insufficient_evidence | document | yes |
| X01 | adversarial | refused | refuse | yes |
| X02 | adversarial | refused | refuse | yes |
| X03 | adversarial | refused | refuse | yes |
| X04 | adversarial | refused | refuse | yes |
| X05 | adversarial | refused | refuse | yes |
| X06 | adversarial | verified | sql | yes |
| X07 | adversarial | verified | sql | yes |
| X08 | adversarial | refused | refuse | yes |
| R01 | comparison | verified | comparison | yes |
| R02 | numerical | verified | sql_calculation | yes |
| R03 | factual | verified | sql | yes |
| R04 | factual | verified | sql | yes |
| R05 | numerical | verified | sql_calculation | yes |

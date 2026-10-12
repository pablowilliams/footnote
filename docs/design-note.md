# Design note: routing, verification and where the model stops

This note explains the decisions that shape Footnote and the failure modes each one is meant to close. It is written for someone deciding whether to trust the system, or whether to extend it.

## 1. The model does not do arithmetic or state figures

The most common way a financial assistant goes wrong is not a wild hallucination. It is a plausible number: a figure rounded twice, a margin computed from the wrong year's revenue, a growth rate calculated in the model's head. Footnote removes the model from that path entirely. Figures are read from the database. Changes, margins, compound growth and free cash flow are computed in `calc.py`. Every figure is written through one formatter, so the verifier can compare the text of an answer against the exact set of strings the evidence allows.

By default no language model is involved at all, and the evaluation results are for that configuration. The optional Claude narrator receives only the computed sentences, already containing their figures and citation markers, and is asked to improve the prose. Its output goes back through the same checks; if it adds, drops or alters a figure, the template text is used instead. Quotations from filings are never passed to it.

## 2. Routing is a rule, not a model

Every question is routed before any retrieval happens:

| Question | Route | Why |
|---|---|---|
| "What was revenue in 2024?" | structured query | A figure exists as a row; retrieving prose to find it adds a way to be wrong |
| "How did revenue change?" | structured query and calculation | The change is computed, not read |
| "Compare A and B" | multi-company query | Ranking needs the same metric and fiscal year for every company |
| "Why did margins decline?" | structured query and document search | The numbers come from the statements; the explanation comes from Item 7 |
| "What risks did X disclose?" | document search | There is no figure to compute |
| No company, or "profit" without saying which | clarification | Guessing which company or which profit is a silent error |
| Another company, a forecast, advice, a currency conversion, instructions to the system, a database command | refusal | The corpus cannot support an answer |

The router is deterministic for three reasons. A routing mistake is the most expensive mistake in the pipeline, because everything downstream is correct for the wrong question. A rule that misfires can be reproduced with one line of test and fixed. And the router is cheap: it runs in under a millisecond, so a structured answer returns in single-digit milliseconds.

The cost is coverage of phrasing. The parser only knows the words it has been given. "What drove Nvidia's growth?" was misrouted in the first evaluation run because "drove" was not on the list of explanation words, and "Did Apple's net income go up?" was answered as a single figure because "go up" was not on the list of change words. Both were found, fixed and added as regression tests. A model-based classifier would handle more phrasings and fail less predictably; this design accepts the first cost to avoid the second.

## 3. Questions with more than one reasonable reading are not guessed

"Profit" can mean operating income or net income; "cash flow" can mean operating or free cash flow; "earnings" can mean net income or earnings per share. Rather than choose, the system asks, and the interface offers the options as buttons. The same applies to a question with no company. This costs a round trip on a small share of questions and prevents a correct-looking answer to a different question.

## 4. Verification decides what is released

Each check is either blocking or advisory.

**Blocking** (the answer is withheld and the status is `insufficient_evidence`):

- every citation marker resolves, and every sentence that states a figure carries one;
- every figure in a computed sentence is a source value or a calculation result, exactly as formatted;
- every quotation appears word for word in the passage it cites;
- every calculation reproduces from its cited inputs;
- units are consistent, and a change is never computed across different units;
- every cited figure belongs to a fiscal year the question asked about;
- words that describe a change ("rose", "fell") agree with the sign of the computed change.

**Advisory** (the answer is released as `partially_verified`):

- a requested figure is missing (for example, one company did not report the metric that year);
- the best passage is only weakly similar to the question;
- a figure was read from text with a low-confidence pattern.

The statuses are `verified`, `partially_verified`, `ambiguous`, `insufficient_evidence` and `refused`. An honest status is part of the answer.

## 5. What "verified" does and does not mean

"Verified" means every figure traces to a filing, every calculation reproduces, and every quotation is exact. It does not mean the quotations are the best possible answer to the question. A sentence about AWS segment costs is a verbatim, correctly cited sentence from Amazon's annual report, and it is not an answer to "who are AWS's competitors?". The verifier cannot tell the difference; the evaluation can, and that question is the one still marked wrong. See the [failure analysis](failure-analysis.md).

## 6. Fiscal years are the company's own

Apple's fiscal year ends in September, Microsoft's in June, Nvidia's in January. "2024" in a question means each company's fiscal 2024, which is how the companies themselves report. When a comparison spans companies whose years end in different months, the answer says so. When a comparison names different years for different companies, each company's figure is stated for each year rather than ranked, so neither year is silently dropped.

## 7. Hybrid retrieval, and what the evaluation said about it

Search runs dense vectors and BM25 over the same filtered passages and fuses the rankings. The expectation was that BM25 would rescue exact terms ("tariffs", "CHIPS Act") that the small embedding model blurs. On this evaluation set it did not help: dense search alone placed a relevant passage first for every question, while the hybrid did so for 94%, because BM25 occasionally promotes a passage that repeats the query terms without discussing the topic. The hybrid is kept because the set is small (18 retrieval questions) and is filtered to one company, year and section, which favours dense search; the result is reported rather than tuned away, and the retriever is a one-word switch (`mode=`) if a larger set confirms it.

## 8. Failure modes this design does not close

- Phrasings the parser has not seen are misrouted or treated as ambiguous.
- A quotation can be exact and still off-topic.
- Generic questions ("which risks did Nvidia mention?") draw generic sentences, because there is no topic to search for.
- Coverage is fifteen companies, ten metrics and four sections. Anything else is refused, which is correct but limiting.
- Headcount is read from text and depends on how each company words it; two companies (Coca-Cola and Caterpillar) have no reliable worldwide figure and the system reports none rather than a wrong one.

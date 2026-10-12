# Failure analysis

Where Footnote is wrong, weak or untested, with the evidence for each. Faults that have been fixed are listed with what fixed them, because the record of what went wrong is as useful as the final score.

## Still wrong

### An exact, cited quotation that does not answer the question (D06)

**Question:** "Who does Amazon identify as competitors for AWS?"

**What happened:** The answer quoted four verbatim sentences from Amazon's fiscal 2025 annual report about AWS: what it offers, how its costs are classified, and that its sales grew 20%. Every quotation was exact and correctly cited, so every check passed and the answer was marked verified. None of the sentences names a competitor.

**Why:** Amazon's 10-K discusses competition in general terms ("our businesses are rapidly evolving and intensely competitive") rather than naming AWS competitors, so the passages most similar to the question are the ones that mention AWS most. The verifier checks that a quotation is faithful to its source; it does not check that it answers the question.

**What would fix it:** A relevance check between each selected sentence and the question, with a threshold below which the answer is downgraded to partially verified or withheld. A cross-encoder would do this better than the embedding model already in use. It has not been added because a threshold tuned on one example would be a guess.

### Generic risk questions draw generic sentences

"Which risks did Nvidia mention in its latest annual report?" has no topic to search for. The system substitutes a query for material risks to the business, which finds real risk statements but favours the most general ones ("Business disruptions could harm our operations and financial results"). Boilerplate is filtered ("risks not presently known", "the following risks"), but the answer is still less useful than a list of the risk-factor headings would be. Scored as correct, because the quotations are real risks from Item 1A; it is listed here because it is weak.

## Fixed after the first run

The first full run, on 67 questions, scored 95.5%. Three questions were wrong:

| Question | Fault | Fix |
|---|---|---|
| "What drove Nvidia's revenue growth in fiscal 2025?" | "drove" was not recognised as asking for an explanation, so the question got figures without the company's explanation | Added "drove", "drive", "caused", "contributed" to the explanation words |
| "What does Microsoft say about artificial intelligence in its 2025 annual report?" | Withheld as insufficient evidence. The query sent to search was "What does say about artificial intelligence in its ?", and the question scaffolding diluted the embedding below the similarity threshold | Question words ("what does ... say about", "in its") are now removed before searching |
| "Who does Amazon identify as competitors for AWS?" | Off-topic quotations | Not fixed; see above |

Probing edge cases by hand afterwards found four more faults, now fixed and added to the evaluation as R01 to R05:

| Question | Fault | Fix |
|---|---|---|
| "Compare Apple's 2024 revenue with Alphabet's 2023 revenue" | **Silently wrong.** The comparison ranked both companies on 2024 and dropped 2023, and was marked verified, because every figure it did state was correct | A comparison naming more than one year now states each company's figure for each year instead of ranking |
| "Did Apple's net income go up in fiscal 2024?" | Answered with the 2024 figure only; "go up" was not recognised as asking about a change | Added "go up", "went down", "improve", "worsen", "over the last" |
| "How did Costco's revenue change over the last five years?" | Compared the last two years only | "Last N years" now sets the start of the range |
| "How many people did Amazon employ in 2024?" | Routed to document search; "employ" was not a headcount synonym | Added "employ" and "employed" |

Two ingestion faults were found while checking the extracted headcounts against the sentences they came from:

- **Coca-Cola's headcount was recorded as 7,600**, read from "we had approximately 7,600 employees located in the United States, excluding the employees of our consolidated bottlers", a high-confidence pattern. A count qualified by country or by exclusion is now recognised as a subset and is not reported as the company total. Coca-Cola now has no headcount figure, which is correct: its 10-K gives no worldwide total in Item 1.
- **Alphabet, Amazon and Procter & Gamble had no headcount**, because the patterns expected "we" or "the company" as the subject ("Alphabet had 183,323 employees") and did not allow "full-time and part-time employees". Both are now handled.
- **The first version of the subset rule then removed Adobe's headcount**, because "we employed 31,360 people, of which 50% were in the United States" contains "in the United States" within a few words of the count. The rule now requires the qualifier to follow the count directly. Thirteen of fifteen companies have a headcount; Coca-Cola and Caterpillar do not give a worldwide total in a form the patterns recognise.

The silently wrong comparison is the most important finding in this document. Every check passed because the verifier confirms that what is stated is supported, and the fault was in what was left out. The `complete` check catches a requested figure that is missing from the data, but not one the answer builder never asked for.

## Data quality

| Check | Result |
|---|---|
| XBRL figures also found, as printed, in a parsed filing | 498 of 513 (97.1%) |
| Of those, found under the heading of the right financial statement | 493 (99.0%) |
| Figures that could not be checked (no parsed filing covers the year) | 40, all fiscal 2021 |
| Values restated in a later filing | 9, later value used, original kept |
| Sections found, of 4 per filing | 170 of 180 |
| Missing sections | Item 7A for PepsiCo and Procter & Gamble (a cross-reference to Item 7, correctly absent); Item 7A for all three Intel reports and Item 1 for Intel's fiscal 2025 report (Intel's non-standard layout) |

All fifteen figures not found in the text are long-term debt for fiscal 2021 and 2022, where the XBRL value is a total that the balance sheet prints as separate current and non-current lines rather than as one figure.

## Limits of the evaluation itself

- **The questions and gold answers were written by the person who built the system.** Questions were written before the system was run against them, and gold figures were entered by hand from the published statements rather than copied from the database; all 36 matched. But the author knows what the parser handles, and a question set written by someone else would very likely score lower. The five regression questions were added after seeing failures and so measure the fixes, not the system's general ability.
- **72 questions is a small set.** One question is 1.4 percentage points.
- **Retrieval relevance is judged by a pattern.** A passage counts as relevant if it contains the expected topic word. That is generous: D06 shows a passage can mention AWS without answering the question.
- **Latency was measured on a laptop** with a warm cache and a local database. The first document question after start-up takes about ten seconds while the embedding model and the BM25 index load; the API does this at start-up for that reason.
- **Browser latency is higher for document questions.** In the browser build, a question that searches the text takes one to three seconds, because the candidate sentences are embedded in JavaScript on a single thread; structured questions take a few milliseconds. The first visit downloads about 120 MB.
- **The Claude narrator is not covered by the evaluation.** No API key was available when the evaluation was run, so all reported results use the template narrator. The narrator's safety rests on the same verifier, which is tested, but its effect on answer quality is unmeasured.

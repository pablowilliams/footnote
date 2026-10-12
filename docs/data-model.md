# Data model

```mermaid
erDiagram
    company ||--o{ filing : files
    company ||--o{ metric_value : reports
    company ||--o{ xbrl_fact : tagged
    filing ||--o{ section : contains
    section ||--o{ chunk : "split into"
    filing ||--o{ metric_value : "latest source of"
    metric ||--o{ metric_value : defines

    company {
        int company_id PK
        text ticker UK
        int cik UK
        text name
    }
    filing {
        int filing_id PK
        int company_id FK
        text accession UK "SEC accession number"
        text form "10-K"
        int fiscal_year
        date period_end
        date filed
        text url "primary document on sec.gov"
        bool has_text "text parsed and searchable"
        int page_count
    }
    section {
        int section_id PK
        int filing_id FK
        text item "1, 1A, 7 or 7A"
        text title
        text start_page "printed page"
        text end_page
        int word_count
    }
    chunk {
        int chunk_id PK
        int section_id FK
        int filing_id FK
        int company_id FK
        int fiscal_year
        text item
        int ordinal
        text page "printed page where the passage starts"
        text text
        text content_hash "deduplication and embedding cache key"
        vector embedding "384 dimensions, HNSW cosine index"
    }
    metric {
        text metric_key PK
        text label
        text unit "USD, USD/share, ratio or people"
        text kind "duration or instant"
        text source "xbrl, derived or text"
    }
    xbrl_fact {
        bigint fact_id PK
        int company_id FK
        text tag "us-gaap tag"
        date period_start
        date period_end
        numeric value
        text accession "filing that reported it"
        date filed
        int fy "fiscal year of the reporting filing"
    }
    metric_value {
        int metric_value_id PK
        int company_id FK
        text metric_key FK
        int fiscal_year "the period's own fiscal year"
        date period_end
        numeric value
        text source_kind "xbrl, derived or text"
        int filing_id FK "latest filing that reported it"
        text xbrl_tag
        numeric extraction_confidence
        bool found_in_document "printed in the filing text"
        numeric restated_from "original value if later restated"
        jsonb derivation "formula and inputs, for derived values"
        jsonb evidence "page, statement and filing where it is printed"
    }
    answer_log {
        uuid answer_id PK
        timestamptz created_at
        text question
        text route
        text status
        int latency_ms
        jsonb response "the full answer, for evidence follow-ups"
    }
```

## How a citation is assembled

A figure in an answer comes from one `metric_value` row. Its citation names the filing where the figure is printed (from `evidence.doc_accession`), the statement it sits in (`evidence.location`) and the printed page (`evidence.page`), and links to the document on sec.gov. Figures printed on the same page of the same report share one citation. A derived figure is cited through its inputs. A figure read from text (headcount) carries the sentence it was read from.

A quotation comes from one `chunk`. Its citation names the filing, the item and the page the passage starts on, and the verifier confirms the quoted sentence appears in that passage word for word.

## Grain and keys

- `metric_value` holds exactly one row per company, metric and fiscal year (`UNIQUE (company_id, metric_key, fiscal_year)`). Every answer about a figure reads this table.
- `xbrl_fact` holds every annual fact the SEC has for the tags in use, including values later restated, so any selection in `metric_value` can be audited back to the raw filings.
- `chunk.content_hash` is a hash of the normalised text. It removes duplicates within a filing and keys the embedding cache.

## Access

Generated SQL runs under the `footnote_reader` role, which can read `company`, `filing`, `metric` and `metric_value` and nothing else. It cannot see `answer_log` or the passage text, and it cannot write.

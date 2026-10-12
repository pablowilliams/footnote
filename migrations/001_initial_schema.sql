-- Footnote schema, version 1.
-- Every answer the system gives must be traceable to a row here, and every row
-- here must be traceable to an SEC filing.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE company (
    company_id  serial PRIMARY KEY,
    ticker      text NOT NULL UNIQUE,
    cik         integer NOT NULL UNIQUE,
    name        text NOT NULL
);

CREATE TABLE filing (
    filing_id         serial PRIMARY KEY,
    company_id        integer NOT NULL REFERENCES company,
    accession         text NOT NULL UNIQUE,
    form              text NOT NULL,
    fiscal_year       integer NOT NULL,
    period_end        date NOT NULL,
    filed             date NOT NULL,
    primary_document  text NOT NULL,
    url               text NOT NULL,
    has_text          boolean NOT NULL DEFAULT false,
    page_count        integer
);
CREATE INDEX filing_company_year ON filing (company_id, fiscal_year);

CREATE TABLE section (
    section_id   serial PRIMARY KEY,
    filing_id    integer NOT NULL REFERENCES filing ON DELETE CASCADE,
    item         text NOT NULL,
    title        text NOT NULL,
    start_page   text,
    end_page     text,
    word_count   integer NOT NULL,
    UNIQUE (filing_id, item)
);

CREATE TABLE chunk (
    chunk_id      serial PRIMARY KEY,
    section_id    integer NOT NULL REFERENCES section ON DELETE CASCADE,
    filing_id     integer NOT NULL REFERENCES filing ON DELETE CASCADE,
    company_id    integer NOT NULL REFERENCES company,
    fiscal_year   integer NOT NULL,
    item          text NOT NULL,
    ordinal       integer NOT NULL,
    page          text,
    text          text NOT NULL,
    content_hash  text NOT NULL,
    embedding     vector(384)
);
CREATE INDEX chunk_filter ON chunk (company_id, fiscal_year, item);
CREATE INDEX chunk_embedding ON chunk USING hnsw (embedding vector_cosine_ops);

CREATE TABLE metric (
    metric_key   text PRIMARY KEY,
    label        text NOT NULL,
    unit         text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('duration', 'instant')),
    source       text NOT NULL CHECK (source IN ('xbrl', 'derived', 'text'))
);

-- Every annual fact the SEC holds for the tags we use, including values later restated.
CREATE TABLE xbrl_fact (
    fact_id       bigserial PRIMARY KEY,
    company_id    integer NOT NULL REFERENCES company,
    tag           text NOT NULL,
    unit          text NOT NULL,
    period_start  date,
    period_end    date NOT NULL,
    value         numeric NOT NULL,
    accession     text NOT NULL,
    form          text NOT NULL,
    filed         date NOT NULL,
    fy            integer,
    fp            text,
    frame         text
);
CREATE INDEX xbrl_fact_lookup ON xbrl_fact (company_id, tag, period_end);

-- The one value per company, metric and fiscal year that answers are computed from.
CREATE TABLE metric_value (
    metric_value_id        serial PRIMARY KEY,
    company_id             integer NOT NULL REFERENCES company,
    metric_key             text NOT NULL REFERENCES metric,
    fiscal_year            integer NOT NULL,
    period_start           date,
    period_end             date NOT NULL,
    value                  numeric NOT NULL,
    unit                   text NOT NULL,
    source_kind            text NOT NULL CHECK (source_kind IN ('xbrl', 'derived', 'text')),
    filing_id              integer REFERENCES filing,
    xbrl_tag               text,
    extraction_confidence  numeric NOT NULL DEFAULT 1.0,
    found_in_document      boolean,
    restated_from          numeric,
    derivation             jsonb,
    evidence               jsonb,
    UNIQUE (company_id, metric_key, fiscal_year)
);

CREATE TABLE answer_log (
    answer_id    uuid PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    question     text NOT NULL,
    route        text NOT NULL,
    status       text NOT NULL,
    latency_ms   integer NOT NULL,
    response     jsonb NOT NULL
);
CREATE INDEX answer_log_created ON answer_log (created_at DESC);

-- A read-only role for the SQL analytics path. Generated queries run under it, so
-- even a query that slipped past validation could not change data.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'footnote_reader') THEN
        CREATE ROLE footnote_reader NOLOGIN;
    END IF;
END $$;
GRANT SELECT ON company, filing, metric, metric_value TO footnote_reader;

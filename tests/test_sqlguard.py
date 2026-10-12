"""SQL generation safety: only one bounded, read-only SELECT over allowed tables passes."""

import pytest

from footnote.analytics import _COVERAGE_SQL, _METRIC_SQL
from footnote.sqlguard import UnsafeQuery, validate


def test_the_queries_the_system_actually_runs_pass():
    validate(_METRIC_SQL)
    validate(_COVERAGE_SQL)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE metric_value",
        "DELETE FROM metric_value WHERE true",
        "UPDATE metric_value SET value = 0 LIMIT 1",
        "INSERT INTO company (ticker) VALUES ('X')",
        "SELECT * FROM metric_value LIMIT 10; DROP TABLE company",
        "SELECT * FROM pg_shadow LIMIT 1",
        "SELECT * FROM answer_log LIMIT 1",
        "SELECT * FROM chunk LIMIT 1",
        "SELECT value FROM metric_value",
        "SELECT value FROM metric_value LIMIT 100000",
        "SELECT pg_sleep(10) FROM metric_value LIMIT 1",
        "SELECT pg_read_file('/etc/passwd') FROM company LIMIT 1",
        "SELECT * INTO stolen FROM metric_value LIMIT 1",
        "COPY metric_value TO '/tmp/x'",
        "not sql at all",
    ],
)
def test_unsafe_or_unbounded_queries_are_rejected(sql):
    with pytest.raises(UnsafeQuery):
        validate(sql)


def test_a_cte_over_allowed_tables_is_accepted():
    validate("WITH v AS (SELECT value FROM metric_value) SELECT * FROM v LIMIT 5")

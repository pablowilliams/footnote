"""Validate generated SQL before it reaches the database.

The analytics path builds SQL from a structured plan rather than free text, so in
normal operation every query passes. The guard exists for the day that changes:
it parses the statement and rejects anything that is not a single, bounded,
read-only SELECT over the four tables the analytics path is allowed to see. The
query then runs under a role that can only read those tables, so a query that
slipped past this check would still fail.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"metric_value", "company", "filing", "metric"}
DENIED_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "lo_import",
    "lo_export",
    "dblink",
    "set_config",
    "current_setting",
    "pg_terminate_backend",
}
MAX_LIMIT = 500


class UnsafeQuery(ValueError):
    pass


def validate(sql: str, dialect: str = "postgres") -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as err:
        raise UnsafeQuery(f"could not parse: {err}") from err
    if len(statements) != 1:
        raise UnsafeQuery("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise UnsafeQuery(f"only SELECT is allowed, got {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Into)):
            raise UnsafeQuery(f"{node.key.upper()} is not allowed")
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table)} - ctes
    if not tables:
        raise UnsafeQuery("the query reads no table")
    unknown = tables - ALLOWED_TABLES
    if unknown:
        raise UnsafeQuery(f"table not allowed: {', '.join(sorted(unknown))}")
    for fn in tree.find_all(exp.Anonymous, exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in DENIED_FUNCTIONS:
            raise UnsafeQuery(f"function not allowed: {name}")
    limit = tree.args.get("limit")
    if limit is None:
        raise UnsafeQuery("a LIMIT is required")
    try:
        if int(limit.expression.name) > MAX_LIMIT:
            raise UnsafeQuery(f"LIMIT may not exceed {MAX_LIMIT}")
    except (AttributeError, ValueError) as err:
        raise UnsafeQuery("LIMIT must be a literal number") from err
    return tree

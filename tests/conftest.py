"""Shared fixtures. Tests that need data use the database named by FOOTNOTE_DATABASE_URL,
loaded from the committed snapshot; they are skipped when no database is reachable."""

from __future__ import annotations

import os

import pytest


def _db_ready() -> bool:
    try:
        from sqlalchemy import text

        from footnote.db import connect

        with connect() as conn:
            return conn.execute(text("SELECT count(*) FROM metric_value")).scalar_one() > 0
    except Exception:
        return False


@pytest.fixture(scope="session")
def db() -> None:
    if os.environ.get("FOOTNOTE_SKIP_DB") or not _db_ready():
        pytest.skip("database with the snapshot loaded is not available")

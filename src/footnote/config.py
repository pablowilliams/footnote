"""Runtime settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("FOOTNOTE_DATABASE_URL", "postgresql+psycopg://localhost:5432/footnote")
    )
    # The SEC asks every automated client to identify itself with a contact address.
    sec_user_agent: str = field(
        default_factory=lambda: os.environ.get("FOOTNOTE_SEC_USER_AGENT", "Footnote research project pablowilliams119@gmail.com")
    )
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FOOTNOTE_DATA_DIR", ROOT / "data")))
    embedding_model: str = field(
        default_factory=lambda: os.environ.get("FOOTNOTE_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    )
    embedding_dim: int = 384
    narrator: str = field(default_factory=lambda: os.environ.get("FOOTNOTE_NARRATOR", "template"))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.environ.get("FOOTNOTE_RATE_LIMIT", "30")))
    first_fiscal_year: int = 2021
    text_filings_per_company: int = 3

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshot"


settings = Settings()

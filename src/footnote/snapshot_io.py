"""Read and write the snapshot's JSON Lines files. No dependencies beyond the standard library."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(type(obj))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8") as fh:  # type: ignore[operator]
        for row in rows:
            fh.write(json.dumps(row, default=_jsonable, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        return [json.loads(line) for line in fh if line.strip()]

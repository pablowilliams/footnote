#!/bin/sh
# Apply migrations and, on first start, load the committed snapshot. Both steps are idempotent.
set -e
python -m footnote.cli migrate
if ! python - <<'PY'
from sqlalchemy import text
from footnote.db import connect
with connect() as c:
    raise SystemExit(0 if c.execute(text("SELECT count(*) FROM chunk WHERE embedding IS NOT NULL")).scalar_one() else 1)
PY
then
  python -m footnote.cli load
fi
exec "$@"

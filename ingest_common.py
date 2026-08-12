"""Shared ingestion helpers for the backfill/fetcher scripts.

Every backfill script writes rows into the same `match_results` table, so the
ID derivation and the upsert call belong in one place. Two rules matter:

1. **Stable IDs.** ``api_football_id`` is the upsert conflict key. It must be
   identical every run for the same match, otherwise re-running a backfill
   inserts duplicate history instead of updating it. Python's builtin ``hash``
   is salted per process (``PYTHONHASHSEED``), so it is unsafe here — we use an
   md5 digest of ``date_home_away`` instead.

2. **Chunked upserts.** Supabase rejects very large payloads, so writes are
   split into fixed-size batches. Provider/database failures are logged and the
   caller keeps going with the next league.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

UPSERT_CHUNK = 500
CONFLICT_KEY = "api_football_id"


def stable_id(date_str: str, home: str, away: str) -> int:
    """Deterministic positive int ID for a match.

    md5 (not ``hash``) so the same fixture maps to the same ID on every run and
    upserts collapse onto the existing row instead of duplicating it.
    """
    key = f"{date_str}_{home}_{away}".strip().lower()
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % (10 ** 9)


def build_row(
    date_str: str,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    league: str,
    **extra: Any,
) -> dict[str, Any]:
    """Normalize one match into the `match_results` schema.

    ``extra`` carries optional stat columns (home_shots, away_shots, ...); None
    values are kept so the column is explicitly cleared rather than skipped.
    """
    row: dict[str, Any] = {
        CONFLICT_KEY: stable_id(date_str, home, away),
        "home_team": str(home),
        "away_team": str(away),
        "home_score": int(home_score),
        "away_score": int(away_score),
        "match_date": date_str,
        "league": league,
    }
    row.update(extra)
    return row


def upsert_matches(supabase, rows: Iterable[dict[str, Any]], label: str = "") -> int:
    """Chunked upsert onto the conflict key. Returns number of rows written.

    Failures are logged (not raised) so one bad batch does not abort the run.
    """
    rows = list(rows)
    if not rows:
        return 0
    written = 0
    for i in range(0, len(rows), UPSERT_CHUNK):
        batch = rows[i:i + UPSERT_CHUNK]
        try:
            supabase.table("match_results").upsert(
                batch, on_conflict=CONFLICT_KEY
            ).execute()
            written += len(batch)
        except Exception as exc:  # keep going with the next batch
            logger.warning("⚠️ Upsert failed for %s (batch %d): %s", label, i, exc)
    return written

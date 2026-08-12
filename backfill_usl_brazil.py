"""Dedicated backfill for competitions the other scripts miss.

OpenFootball has no USL or lower-tier Brazilian data. This fetcher pulls those
competitions straight from their SofaScore unique-tournament feeds using
``/events/last/{page}`` (the working replacement for the retired
``scheduled-events/{date}`` endpoint), season by season.

Each match date comes from the event's own ``startTimestamp`` and rows normalize
to ``match_results`` through ``ingest_common`` so IDs stay stable across runs and
re-runs upsert instead of duplicating history. Provider failures for one
tournament/season are logged and the walk continues with the next.

Usage:
    python backfill_usl_brazil.py                 # last 3 seasons of each comp
    python backfill_usl_brazil.py --seasons 5     # deeper history
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))
from predictionbot.sources.sofascore import SofascoreClient

from ingest_common import build_row, upsert_matches

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# SofaScore unique-tournament IDs -> clean league label persisted to
# `match_results`. IDs verified against the live search endpoint 2026-08-07.
TARGET_TOURNAMENTS = {
    13363: "USL Championship",
    13362: "USL League One",
    390: "Brazil Serie B",
    1281: "Brazil Serie C",
    10326: "Brazil Serie D",
}

# Hard stop on pagination so a misbehaving feed can never loop forever.
MAX_PAGES = 20


def _event_date(fixture) -> str | None:
    """ISO date (YYYY-MM-DD) from the event's start timestamp, or None."""
    ts = fixture.raw.get("startTimestamp")
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _finished_rows(client: SofascoreClient, tournament_id: int, season_id: int, label: str) -> list[dict]:
    """Walk every `events/last` page for one season, collecting finished games."""
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        try:
            fixtures = client.tournament_events_last(tournament_id, season_id, page)
        except Exception as exc:
            # 404 is the normal end-of-pagination signal; anything else is logged
            # and also ends this season so one bad page can't abort the run.
            if "404" not in str(exc):
                logger.warning("⚠️ %s season %s page %d: %s", label, season_id, page, exc)
            break
        if not fixtures:
            break

        for fx in fixtures:
            raw = fx.raw
            if raw.get("status", {}).get("type") != "finished":
                continue
            home_score = (raw.get("homeScore") or {}).get("current")
            away_score = (raw.get("awayScore") or {}).get("current")
            date_str = _event_date(fx)
            if home_score is None or away_score is None or not date_str:
                continue
            rows.append(build_row(
                date_str,
                fx.home.name,
                fx.away.name,
                int(home_score),
                int(away_score),
                label,
                home_shots=None,
                away_shots=None,
            ))
        time.sleep(1.0)  # courtesy delay between pages
    return rows


def backfill_usl_brazil(seasons_back: int = 3) -> int:
    load_dotenv()

    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_KEY in .env file.")
        return 0

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = SofascoreClient()

    logger.info(
        "🚀 Starting USL + lower Brazilian tier backfill (%d seasons each)...",
        seasons_back,
    )
    total_inserted = 0

    for tournament_id, label in TARGET_TOURNAMENTS.items():
        try:
            seasons = client.unique_tournament_seasons(tournament_id)
        except Exception as exc:
            logger.warning("⚠️ Could not list seasons for %s: %s", label, exc)
            continue

        # `/seasons` is newest-first; keep the most recent N.
        for season in seasons[:seasons_back]:
            season_id = season.get("id")
            year = season.get("year")
            if season_id is None:
                continue
            logger.info("📚 Fetching %s (%s)...", label, year)
            rows = _finished_rows(client, tournament_id, season_id, label)
            inserted = upsert_matches(supabase, rows, f"{label} {year}")
            total_inserted += inserted
            if inserted:
                logger.info("✅ Inserted %d matches for %s (%s).", inserted, label, year)

    logger.info("🎉 USL/Brazil Backfill Complete! Total added: %d", total_inserted)
    return total_inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill USL and lower Brazilian tiers")
    parser.add_argument(
        "--seasons", type=int, default=3,
        help="How many recent seasons of each competition to pull",
    )
    args = parser.parse_args()
    if args.seasons < 1:
        parser.error("--seasons must be at least 1")
    backfill_usl_brazil(args.seasons)

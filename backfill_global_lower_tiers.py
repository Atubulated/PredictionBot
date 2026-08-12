import os
import re
import logging
import hashlib

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VERIFIED SOURCE FILES (checked against GitHub 2026-08-03).
# These leagues are published as plain-text "football.txt" files, NOT JSON:
#   NL 2nd tier : openfootball/europe      -> netherlands/2023-24_nl2.txt
#   PT 2nd tier : openfootball/europe      -> portugal/2023-24_pt2.txt
#   DE 3rd tier : openfootball/deutschland -> 2023-24/3-liga3.txt
# ---------------------------------------------------------------------------
SEASONS = ["2023-24", "2024-25"]

SOURCES = {
    "eerste_divisie": {
        "label": "Eerste Divisie",
        "url": "https://raw.githubusercontent.com/openfootball/europe/master/netherlands/{season}_nl2.txt",
    },
    "liga_portugal_2": {
        "label": "Liga Portugal 2",
        "url": "https://raw.githubusercontent.com/openfootball/europe/master/portugal/{season}_pt2.txt",
    },
    "liga_3": {
        "label": "Liga 3",
        "url": "https://raw.githubusercontent.com/openfootball/deutschland/master/{season}/3-liga3.txt",
    },
}

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# e.g. "Fri Aug 4", "Sat Jan 6 2024"
DATE_RE = re.compile(
    r'^\[?(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})'
    r'(?:\s+(\d{4}))?\]?$'
)
# europe style:  "FC Den Bosch v TOP Oss 1-0 (0-0)"
MATCH_V_RE = re.compile(r'^(.+?)\s+v\s+(.+?)\s+(\d+)\s*-\s*(\d+)\s*(?:\([^)]*\))?$')
# deutschland style: "Hallescher FC 2-1 (2-0) Rot-Weiss Essen"
MATCH_SCORE_RE = re.compile(r'^(.+?)\s+(\d+)\s*-\s*(\d+)\s*(?:\([^)]*\)\s*)?(.+)$')


def stable_id(date_str, home, away):
    # md5 instead of hash() -> same ID on every run, so re-runs upsert, not duplicate
    key = f"{date_str}_{home}_{away}".strip().lower()
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % (10 ** 9)


def parse_fixtures(text, season):
    start_year = int(season.split("-")[0])
    end_year = 2000 + int(season.split("-")[1])

    current_date = None
    rows = []

    for raw_line in text.splitlines():
        line = raw_line.replace("\\.", ".").strip()
        if not line or line[0] in "#=▪*•":
            continue

        dm = DATE_RE.match(line)
        if dm:
            month = MONTHS[dm.group(1)]
            day = int(dm.group(2))
            # year given on line, or infer: Jul-Dec = start year, Jan-Jun = end year
            year = int(dm.group(3)) if dm.group(3) else (start_year if month >= 7 else end_year)
            current_date = f"{year:04d}-{month:02d}-{day:02d}"
            continue

        if current_date is None:
            continue

        core = re.sub(r'^\d{1,2}:\d{2}\s+', '', line)  # strip kick-off time

        home = away = None
        hs = as_ = None

        mv = MATCH_V_RE.match(core)
        if mv:
            home, away = mv.group(1).strip(), mv.group(2).strip()
            hs, as_ = int(mv.group(3)), int(mv.group(4))
        else:
            ms = MATCH_SCORE_RE.match(core)
            if ms:
                home, away = ms.group(1).strip(), ms.group(4).strip()
                hs, as_ = int(ms.group(2)), int(ms.group(3))

        if home and away and hs is not None and as_ is not None:
            rows.append({"date": current_date, "home": home, "away": away,
                         "home_score": hs, "away_score": as_})
    return rows


def backfill_global_tiers():
    load_dotenv()

    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing Supabase credentials in .env")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    logger.info("🌍 Starting absolute-coverage global lower tier backfill...")
    total_inserted = 0

    for league_name, cfg in SOURCES.items():
        for season in SEASONS:
            url = cfg["url"].format(season=season)
            logger.info(f"📚 Fetching {league_name} ({season}) from {url}...")

            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code != 200:
                    logger.warning(f"⚠️ HTTP {resp.status_code} for {league_name} ({season})")
                    continue

                text = resp.content.decode("utf-8", errors="replace")
                parsed = parse_fixtures(text, season)

                if not parsed:
                    logger.warning(f"⚠️ No matches parsed for {league_name} ({season})")
                    continue

                rows = [{
                    "api_football_id": stable_id(m["date"], m["home"], m["away"]),
                    "home_team": m["home"],
                    "away_team": m["away"],
                    "home_score": m["home_score"],
                    "away_score": m["away_score"],
                    "match_date": m["date"],
                    "league": cfg["label"],
                } for m in parsed]

                for i in range(0, len(rows), 500):  # chunked upsert
                    supabase.table("match_results").upsert(
                        rows[i:i + 500], on_conflict="api_football_id"
                    ).execute()

                total_inserted += len(rows)
                logger.info(f"✅ Inserted {len(rows)} matches for {league_name} ({season}).")

            except Exception as e:
                logger.warning(f"⚠️ Error processing {league_name} ({season}): {e}")

    logger.info(f"🎉 Global Lower Tier Backfill Complete! Total added: {total_inserted}")


if __name__ == "__main__":
    backfill_global_tiers()
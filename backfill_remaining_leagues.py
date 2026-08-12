import os
import sys
import logging
from dotenv import load_dotenv

from ingest_common import build_row, upsert_matches
from supabase import create_client, Client

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from predictionbot.http import JsonHttpClient
from predictionbot.sources.openfootball import OpenFootballClient

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Extended list covering additional divisions and alternative codes
REMAINING_LEAGUES = {
    "league_one": "en.3.json",
    "league_two": "en.4.json",
    "segunda_division": "es.2.json",
    "serie_b": "it.2.json",
    "ligue_2": "fr.2.json",
    "eerste_divisie": "nl.2.json",
    "liga_portugal_2": "pt.2.json"
}

def backfill_remaining():
    load_dotenv()
    
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing Supabase credentials in .env")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = OpenFootballClient(JsonHttpClient('Mozilla/5.0'))

    logger.info("🚀 Starting deep-scan backfill for remaining minor/lower tiers...")
    total_inserted = 0

    for league_name, file_name in REMAINING_LEAGUES.items():
        for season in ["2023-24", "2024-25"]:
            logger.info(f"📚 Fetching {league_name} ({season})...")
            try:
                matches = client.fetch_season(season, file_name)
                if not matches:
                    continue
                
                matches_to_instances = []
                for m in matches:
                    home = getattr(m, 'home', None)
                    away = getattr(m, 'away', None)
                    home_score = getattr(m, 'home_goals', None)
                    away_score = getattr(m, 'away_goals', None)
                    date_val = getattr(m, 'date', None)

                    if not home or not away or home_score is None or away_score is None:
                        continue

                    date_str = str(date_val)[:10] if date_val else "2024-01-01"
                    matches_to_instances.append(build_row(
                        date_str,
                        str(home),
                        str(away),
                        int(home_score),
                        int(away_score),
                        league_name.replace("_", " ").title(),
                        home_shots=getattr(m, "home_shots", None),
                        away_shots=getattr(m, "away_shots", None),
                    ))

                inserted = upsert_matches(
                    supabase, matches_to_instances, f"{league_name} ({season})"
                )
                total_inserted += inserted
                if inserted:
                    logger.info(
                        f"✅ Inserted {inserted} matches for {league_name} ({season})."
                    )

            except Exception as e:
                logger.warning(f"⚠️ Notice for {league_name} ({season}): {e}")

    logger.info(f"🎉 Deep-Scan Backfill Complete! Additional matches added: {total_inserted}")

if __name__ == "__main__":
    backfill_remaining()
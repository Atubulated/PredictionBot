import os
import sys
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

from ingest_common import build_row, upsert_matches

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from predictionbot.http import JsonHttpClient
from predictionbot.sources.openfootball import OpenFootballClient

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

OPENFOOTBALL_LEAGUES = {
    "premier_league": "en.1.json", "championship": "en.2.json",
    "bundesliga": "de.1.json", "laliga": "es.1.json", 
    "ligue_1": "fr.1.json", "serie_a": "it.1.json"
}

def backfill_supabase_from_openfootball():
    load_dotenv()
    
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing Supabase credentials in .env")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    settings_dummy = type('Settings', (object,), {'user_agent': 'Mozilla/5.0'})()
    client = OpenFootballClient(JsonHttpClient(settings_dummy.user_agent))

    logger.info("🚀 Starting final OpenFootball backfill into Supabase...")
    total_inserted = 0

    for league_name, file_name in OPENFOOTBALL_LEAGUES.items():
        for season in ["2023-24", "2024-25"]:
            logger.info(f"📚 Fetching {league_name} ({season})...")
            try:
                matches = client.fetch_season(season, file_name)
                if not matches:
                    continue
                
                matches_to_insert = []
                for m in matches:
                    # Mapped directly to the exact attributes revealed in the debug log
                    home = getattr(m, 'home', None)
                    away = getattr(m, 'away', None)
                    home_score = getattr(m, 'home_goals', None)
                    away_score = getattr(m, 'away_goals', None)
                    date_val = getattr(m, 'date', None)

                    if not home or not away or home_score is None or away_score is None:
                        continue

                    date_str = str(date_val)[:10] if date_val else "2024-01-01"
                    matches_to_insert.append(build_row(
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
                    supabase, matches_to_insert, f"{league_name} ({season})"
                )
                total_inserted += inserted
                if inserted:
                    logger.info(
                        f"✅ Successfully inserted {inserted} matches for {league_name} ({season})."
                    )

            except Exception as e:
                logger.warning(f"⚠️ Error processing {league_name}: {e}")

    logger.info(f"🎉 Backfill Complete! Total historical matches loaded: {total_inserted}")

if __name__ == "__main__":
    backfill_supabase_from_openfootball()
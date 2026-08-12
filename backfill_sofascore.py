import os
import sys
import time
import logging
from datetime import date, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

from ingest_common import upsert_matches

# Ensure we can import from your src directory
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from predictionbot.sources.sofascore import SofascoreClient

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def backfill_with_sofascore(days_back: int = 30):
    load_dotenv()
    
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_KEY in .env file.")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = SofascoreClient()

    logger.info(f"🚀 Starting SofaScore 30-day historical backfill...")
    total_inserted = 0

    # Loop backward in time
    for i in range(days_back, 0, -1):
        target_date = date.today() - timedelta(days=i)
        date_str = target_date.isoformat()
        logger.info(f"📅 Fetching SofaScore results for {date_str}...")
        
        try:
            # Fetch scheduled events for football on this date
            events = client.scheduled_events(sport="football", date=date_str)
            matches_to_insert = []
            
            for event in events:
                raw = event.raw
                
                # Check if the match is finished
                status = raw.get("status", {}).get("type")
                if status != "finished":
                    continue
                
                home_score = raw.get("homeScore", {}).get("current")
                away_score = raw.get("awayScore", {}).get("current")
                
                if home_score is None or away_score is None:
                    continue

                match_data = {
                    'api_football_id': int(event.source_id),  # We map SofaScore ID into this column to keep schema happy
                    'home_team': event.home.name,
                    'away_team': event.away.name,
                    'home_score': int(home_score),
                    'away_score': int(away_score),
                    'match_date': date_str,
                    'league': event.league or "Unknown League",
                    'home_shots': None,
                    'away_shots': None,
                }
                matches_to_insert.append(match_data)
                
            if matches_to_insert:
                inserted = upsert_matches(supabase, matches_to_insert, date_str)
                total_inserted += inserted
                logger.info(f"✅ Inserted {inserted} matches from SofaScore for {date_str}.")
            else:
                logger.warning(f"📭 No finished matches found for {date_str}.")
                
        except Exception as e:
            logger.error(f"❌ Failed on {date_str}: {e}")
            
        # Small courteous delay so SofaScore's Cloudflare doesn't rate-limit your curl session
        time.sleep(2.0)

    logger.info(f"🎉 SofaScore Backfill Complete! Successfully loaded {total_inserted} historical matches into Supabase.")

if __name__ == "__main__":
    backfill_with_sofascore(30)
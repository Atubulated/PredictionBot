import os
import sys
import time
import logging
from datetime import date, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

# Ensure we can import from your src directory
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from predictionbot.sources.api_football import ApiFootballProvider

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def backfill_database(days_back: int = 30):
    load_dotenv()
    
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_KEY in .env file.")
        return

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    provider = ApiFootballProvider()

    logger.info(f"🚀 Starting 30-day historical backfill...")
    total_inserted = 0

    # Loop backward in time
    for i in range(days_back, 0, -1):
        target_date = date.today() - timedelta(days=i)
        logger.info(f"📅 Fetching results for {target_date.isoformat()}...")
        
        try:
            fixtures = provider.fixtures_by_date(target_date.isoformat())
            matches_to_insert = []
            
            for fixture in fixtures:
                # Only grab finished matches
                if fixture['fixture']['status']['short'] not in ['FT', 'AET', 'PEN']:
                    continue
                
                # Safely extract stats if they exist
                home_shots = None
                away_shots = None
                stats = fixture.get('statistics', [])
                if len(stats) > 0:
                    home_shots = stats[0].get('Shots on Goal')
                if len(stats) > 1:
                    away_shots = stats[1].get('Shots on Goal')

                match_data = {
                    'api_football_id': int(fixture['fixture']['id']),
                    'home_team': fixture['teams']['home']['name'],
                    'away_team': fixture['teams']['away']['name'],
                    'home_score': fixture['goals']['home'],
                    'away_score': fixture['goals']['away'],
                    'match_date': target_date.isoformat(),
                    'league': fixture['league']['name'],
                    'home_shots': home_shots,
                    'away_shots': away_shots,
                }
                matches_to_insert.append(match_data)
                
            if matches_to_insert:
                # Push the whole day's slate to Supabase in one chunk
                supabase.table('match_results').upsert(matches_to_insert, on_conflict='api_football_id').execute()
                total_inserted += len(matches_to_insert)
                logger.info(f"✅ Inserted {len(matches_to_insert)} matches for {target_date}.")
            else:
                logger.warning(f"📭 No finished matches found for {target_date}.")
                
        except Exception as e:
            logger.error(f"❌ Failed on {target_date}: {e}")
            
        # VERY IMPORTANT: Sleep for 6.5 seconds to respect the 10-requests/minute API limit
        logger.info("⏳ Sleeping to respect API rate limits...")
        time.sleep(6.5)

    logger.info(f"🎉 Backfill Complete! Successfully loaded {total_inserted} historical minor league matches into Supabase.")

if __name__ == "__main__":
    backfill_database(30)
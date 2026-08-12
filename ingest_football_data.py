# ingest_football_data.py
import os
import csv
import io
import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables (ensure your .env file has SUPABASE_URL and SUPABASE_KEY)
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# URLs for the top 5 leagues (Premier League, Championship, La Liga, Bundesliga, Serie A, Ligue 1)
# Format: https://www.football-data.co.uk/mmz4281/[SEASON]/[LEAGUE].csv
LEAGUES = ['E0', 'E1', 'SP1', 'D1', 'I1', 'F1']
SEASONS = ['2324', '2223', '2122', '2021', '1920']

def clean_int(val):
    """Safely convert string to int, returning None if empty."""
    return int(val) if val and val.isdigit() else None

def ingest_data():
    print("🚀 Starting massive historical data ingestion...")
    total_inserted = 0
    
    for season in SEASONS:
        for league in LEAGUES:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
            print(f" Downloading {league} for {season}...")
            
            try:
                response = httpx.get(url, follow_redirects=True)
                response.raise_for_status()
                
                # Football-data.co.uk uses Latin-1 encoding
                content = response.content.decode('latin-1')
                reader = csv.DictReader(io.StringIO(content))
                
                batch_data = []
                for row in reader:
                    if not row.get('Date'): continue
                    
                    # Map CSV columns to our Supabase schema
                    match_data = {
                        'home_team': row['HomeTeam'],
                        'away_team': row['AwayTeam'],
                        'home_score': clean_int(row['FTHG']),
                        'away_score': clean_int(row['FTAG']),
                        'match_date': row['Date'], # Format is usually DD/MM/YY
                        'league': league,
                        'home_shots': clean_int(row.get('HS')),
                        'away_shots': clean_int(row.get('AS')),
                        'home_shots_on_target': clean_int(row.get('HST')),
                        'away_shots_on_target': clean_int(row.get('AST')),
                        'home_corners': clean_int(row.get('HC')),
                        'away_corners': clean_int(row.get('AC')),
                        'home_yellows': clean_int(row.get('HY')),
                        'away_yellows': clean_int(row.get('AY')),
                        'home_reds': clean_int(row.get('HR')),
                        'away_reds': clean_int(row.get('AR')),
                    }
                    batch_data.append(match_data)
                
                # Insert in batches of 100 to avoid API limits
                for i in range(0, len(batch_data), 100):
                    batch = batch_data[i:i+100]
                    supabase.table('match_results').insert(batch).execute()
                    total_inserted += len(batch)
                    print(f"  ✅ Inserted {total_inserted} matches so far...")
                    
            except Exception as e:
                print(f"❌ Error processing {league} {season}: {e}")

    print(f"🎉 INGESTION COMPLETE! Total matches added: {total_inserted}")
    print("🔄 You should now run the 'update_team_statistics' function to recalculate team forms.")

if __name__ == "__main__":
    ingest_data()
import os
import sys
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def build_stats():
    load_dotenv()
    supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    
    logger.info("🔄 Fetching match history from Supabase to compute team stats...")
    response = supabase.table('match_results').select('*').execute()
    matches = response.data
    
    if not matches:
        logger.error("No matches found in match_results table!")
        return

    stats = {}
    
    for m in matches:
        home = m['home_team']
        away = m['away_team']
        h_score = m['home_score']
        a_score = m['away_score']
        
        for team in [home, away]:
            if team not in stats:
                stats[team] = {'matches_played': 0, 'wins': 0, 'goals_for': 0, 'goals_against': 0}
                
        stats[home]['matches_played'] += 1
        stats[away]['matches_played'] += 1
        stats[home]['goals_for'] += h_score
        stats[home]['goals_against'] += a_score
        stats[away]['goals_for'] += a_score
        stats[away]['goals_against'] += h_score
        
        if h_score > a_score:
            stats[home]['wins'] += 1
        elif a_score > h_score:
            stats[away]['wins'] += 1

    team_stats_rows = []
    for team, data in stats.items():
        played = data['matches_played']
        xg_diff = (data['goals_for'] - data['goals_against']) / max(1, played)
        team_stats_rows.append({
            'team_name': team,
            'matches_played': played,
            'wins': data['wins'],
            'xg_difference': round(xg_diff, 2)
        })

    logger.info(f"💾 Pushing stats for {len(team_stats_rows)} teams to Supabase...")
    
    # Clear existing stats to avoid duplicate primary key or conflict errors
    try:
        supabase.table('team_stats').delete().neq('team_name', 'DO_NOT_DELETE_PLACEHOLDER').execute()
    except Exception:
        pass

    # Insert in batches of 500
    for i in range(0, len(team_stats_rows), 500):
        batch = team_stats_rows[i:i+500]
        supabase.table('team_stats').insert(batch).execute()

    logger.info("🎉 Team stats successfully built and stored!")

if __name__ == "__main__":
    build_stats()
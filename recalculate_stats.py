# recalculate_stats.py
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def recalculate():
    print("📊 Processing 11,715 matches into team statistics...")
    
    # 1. Get all unique teams from the match_results table
    response = supabase.table('match_results').select('home_team, away_team').execute()
    teams = set()
    for row in response.data:
        teams.add(row['home_team'])
        teams.add(row['away_team'])
        
    print(f"Found {len(teams)} unique teams to process.")
    
    # 2. Process each team's recent form
    for i, team in enumerate(teams):
        # Get last 10 matches (home and away)
        matches = supabase.table('match_results').select('*').or_(
            f"home_team.eq.{team},away_team.eq.{team}"
        ).order('match_date', desc=True).limit(10).execute()
        
        if not matches.data: 
            continue
        
        wins = draws = losses = goals_scored = goals_conceded = 0
        form = []
        
        for m in matches.data:
            is_home = (m['home_team'] == team)
            t_goals = m['home_score'] if is_home else m['away_score']
            o_goals = m['away_score'] if is_home else m['home_score']
            
            goals_scored += t_goals
            goals_conceded += o_goals
            
            if t_goals > o_goals:
                wins += 1; form.append('W')
            elif t_goals == o_goals:
                draws += 1; form.append('D')
            else:
                losses += 1; form.append('L')
                
        # Save to Supabase (Upsert means update if exists, insert if new)
        supabase.table('team_stats').upsert({
            'team_name': team,
            'matches_played': len(matches.data),
            'wins': wins, 
            'draws': draws, 
            'losses': losses,
            'goals_scored': goals_scored, 
            'goals_conceded': goals_conceded,
            'last_5_form': '-'.join(form[:5][::-1]), # e.g., W-L-W-W-D
            'last_updated': '2024-07-27' 
        }, on_conflict='team_name,league').execute()
        
        if (i + 1) % 50 == 0:
            print(f"  ✅ Processed {i+1} teams...")
            
    print("🎉 Team statistics fully updated! Your bot is now ready for Option B.")

if __name__ == "__main__":
    recalculate()
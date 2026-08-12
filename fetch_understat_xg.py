# fetch_understat_xg.py
import os
import json
from dotenv import load_dotenv
from supabase import create_client
from playwright.sync_api import sync_playwright

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

LEAGUES = {
    'EPL': 'EPL',
    'La Liga': 'La_liga',
    'Bundesliga': 'Bundesliga',
    'Serie A': 'Serie_A',
    'Ligue 1': 'Ligue_1'
}
SEASON = '2023'

def get_understat_data(league_code):
    url = f"https://understat.com/league/{league_code}/{SEASON}"
    print(f"  🌐 Launching headless browser for {league_code}...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        api_data = None
        
        def handle_response(response):
            nonlocal api_data
            if 'getLeagueData' in response.url and response.request.method == 'GET':
                try:
                    api_data = response.json()
                    print(f"    ✅ Successfully intercepted data from API!")
                except Exception as e:
                    print(f"    ⚠️ Failed to parse JSON from API: {e}")

        page.on("response", handle_response)
        
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"    ❌ Playwright navigation error: {e}")
        finally:
            browser.close()
            
    return api_data

def fetch_and_update_xg():
    print("🚀 Starting Understat xG Scraper (JSON Parsing Fix)...")
    
    for league_name, league_code in LEAGUES.items():
        print(f"\n📊 Scraping {league_name}...")
        raw_data = get_understat_data(league_code)
        
        if not raw_data:
            print(f"  ❌ Could not fetch data for {league_name}")
            continue
            
        # 🌟 CRITICAL FIX: The API returns {"dates": [...], "teams": {...}}
        # We must extract the 'teams' dictionary specifically.
        teams_data = raw_data.get('teams', {})
        
        if not teams_data:
            print(f"  ️ No 'teams' key found in API response. Keys found: {list(raw_data.keys())}")
            continue
            
        print(f"   Found {len(teams_data)} teams in API response.")
            
        for team_name, stats in teams_data.items():
            # Safety check: ensure stats is a dictionary, not a list
            if not isinstance(stats, dict):
                continue
                
            xg_for = float(stats.get('xG', 0))
            xg_against = float(stats.get('xGA', 0))
            xg_diff = float(stats.get('xGD', 0))
            
            try:
                supabase.table('team_stats').upsert({
                    'team_name': team_name,
                    'league': league_name,
                    'xg_for': xg_for,
                    'xg_against': xg_against,
                    'xg_difference': xg_diff,
                    'last_updated': '2024-07-27'
                }, on_conflict='team_name,league').execute()
                # Only print successful updates to keep console clean
                # print(f"  ✅ Updated {team_name} (xG: {xg_for:.1f} | xGA: {xg_against:.1f})")
            except Exception as e:
                print(f"  ⚠️ Failed to update {team_name}: {e}")
                
        print(f"  ✅ Finished processing {league_name}")

    print("\n🎉 xG Scraper Complete! Your bot now has Expected Goals data.")

if __name__ == "__main__":
    fetch_and_update_xg()
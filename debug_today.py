# debug_today.py
import requests
import json
import sys
import os
from datetime import date

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from predictionbot.sources.bet9ja import BET9JA_LEAGUES

home_url = "https://bet9ja.com"
league_url = "https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetEventsInGroupV2?GROUPID={league_id}&DISP=0&GROUPMARKETID=1&matches=true"

headers = {
    "sec-ch-ua": '"Chromium";v="94", "Microsoft Edge";v="94", ";Not A Brand";v="99"',
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "referer": "https://sports.bet9ja.com",
    "user-agent": "Chrome/94.0.4606.81",
}

session = requests.Session()
session.headers.update(headers)

print("🔥 Warming up session...")
session.get(home_url)

today_str = date.today().isoformat()
print(f"📅 Searching ALL leagues for matches today: {today_str}\n")

total_today = 0

for league_name, league_id in BET9JA_LEAGUES.items():
    url = league_url.format(league_id=league_id)
    try:
        resp = session.get(url)
        data = resp.json()
        raw_events = ((data or {}).get("D") or {}).get("E") or []
        
        for ev in raw_events:
            start_date_str = str(ev.get("STARTDATE", ""))
            if today_str in start_date_str:
                total_today += 1
                match_name = ev.get('DS')
                odds_dict = ev.get("O", {})
                
                print(f"[{total_today}] {league_name}: {match_name}")
                
                # Check if Over/Under (S_OU) exists in the odds
                ou_keys = [k for k in odds_dict.keys() if k.startswith("S_OU@")]
                
                if len(ou_keys) > 0:
                    print(f"   ✅ Over/Under Odds Found: {len(ou_keys)} lines available.")
                else:
                    print(f"   ⚠️ NO Over/Under Odds published by Bet9ja for this match.")
                    
                # Print all keys just in case
                # print(f"   All Odds Keys: {list(odds_dict.keys())}")
                print("-" * 60)
                
    except Exception as e:
        pass # Ignore errors for empty leagues to keep output clean

print(f"\n TOTAL MATCHES FOUND FOR TODAY: {total_today}")
if total_today == 0:
    print("Bet9ja has not loaded any matches for today in these specific leagues.")
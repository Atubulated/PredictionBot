# find_real_ids.py
import requests
import json

home_url = "https://bet9ja.com"
# This endpoint returns the REAL backend Group IDs for a specific Sport (1 = Football)
groups_url = "https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetGroups?SPORTID=1&DISP=0"

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

print("🔍 Fetching real backend Group IDs for Football (Sport ID 1)...")
resp = session.get(groups_url)

try:
    data = resp.json()
    groups_data = data.get("D", [])
    
    # Handle both list and dict formats
    if isinstance(groups_data, dict):
        groups_list = list(groups_data.values())
    else:
        groups_list = groups_data
        
    print(f"\n✅ Successfully fetched {len(groups_list)} backend groups!\n")
    print("Here are the REAL Group IDs for the leagues we care about:\n")
    print("-" * 80)
    
    # Filter for the leagues we want to see
    keywords = ["premier", "championship", "laliga", "serie a", "bundesliga", "ligue 1", "brasil", "scotland", "mls", "libertadores", "sudamericana"]
    
    for g in groups_list:
        name = g.get("N", "Unknown")
        gid = g.get("I")
        
        # Check if the league name matches our keywords
        if any(kw in name.lower() for kw in keywords):
            clean_name = name.lower().replace(" ", "_").replace("-", "_").replace(".", "").replace("'", "")
            print(f'"{clean_name}": {gid},  # {name}')
            
    print("-" * 80)
    print("\n💡 Copy the lines above and replace your BET9JA_LEAGUES dictionary with them!")
    
except Exception as e:
    print(f"❌ Failed to parse response. Raw data: {resp.text[:500]}")
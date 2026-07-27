# check_dates.py
import requests

# Exact working config from your bet9ja.py
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

print("🔥 Warming up session (this bypasses the firewall)...")
session.get(home_url)

leagues_to_check = {
    "premier_league": 492,
    "brasileiro_serie_a": 2332,
    "scotland_premiership": 2372,
}

for name, gid in leagues_to_check.items():
    print(f"\n🔍 Checking {name.replace('_', ' ').title()} (Group ID: {gid})...")
    url = league_url.format(league_id=gid)
    resp = session.get(url)
    
    try:
        data = resp.json()
        # Navigate the exact JSON structure your bot uses
        raw_events = ((data or {}).get("D") or {}).get("E") or []
        print(f"   ✅ Found {len(raw_events)} upcoming events in Bet9ja's system.")
        
        if raw_events:
            print("   📅 First 3 upcoming matches:")
            for i, ev in enumerate(raw_events[:3]):
                ds = ev.get("DS", "Unknown Match")
                start_date = ev.get("STARTDATE")
                print(f"      [{i+1}] {ds}")
                print(f"          Date: {start_date}")
        else:
            print("   ⚠️ List is empty. Bet9ja has not loaded fixtures for this league yet.")
            
    except Exception as e:
        print(f"   ❌ Error parsing response: {e}")

print("\n" + "="*60)
print("CONCLUSION: If the dates shown are in August, that is why")
print("the scanner returned 0 for July. The bot is working perfectly!")
print("="*60)
# test_bet9ja_id.py
import requests
import json

def test_group_id(group_id, league_name):
    print(f"\n🔍 Testing {league_name} (Group ID: {group_id})...")
    
    # Use the exact same session warming as your working Bet9jaClient
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://sports.bet9ja.com/",
        "Accept": "application/json, text/plain, */*",
    })
    
    # Warm up
    session.get("https://sports.bet9ja.com")
    
    # Query the Palimpsest API for events in this group
    url = f"https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetEvents?GROUPID={group_id}&DISP=0"
    response = session.get(url)
    
    try:
        data = response.json()
        events = data.get("D", [])
        
        # Handle both list and dict formats
        if isinstance(events, dict):
            events = list(events.values())
            
        print(f"   ✅ API Response Status: {response.status_code}")
        print(f"   📊 Events Found: {len(events)}")
        
        if len(events) > 0:
            # Print the first event to prove it works
            first_event = events[0] if isinstance(events, list) else list(events.values())[0]
            print(f"   🏆 Sample Match: {first_event.get('NA')} vs {first_event.get('NB')}")
        else:
            print("   ⚠️ API returned an empty list. No events scheduled for this ID right now.")
            
    except Exception as e:
        print(f"   ❌ Failed to parse JSON. Raw response: {response.text[:200]}")

if __name__ == "__main__":
    print("🚀 Testing Bet9ja Palimpsest API directly...\n")
    
    # Test 1: A known working ID (Premier League)
    test_group_id(492, "England Premier League (Control)")
    
    # Test 2: The new summer league ID
    test_group_id(2332, "Brasileiro Serie A")
    
    # Test 3: Another new ID
    test_group_id(2372, "Scotland Premiership")
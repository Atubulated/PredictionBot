# discover_bet9ja.py
import requests
import json

def main():
    # Use a Session to hold cookies
    session = requests.Session()
    
    # Exact headers from your working Bet9jaClient
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://sports.bet9ja.com/",
        "Accept": "application/json, text/plain, */*",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    session.headers.update(headers)

    print("🔥 Warming up session by visiting Bet9ja sports homepage...")
    # This sets the necessary cookies to bypass the empty response
    session.get("https://sports.bet9ja.com", headers={"User-Agent": headers["User-Agent"]})
    
    print("🔍 Connecting to Bet9ja API to fetch sportsbook structure...\n")
    
    # 1. Get all sports
    sports_resp = session.get("https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetSports?DISP=0")
    
    try:
        sports_data = sports_resp.json()
    except Exception as e:
        print(f"❌ Failed to parse JSON. Raw response: {sports_resp.text[:500]}")
        return
        
    d_data = sports_data.get("D", {})
    
    football_id = None
    
    print("📋 Here are all the sports available on Bet9ja:")
    print("-" * 70)
    
    if isinstance(d_data, dict):
        for sport_id, sport_info in d_data.items():
            if isinstance(sport_info, dict):
                desc = sport_info.get("S_DESC", "Unknown")
                s_lang = sport_info.get("S_LANG", {})
                
                # Get the English name if available
                lang_name = "Unknown"
                if isinstance(s_lang, dict):
                    lang_name = s_lang.get("en", s_lang.get("es", "Unknown"))
                
                print(f"ID: {sport_id:<10} | Desc: {desc:<30} | Name: {lang_name}")
                
                # Check for football/soccer in any of the name fields
                search_text = f"{desc} {lang_name}".lower()
                if "football" in search_text or "soccer" in search_text or "futbol" in search_text:
                    football_id = sport_id

    print("-" * 70)

    if not football_id:
        print("\n⚠️ Could not automatically detect Football/Soccer.")
        print("Please look at the list above, find the ID for Football/Soccer, and tell me what it is!")
        return

    print(f"\n✅ Automatically detected Football/Soccer with ID: {football_id}")
    print("🌍 Fetching global categories and leagues... (This may take 10-20 seconds)\n")
    
    # 2. Get categories (countries/regions) for Football
    cats_resp = session.get(f"https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetCategories?SPORTID={football_id}&DISP=0")
    cats_data = cats_resp.json()
    
    all_leagues = {}
    
    # 3. Get competitions (leagues) for each category
    cats_list = cats_data.get("D", [])
    if isinstance(cats_list, dict):
        cats_list = list(cats_list.values())
        
    for cat in cats_list:
        if not isinstance(cat, dict):
            continue
            
        cat_id = cat.get("I")
        cat_name = cat.get("N", "Unknown")
        
        if not cat_id:
            continue
            
        comps_resp = session.get(f"https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetCompetitions?CATEGORYID={cat_id}&DISP=0")
        if comps_resp.status_code == 200:
            comps_data = comps_resp.json()
            comps_list = comps_data.get("D", [])
            if isinstance(comps_list, dict):
                comps_list = list(comps_list.values())
                
            for comp in comps_list:
                if not isinstance(comp, dict):
                    continue
                league_name = comp.get("N", "Unknown")
                group_id = comp.get("I")
                if group_id:
                    # Clean up name for python dictionary key
                    clean_name = league_name.lower().replace(" ", "_").replace("-", "_").replace(".", "").replace("'", "")
                    all_leagues[clean_name] = {"id": group_id, "name": league_name, "country": cat_name}
                    
    # Print results
    print(f"🎉 SUCCESS! Found {len(all_leagues)} total football leagues on Bet9ja!\n")
    print("Below is the verified list. Copy the ones you want into your BET9JA_LEAGUES dictionary:\n")
    print("-" * 80)
    
    # Sort by country for readability
    sorted_leagues = sorted(all_leagues.items(), key=lambda x: x[1]['country'])
    for key, val in sorted_leagues:
        print(f'"{key}": {val["id"]},  # {val["name"]} ({val["country"]})')

if __name__ == "__main__":
    main()
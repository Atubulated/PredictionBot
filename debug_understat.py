# debug_understat.py
import cloudscraper

url = "https://understat.com/league/EPL/2023"
scraper = cloudscraper.create_scraper()

print("🔍 Fetching Understat EPL 2023 page...")
response = scraper.get(url)

print(f"Status Code: {response.status_code}")
print(f"Response Length: {len(response.text)} characters")

# Check if 'teamsData' is anywhere in the page (case-insensitive)
if 'teamsdata' in response.text.lower():
    print("✅ FOUND 'teamsData' in the HTML!")
    
    # Find the first occurrence and print 300 characters around it
    idx = response.text.lower().find('teamsdata')
    snippet = response.text[max(0, idx-100) : idx+300]
    print("\n--- SNIPPET AROUND TEAMS DATA ---")
    print(snippet)
    print("---------------------------------\n")
else:
    print("❌ 'teamsData' NOT FOUND in the HTML.")
    print("Understat may have changed their frontend or is blocking the scraper entirely.")
    
    # Save to file so we can look at it
    with open("debug_understat.html", "w", encoding="utf-8") as f:
        f.write(response.text)
    print("💾 Saved raw HTML to 'debug_understat.html'. Check it in your browser.")
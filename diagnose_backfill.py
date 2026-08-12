# diagnose_backfill.py
import csv, io, re, urllib.request
from datetime import date, datetime

SITE = "https://www.football-data.co.uk/"
GH = "https://raw.githubusercontent.com/footballcsv/cache.footballdata/master/{year}/{code}.csv"
UA = {"User-Agent": "Mozilla/5.0 (personal research)"}

def get_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("windows-1252", errors="replace")

def looks_like_csv(text):
    head = text[:200].lower()
    return ("div" in head and "date" in head) or ("date" in head and "home" in head)

country = "usa"
gh_code = "us.1"

print(f"Testing {country} ({gh_code})...")

# 1) Try live site
print("\n1) Scraping live site...")
try:
    page = get_text(f"{SITE}{country}.php")
    hrefs = re.findall(r'href=["\']([^"\']+\.csv)["\']', page, re.I)
    print(f"   Found {len(hrefs)} CSV links: {hrefs[:3]}")
    
    if hrefs:
        url = hrefs[0] if hrefs[0].startswith("http") else SITE + hrefs[0].lstrip("/")
        print(f"   Downloading: {url}")
        text = get_text(url)
        print(f"   Size: {len(text)} bytes")
        print(f"   First 100 chars: {text[:100]}")
        if looks_like_csv(text):
            print("   ✅ Looks like CSV!")
            rows = list(csv.DictReader(io.StringIO(text)))
            print(f"   Total rows: {len(rows)}")
            if rows:
                print(f"   Columns: {list(rows[0].keys())[:10]}")
                print(f"   Sample: {rows[0]}")
        else:
            print("   ❌ Doesn't look like CSV (probably HTML error page)")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 2) Try GitHub mirror
print("\n2) Trying GitHub mirror...")
for year in [2024, 2023, 2022]:
    try:
        url = GH.format(year=year, code=gh_code)
        print(f"   {year}: {url}")
        text = get_text(url)
        print(f"   Size: {len(text)} bytes, first 80: {text[:80]}")
        if looks_like_csv(text):
            print("   ✅ Looks like CSV!")
            rows = list(csv.DictReader(io.StringIO(text)))
            print(f"   Total rows: {len(rows)}")
            if rows:
                print(f"   Columns: {list(rows[0].keys())[:10]}")
            break
        else:
            print("   ❌ Not CSV")
    except Exception as e:
        print(f"   ❌ Error: {e}")
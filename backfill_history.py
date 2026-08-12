# backfill_history.py — v6: correct column names and path
import csv, io, os, urllib.request, zlib
from datetime import date, datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

BASE = "https://www.football-data.co.uk/new/"
UA = {"User-Agent": "Mozilla/5.0 (personal research)"}

COUNTRIES = {
    "USA": "MLS",
    "BRA": "Brasileirão",
    "ARG": "Liga Profesional",
    "MEX": "Liga MX",
    "NOR": "Eliteserien",
    "SWE": "Allsvenskan",
    "DNK": "Superliga",
    "FIN": "Veikkausliiga",
    "IRL": "Premier Division",
    "JPN": "J1 League",
    "CHN": "Super League",
    "AUT": "Bundesliga",
    "SWZ": "Swiss Super League",
    "POL": "Ekstraklasa",
    "ROU": "Liga 1",
}
MIN_DATE = date(2022, 1, 1)

def fetch_csv(code: str):
    url = f"{BASE}{code}.csv"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8-sig", errors="replace")  # Handle BOM
    return list(csv.DictReader(io.StringIO(text)))

def parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def synth_id(home, away, d):
    return -(zlib.crc32(f"{home}|{away}|{d}".encode()) % 2**31)

def main():
    existing = set()
    offset = 0
    while True:
        rows = supabase.table("match_results").select("home_team,away_team,match_date").range(offset, offset + 999).execute().data
        existing.update((r["home_team"], r["away_team"], r["match_date"]) for r in rows)
        if len(rows) < 1000:
            break
        offset += 1000
    print(f"📦 Existing rows: {len(existing)}")

    total = 0
    for code, label in COUNTRIES.items():
        try:
            rows = fetch_csv(code)
        except Exception as e:
            print(f"⚠️ {label} ({code}): download failed ({e})")
            continue
        
        batch = []
        for r in rows:
            d = parse_date(r.get("Date"))
            if not d or d < MIN_DATE:
                continue
            
            # Try HG/AG first (new format), then FTHG/FTAG (old format)
            try:
                hg = int(float(r.get("HG") or r.get("FTHG", "")))
                ag = int(float(r.get("AG") or r.get("FTAG", "")))
            except (ValueError, TypeError):
                continue
            
            home = (r.get("Home") or r.get("HomeTeam") or "").strip()
            away = (r.get("Away") or r.get("AwayTeam") or "").strip()
            if not home or not away:
                continue
            
            key = (home, away, d.isoformat())
            if key in existing:
                continue
            existing.add(key)
            
            batch.append({
                "api_football_id": synth_id(home, away, d.isoformat()),
                "home_team": home, "away_team": away,
                "home_score": hg, "away_score": ag,
                "match_date": d.isoformat(), "league": label,
            })
        
        for i in range(0, len(batch), 100):
            supabase.table("match_results").insert(batch[i:i+100]).execute()
        total += len(batch)
        print(f"✅ {label} ({code}): +{len(batch)} matches")

    print(f"🎉 Done: {total} new historical matches (0 API-Football quota used).")

if __name__ == "__main__":
    main()
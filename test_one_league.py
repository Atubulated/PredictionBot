# test_one_league.py — diagnose why USA.csv inserts 0 rows
import csv, io, os, urllib.request
from datetime import date, datetime

url = "https://www.football-data.co.uk/usa/USA.csv"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=20) as resp:
    text = resp.read().decode("windows-1252", errors="replace")

rows = list(csv.DictReader(io.StringIO(text)))
print(f"📥 Downloaded {len(rows)} rows from USA.csv")
print(f"Columns: {list(rows[0].keys()) if rows else 'NONE'}")

def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m/%Y %H:%M"):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: continue
    return None

MIN_DATE = date(2022, 1, 1)
valid = []
failed_date = failed_scores = failed_names = 0

for r in rows:
    d = parse_date(r.get("Date"))
    if not d:
        failed_date += 1
        continue
    if d < MIN_DATE:
        continue
    try:
        hg, ag = int(r.get("FTHG", "")), int(r.get("FTAG", ""))
    except (ValueError, TypeError):
        failed_scores += 1
        continue
    home = (r.get("Home") or "").strip()
    away = (r.get("Away") or "").strip()
    if not home or not away:
        failed_names += 1
        continue
    valid.append((d.isoformat(), home, away, hg, ag))

print(f"\n✅ Valid rows (≥2022, scores, names): {len(valid)}")
print(f"❌ Failed date parse: {failed_date}")
print(f"❌ Failed score parse: {failed_scores}")
print(f"❌ Empty team names: {failed_names}")

if valid:
    print(f"\nSample valid rows:")
    for d, h, a, hg, ag in valid[:3]:
        print(f"  {d} | {h} {hg}-{ag} {a}")
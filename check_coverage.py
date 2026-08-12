"""Report historical match coverage by league.

Usage:
    python check_coverage.py
    python check_coverage.py --min-matches 20

The Supabase query remains paginated in 1,000-row windows and the script
continues to expose provider/database failures through its exit status.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client


def collect_coverage(supabase, page_size: int = 1000) -> tuple[Counter, list[str]]:
    leagues: Counter = Counter()
    dates: list[str] =[]
    offset = 0
    while True:
        rows = (
            supabase.table("match_results")
            .select("match_date,league")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
            or[]
        )
        for row in rows:
            league = (row.get("league") or "Unknown").strip() or "Unknown"
            leagues[league] += 1
            if row.get("match_date"):
                dates.append(str(row["match_date"]))
        if len(rows) < page_size:
            break
        offset += page_size
    return leagues, dates


def _parse_date(value: str) -> datetime | None:
    """Parse the mixed date formats present in `match_results`.

    Older ingests stored DD/MM/YYYY; the backfill scripts store ISO YYYY-MM-DD
    (optionally with a time component). Returns None for anything unrecognized
    so the caller can count and report unparseable rows instead of silently
    producing a bogus lexical min/max.
    """
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def report_coverage(leagues: Counter, dates: list[str], min_matches: int = 10) -> str:
    total = sum(leagues.values())
    parsed = [d for d in (_parse_date(x) for x in dates) if d is not None]
    unparsed = len(dates) - len(parsed)
    if parsed:
        date_range = f"{min(parsed).date()} -> {max(parsed).date()}"
    else:
        date_range = "no parseable dated rows"
    lines = [f"Total rows: {total}  |  Date range: {date_range}"]
    if unparsed:
        lines[0] += f"  |  Unparseable dates: {unparsed}"
    for league, count in leagues.most_common():
        marker = "  LOW COVERAGE" if count < min_matches else ""
        lines.append(f"  {count:6d}  {league}{marker}")
    low = sorted((league, count) for league, count in leagues.items() if count < min_matches)
    lines.append("")
    lines.append(f"Leagues below {min_matches} matches: {len(low)}")
    for league, count in low:
        lines.append(f"  {count:6d}  {league}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect match_results league coverage")
    parser.add_argument("--min-matches", type=int, default=10)
    args = parser.parse_args()
    if args.min_matches < 1:
        parser.error("--min-matches must be at least 1")

    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")
    supabase = create_client(url, key)
    leagues, dates = collect_coverage(supabase)
    print(report_coverage(leagues, dates, args.min_matches))


if __name__ == "__main__":
    main()

"""One-off: re-settle already-graded bet legs in Supabase with the fixed grader.

Background
----------
The old ``evaluator.evaluate_bet`` lower-cased the whole combined
``"{market} - {selection}"`` string and matched substrings, so it mis-graded:

  * every 1X2 bet (the string "1x2 - away" contains "1x" -> graded as a
    Home-or-Draw double chance),
  * slash-form double chances and handicaps (fell through to a blanket loss),
  * corners (graded against goals instead of corner counts).

Those wrong statuses were written to the ``user_bets`` table and shown to users.
This script re-grades every terminal leg (won/lost/void) with the corrected
grader and, for corner legs, real corner counts fetched from API-Football.

Safety
------
Dry-run by DEFAULT — it only prints what WOULD change. Pass ``--apply`` to
write. Pass ``--renotify-changed`` to also clear ``settlement_notified_at`` on
slips whose overall result flipped, so the running bot resends a corrected
result sheet on its next polling cycle.

Usage
-----
    PYTHONPATH=src python resettle_history.py                 # dry run
    PYTHONPATH=src python resettle_history.py --apply          # write fixes
    PYTHONPATH=src python resettle_history.py --apply --renotify-changed
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv

load_dotenv()

from supabase import create_client

from predictionbot.evaluator import evaluate_bet, is_corners_market
from predictionbot.sources.api_football import ApiFootballProvider
from predictionbot.stats import StatCode

TERMINAL = ("won", "lost", "void")


def _parse_score(final_score: str | None) -> tuple[int, int] | None:
    """Parse a stored "H-A" (possibly with a trailing " (N corners)")."""
    if not final_score:
        return None
    head = final_score.split(" ")[0].strip()
    if "-" not in head:
        return None
    a, _, b = head.partition("-")
    try:
        return int(a), int(b)
    except ValueError:
        return None


def _slip_result(statuses: list[str]) -> str:
    if "lost" in statuses:
        return "lost"
    if "won" in statuses:
        return "won"
    return "void"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write corrections (default: dry run)")
    parser.add_argument(
        "--renotify-changed",
        action="store_true",
        help="clear settlement_notified_at on slips whose overall result flipped",
    )
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL / SUPABASE_KEY in environment.")
        return 1
    supabase = create_client(url, key)
    provider = ApiFootballProvider()

    rows = (
        supabase.table("user_bets").select("*").in_("status", list(TERMINAL)).execute().data
    )
    print(f"Loaded {len(rows)} already-settled legs.\n")

    result_cache: dict[str, dict | None] = {}
    corners_cache: dict[str, float | None] = {}

    def fixture_result(af_id) -> dict | None:
        key = str(af_id)
        if key not in result_cache:
            result_cache[key] = provider.get_fixture_result(key)
        return result_cache[key]

    def fixture_corners(af_id) -> float | None:
        key = str(af_id)
        if key not in corners_cache:
            try:
                stats = provider.fixture_stats(key)
                has_rows = any(s.code == StatCode.CORNERS for s in stats.team_stats)
                corners_cache[key] = stats.team_total(StatCode.CORNERS) if has_rows else None
            except Exception:
                corners_cache[key] = None
        return corners_cache[key]

    changes: list[dict] = []          # per-leg corrections
    changed_slips: dict[str, bool] = {}  # slip_id -> overall result flipped
    old_by_slip: dict[str, list[str]] = {}
    new_by_slip: dict[str, list[str]] = {}

    for row in rows:
        slip_id = row.get("slip_id", "")
        selection = row.get("selection", "")
        af_id = row.get("api_football_id")
        old_status = row.get("status")

        # Prefer a fresh fetch (the stored score came from the buggy run and, for
        # corners, was goals not corners). Fall back to the stored score.
        home = away = None
        corners_total = None
        used_source = "stored"
        if af_id:
            res = fixture_result(af_id)
            if res and res.get("status") in ("FT", "AET", "PEN"):
                home, away = res.get("home_score"), res.get("away_score")
                used_source = "api"
        if home is None or away is None:
            parsed = _parse_score(row.get("final_score"))
            if parsed:
                home, away = parsed

        new_status = old_status
        new_final = row.get("final_score")
        note = ""
        if home is None or away is None:
            note = "no score available — left unchanged"
        else:
            if is_corners_market(selection) and af_id:
                corners_total = fixture_corners(af_id)
            outcome = evaluate_bet(selection, home, away, corners_total=corners_total)
            if outcome == "unsettleable":
                note = "corner stats unavailable — left unchanged"
            else:
                new_status = outcome
                new_final = f"{home}-{away}"
                if is_corners_market(selection) and corners_total is not None:
                    new_final += f" ({int(corners_total)} corners)"

        old_by_slip.setdefault(slip_id, []).append(old_status)
        new_by_slip.setdefault(slip_id, []).append(new_status)

        if new_status != old_status:
            changes.append(
                {
                    "id": row.get("id"),
                    "slip_id": slip_id,
                    "selection": selection,
                    "old": old_status,
                    "new": new_status,
                    "final_score": new_final,
                    "src": used_source,
                }
            )
        if note:
            print(f"  · {slip_id} | {selection!r}: {note}")

    for slip_id in old_by_slip:
        changed_slips[slip_id] = _slip_result(old_by_slip[slip_id]) != _slip_result(
            new_by_slip.get(slip_id, [])
        )

    print(f"\n{len(changes)} leg(s) would change status:\n")
    for c in changes:
        print(
            f"  [{c['slip_id']}] {c['selection']:<40} "
            f"{c['old']:>5} -> {c['new']:<5} ({c['final_score']}, via {c['src']})"
        )

    flipped = [s for s, did in changed_slips.items() if did]
    print(f"\n{len(flipped)} slip(s) change OVERALL result: {', '.join(flipped) or '(none)'}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to persist.")
        return 0

    for c in changes:
        supabase.table("user_bets").update(
            {"status": c["new"], "final_score": c["final_score"]}
        ).eq("id", c["id"]).execute()
    print(f"\n✅ Applied {len(changes)} leg correction(s).")

    if args.renotify_changed and flipped:
        for slip_id in flipped:
            supabase.table("user_bets").update({"settlement_notified_at": None}).eq(
                "slip_id", slip_id
            ).execute()
        print(f"🔔 Cleared notify marker on {len(flipped)} flipped slip(s) — the bot will resend corrected sheets.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

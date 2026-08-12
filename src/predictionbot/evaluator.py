# src/predictionbot/evaluator.py
"""Settlement grader for saved bet legs.

Each leg is persisted (``telegram_bot.save_bet_slip_to_db``) with its selection
stored as the combined string ``f"{market} - {selection}"`` — e.g.::

    "1X2 - Away"
    "O/U Over 2.5 - Over 2.5"
    "BTTS - Yes"
    "Double Chance - Home/Away"
    "AH Home -1.5 - Home -1.5"
    "Draw No Bet - Home"
    "Corners O/U Over 8.5 - Over 8.5"

The market family is NOT stored on the row, but the *market prefix* (the part
before the first ``" - "``) is unambiguous, so we recover the family from it and
grade the pure selection. This is what fixes the old grader, which lower-cased
the WHOLE combined string and matched substrings — "1x2 - away" contains "1x",
so every 1X2 bet was graded as a Home-or-Draw double chance; slash-form double
chances and handicaps fell through to a blanket loss; and corners were graded
against goals. See tests/test_evaluator.py for the exact regressions.

``evaluate_bet`` returns one of:
    "won"          — leg won
    "lost"         — leg lost
    "void"         — push / stake returned (handicap push, DNB draw, unknown market)
    "unsettleable" — cannot be graded from the data on hand (corners with no
                     corner stats yet). The caller decides whether to keep the
                     leg pending and retry, or void it after the grace period.
"""
from __future__ import annotations

import re

# Signed number, e.g. "-1.5", "+2", "8.5". Sign is kept so "Home -1.5" parses to
# -1.5 (a lay-goals handicap), never +1.5.
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _last_line(text: str) -> float | None:
    matches = _NUM.findall(text)
    return float(matches[-1]) if matches else None


def is_corners_market(selection: str) -> bool:
    """True when the leg is a corners over/under (needs corner stats, not goals)."""
    return (selection or "").strip().lower().startswith("corners")


def evaluate_bet(
    selection: str,
    home_score,
    away_score,
    *,
    corners_total: float | None = None,
) -> str:
    """Grade one leg. Returns "won" | "lost" | "void" | "unsettleable".

    ``home_score``/``away_score`` are full-time GOALS. ``corners_total`` is the
    combined corner count for the fixture and is only consulted for corners
    markets; leave it None for everything else.
    """
    if home_score is None or away_score is None:
        # Finished-but-scoreless (abandoned/postponed feed) — refund rather than
        # declare a loss.
        return "void"

    market_part, sep, sel_part = (selection or "").partition(" - ")
    if not sep:
        # No separator: nothing we can safely dispatch on.
        sel_part = market_part
    mp = market_part.strip().lower()
    sel = sel_part.strip()

    if mp.startswith("corners"):
        if corners_total is None:
            return "unsettleable"
        return _grade_over_under(sel, corners_total)
    if mp.startswith("1x2"):
        return _grade_1x2(sel, home_score, away_score)
    if mp.startswith("o/u"):
        return _grade_over_under(sel, home_score + away_score)
    if mp.startswith("btts"):
        return _grade_btts(sel, home_score, away_score)
    if mp.startswith("double chance"):
        return _grade_double_chance(sel, home_score, away_score)
    if mp.startswith("draw no bet") or mp.startswith("dnb"):
        return _grade_draw_no_bet(sel, home_score, away_score)
    if mp.startswith("ah") or mp.startswith("asian") or mp.startswith("handicap"):
        return _grade_handicap(sel, home_score, away_score)

    # Unknown / unmapped market: refund rather than risk a wrong grade.
    return "void"


def _grade_1x2(sel: str, home: int, away: int) -> str:
    low = sel.lower()
    if "home" in low or low == "1":
        return "won" if home > away else "lost"
    if "away" in low or low == "2":
        return "won" if away > home else "lost"
    if "draw" in low or low == "x":
        return "won" if home == away else "lost"
    return "void"


def _grade_double_chance(sel: str, home: int, away: int) -> str:
    # API-Football slash forms ("Home/Draw", "Home/Away", "Draw/Away") plus the
    # textual/notation variants ("Home or Draw", "1X", "12", "X2").
    low = sel.lower().replace(" ", "")
    has_home = "home" in low
    has_away = "away" in low
    has_draw = "draw" in low
    if (has_home and has_draw) or "1x" in low:
        return "won" if home >= away else "lost"          # 1X: home win or draw
    if (has_draw and has_away) or "x2" in low:
        return "won" if away >= home else "lost"          # X2: away win or draw
    if (has_home and has_away) or "12" in low:
        return "won" if home != away else "lost"          # 12: either team wins
    return "void"


def _grade_btts(sel: str, home: int, away: int) -> str:
    low = sel.lower()
    both_scored = home > 0 and away > 0
    if "yes" in low:
        return "won" if both_scored else "lost"
    if "no" in low:
        return "won" if not both_scored else "lost"
    return "void"


def _grade_over_under(sel: str, total: float) -> str:
    line = _last_line(sel)
    if line is None:
        return "void"
    low = sel.lower()
    if "over" in low:
        if total > line:
            return "won"
        if total < line:
            return "lost"
        return "void"          # exact whole-number line: push
    if "under" in low:
        if total < line:
            return "won"
        if total > line:
            return "lost"
        return "void"
    return "void"


def _grade_draw_no_bet(sel: str, home: int, away: int) -> str:
    if home == away:
        return "void"          # stake returned on a draw
    low = sel.lower()
    if "home" in low or low == "1":
        return "won" if home > away else "lost"
    if "away" in low or low == "2":
        return "won" if away > home else "lost"
    return "void"


def _grade_handicap(sel: str, home: int, away: int) -> str:
    """Signed-line handicap for a Home/Away selection.

    "AH Home -1.5" wins if home wins by 2+, "AH Away +1" wins unless away loses
    by 2+, etc. A push (adjusted margin exactly 0 on a whole line) is void.

    Note: quarter lines (±0.25 / ±0.75) split the stake in real bookmaking; with
    only won/lost/void to work with we settle them to the side that finished
    ahead of the adjusted line (never mis-declaring direction). Draw-handicap
    (3-way "Handicap Result") selections are left unsettleable rather than
    guessed.
    """
    low = sel.lower()
    line = _last_line(sel)
    if line is None:
        return "void"
    if "home" in low:
        margin = (home + line) - away
    elif "away" in low:
        margin = (away + line) - home
    elif "draw" in low:
        return "unsettleable"  # 3-way handicap draw — don't guess
    else:
        return "void"
    if margin > 0:
        return "won"
    if margin < 0:
        return "lost"
    return "void"

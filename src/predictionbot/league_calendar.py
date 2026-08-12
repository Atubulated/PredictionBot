"""Trusted-league calendar: which leagues the model has real history for, when
each is in season, and the user-facing status/come-back messages.

This is the single source of truth behind three things in ``telegram_bot``:

* ``MAJOR_LEAGUES`` — the lowercase substrings the major-only scan pass tests
  against ``fixture.league_name`` (``build_family_groups``).
* the ``/leagues`` command (``format_leagues_status``).
* the "come back when your league is live" hint shown on no-slip days
  (``format_comeback_hint``).

Every league here has genuine, verified history in the pool — OpenFootball season
files and/or Supabase ``match_results`` (900+ matches each; see ``check_coverage.py``).
Nothing is filler.

It lives in its own module (not inside ``telegram_bot``) so it can be unit-tested:
``telegram_bot`` can't be imported without live Supabase/Telegram credentials, but
this has no such dependency.
"""
from __future__ import annotations

from datetime import date

# match : lowercase substring tested against fixture.league_name
# emoji : flag shown in /leagues
# start : confirmed 2026-27 opener (European leagues run start -> the following May)
# months: in-season months for summer leagues that are already running now
TRUSTED_LEAGUES: list[dict] = [
    # European leagues — confirmed 2026-27 openers (pushed late by the 2026 World Cup).
    {"name": "2. Bundesliga",  "emoji": "🇩🇪", "match": "2. bundesliga",  "start": date(2026, 8, 7)},
    {"name": "League One",     "emoji": "🏴", "match": "league one",     "start": date(2026, 8, 8)},
    {"name": "Ligue 2",        "emoji": "🇫🇷", "match": "ligue 2",        "start": date(2026, 8, 8)},
    {"name": "Championship",   "emoji": "🏴", "match": "championship",   "start": date(2026, 8, 14)},
    {"name": "League Two",     "emoji": "🏴", "match": "league two",     "start": date(2026, 8, 14)},
    {"name": "La Liga",        "emoji": "🇪🇸", "match": "laliga",         "start": date(2026, 8, 16)},
    {"name": "Premier League", "emoji": "🏴", "match": "premier league", "start": date(2026, 8, 22)},
    {"name": "Serie A",        "emoji": "🇮🇹", "match": "serie a",        "start": date(2026, 8, 23)},
    {"name": "Ligue 1",        "emoji": "🇫🇷", "match": "ligue 1",        "start": date(2026, 8, 23)},
    {"name": "Bundesliga",     "emoji": "🇩🇪", "match": "bundesliga",     "start": date(2026, 8, 28)},
    # Summer leagues — already running now (Supabase history, 1000+ matches each).
    {"name": "MLS",               "emoji": "🇺🇸", "match": "major league soccer", "months": {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}},
    {"name": "Liga MX",           "emoji": "🇲🇽", "match": "liga mx",             "months": {1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12}},
    {"name": "Brasileirão",       "emoji": "🇧🇷", "match": "brasileir",           "months": {3, 4, 5, 6, 7, 8, 9, 10, 11, 12}},
    {"name": "Argentina Primera", "emoji": "🇦🇷", "match": "liga profesional",    "months": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}},
    {"name": "J1 League",         "emoji": "🇯🇵", "match": "j1 league",           "months": {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}},
    {"name": "Ekstraklasa",       "emoji": "🇵🇱", "match": "ekstraklasa",         "months": {1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12}},
    {"name": "Eliteserien",       "emoji": "🇳🇴", "match": "eliteserien",         "months": {3, 4, 5, 6, 7, 8, 9, 10, 11}},
    {"name": "Allsvenskan",       "emoji": "🇸🇪", "match": "allsvenskan",         "months": {3, 4, 5, 6, 7, 8, 9, 10, 11}},
]

# Lowercase substrings tested against fixture.league_name in the major-only scan pass.
MAJOR_LEAGUES: list[str] = [lg["match"] for lg in TRUSTED_LEAGUES]


def league_is_live(lg: dict, today: date) -> bool:
    """True if the trusted league is currently in season."""
    start = lg.get("start")
    if start is not None:
        # European season: live from the opener until the following June.
        season_end = date(start.year + 1, 6, 1) if start.month >= 6 else date(start.year, 6, 1)
        return start <= today < season_end
    months = lg.get("months")
    if months is not None:
        return today.month in months
    return False


def live_leagues(today: date) -> list[dict]:
    """Trusted leagues in season on ``today``."""
    return [lg for lg in TRUSTED_LEAGUES if league_is_live(lg, today)]


def next_openers(today: date) -> list[tuple[str, str, date, int]]:
    """Upcoming European openers as (name, emoji, date, days_away), nearest first."""
    upcoming = [
        (lg["name"], lg["emoji"], lg["start"], (lg["start"] - today).days)
        for lg in TRUSTED_LEAGUES
        if lg.get("start") and lg["start"] > today
    ]
    return sorted(upcoming, key=lambda x: x[2])


def format_leagues_status(today: date | None = None) -> str:
    """Full /leagues report: what's live now, what starts soon."""
    today = today or date.today()
    live = live_leagues(today)
    upcoming = next_openers(today)
    lines = ["📅 *Trusted leagues* — the ones your model has real history for.", ""]
    if live:
        lines.append("🟢 *In season now:*")
        for lg in live:
            lines.append(f"   {lg['emoji']} {lg['name']}")
    else:
        lines.append("😴 *No trusted league is in season right now.*")
    if upcoming:
        lines.append("")
        lines.append("⏳ *Starting soon:*")
        for name, emoji, d, days in upcoming:
            when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
            lines.append(f"   {emoji} {name} — {d:%a %d %b} ({when})")
    lines.append("")
    lines.append("_Ask for a slip and I'll scan these first._")
    return "\n".join(lines)


def format_comeback_hint(today: date | None = None) -> str:
    """Compact 'when to come back' block for no-slip days."""
    today = today or date.today()
    live = live_leagues(today)
    upcoming = next_openers(today)
    parts: list[str] = []
    if live:
        names = ", ".join(f"{lg['emoji']} {lg['name']}" for lg in live[:6])
        parts.append(f"🟢 *In season now:* {names}.")
    if upcoming:
        soon = "  •  ".join(f"{emoji} {name} {d:%d %b}" for name, emoji, d, _ in upcoming[:3])
        parts.append(f"⏳ *Next kickoffs:* {soon}")
    parts.append("Tap /leagues anytime for the full calendar.")
    return "\n".join(parts)

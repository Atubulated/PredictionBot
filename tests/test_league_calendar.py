"""Trusted-league calendar: in-season detection, upcoming openers, and the
/leagues and come-back message copy.

These pin the behavior the user asked for: tap /leagues and instantly see which
trusted leagues are live vs. when the rest start, and on a no-slip day get told
when to come back. Dates are the confirmed 2026-27 openers (World Cup pushed the
top leagues late).
"""
from datetime import date

from predictionbot.league_calendar import (
    MAJOR_LEAGUES,
    TRUSTED_LEAGUES,
    format_comeback_hint,
    format_leagues_status,
    league_is_live,
    live_leagues,
    next_openers,
)


def _by_name(name):
    return next(lg for lg in TRUSTED_LEAGUES if lg["name"] == name)


# --- the whitelist actually widened -------------------------------------------
def test_major_leagues_now_includes_ligue2_and_summer_leagues():
    # Ligue 2 has OpenFootball history (fr.2.json) but was NOT trusted before.
    assert "ligue 2" in MAJOR_LEAGUES
    # Summer leagues with real Supabase history are now trusted too.
    assert "major league soccer" in MAJOR_LEAGUES
    assert "liga profesional" in MAJOR_LEAGUES
    assert len(MAJOR_LEAGUES) == len(TRUSTED_LEAGUES) == 18


# --- in-season detection -------------------------------------------------------
def test_european_league_live_between_opener_and_may():
    epl = _by_name("Premier League")            # starts 2026-08-22
    assert not league_is_live(epl, date(2026, 8, 21))
    assert league_is_live(epl, date(2026, 8, 22))
    assert league_is_live(epl, date(2027, 1, 5))
    assert not league_is_live(epl, date(2027, 7, 1))


def test_summer_league_live_by_month():
    mls = _by_name("MLS")                        # months Feb–Nov
    assert league_is_live(mls, date(2026, 8, 10))
    assert not league_is_live(mls, date(2026, 12, 20))


# --- today (off-season for the big five, live seconds/summer) -------------------
def test_today_2026_08_10_has_live_and_upcoming():
    today = date(2026, 8, 10)
    live_names = {lg["name"] for lg in live_leagues(today)}
    # Second tiers already kicked off (Aug 7/8), plus summer leagues run now.
    assert {"2. Bundesliga", "League One", "Ligue 2"} <= live_names
    assert "MLS" in live_names
    # The big five haven't started yet.
    assert "Premier League" not in live_names
    assert "Bundesliga" not in live_names

    openers = next_openers(today)
    # Nearest upcoming opener is the Championship / League Two on Aug 14.
    assert openers[0][2] == date(2026, 8, 14)
    # Sorted ascending by date.
    assert [o[2] for o in openers] == sorted(o[2] for o in openers)


def test_next_openers_excludes_already_started():
    today = date(2026, 8, 10)
    names = {o[0] for o in next_openers(today)}
    assert "Ligue 2" not in names          # started Aug 8
    assert "Premier League" in names       # starts Aug 22


# --- message copy --------------------------------------------------------------
def test_leagues_status_lists_live_and_upcoming():
    msg = format_leagues_status(date(2026, 8, 10))
    assert "In season now" in msg
    assert "Ligue 2" in msg
    assert "Starting soon" in msg
    assert "Premier League" in msg


def test_comeback_hint_is_short_and_actionable():
    hint = format_comeback_hint(date(2026, 8, 10))
    assert "/leagues" in hint
    # Shows the next kickoffs so the user knows when to return.
    assert "Next kickoffs" in hint


def test_deep_offseason_still_points_forward():
    # A date after every summer league and before the openers: nothing live, but
    # the user must still be told when to come back.
    msg = format_leagues_status(date(2026, 7, 1))
    assert "Starting soon" in msg
    assert "2. Bundesliga" in msg  # first opener, Aug 7

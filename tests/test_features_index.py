from datetime import datetime

from predictionbot.domain import HistoricalMatch
from predictionbot.features import (
    build_goal_profile,
    build_stat_profile,
    history_for_fixture,
    index_history_by_team,
)


def _match(home, away, hg, ag, day, **stats):
    return HistoricalMatch(
        date=datetime(2026, 1, day),
        home=home,
        away=away,
        home_goals=hg,
        away_goals=ag,
        **stats,
    )


def _pool():
    # Two focus teams plus noise, interleaved so order matters for [-limit:].
    return [
        _match("Arsenal", "Chelsea", 2, 1, 1, home_corners=6, away_corners=3),
        _match("Spurs", "Everton", 0, 0, 2),
        _match("Chelsea", "Arsenal", 1, 1, 3, home_corners=4, away_corners=5),
        _match("Liverpool", "Arsenal", 3, 2, 4, home_corners=7, away_corners=8),
        _match("Chelsea", "Newcastle", 2, 0, 5, home_corners=2, away_corners=1),
        _match("Fulham", "Brentford", 1, 1, 6),
    ]


def test_index_buckets_each_match_under_both_sides():
    index = index_history_by_team(_pool())
    # Arsenal appears in 3 matches (home once, away twice).
    assert len(index["arsenal"]) == 3
    # Chelsea appears in 3 matches too.
    assert len(index["chelsea"]) == 3
    # A team from a single match shows up once.
    assert len(index["everton"]) == 1


def test_history_for_fixture_dedupes_head_to_head_and_keeps_order():
    pool = _pool()
    index = index_history_by_team(pool)
    combined = history_for_fixture(index, "Arsenal", "Chelsea")
    # Arsenal(3) ∪ Chelsea(3) with two shared head-to-heads => 4 unique matches.
    assert len(combined) == 4
    # Original pool order is preserved.
    assert combined == [pool[0], pool[2], pool[3], pool[4]]


def test_history_for_fixture_handles_same_team_both_sides():
    index = index_history_by_team(_pool())
    combined = history_for_fixture(index, "Arsenal", "Arsenal")
    assert len(combined) == 3


def test_goal_profile_identical_from_slice_and_full_pool():
    pool = _pool()
    index = index_history_by_team(pool)
    fx_history = history_for_fixture(index, "Arsenal", "Chelsea")
    for team in ("Arsenal", "Chelsea"):
        assert build_goal_profile(team, fx_history) == build_goal_profile(team, pool)


def test_stat_profile_identical_from_slice_and_full_pool():
    pool = _pool()
    index = index_history_by_team(pool)
    fx_history = history_for_fixture(index, "Arsenal", "Chelsea")
    for team in ("Arsenal", "Chelsea"):
        assert build_stat_profile(team, "corners", fx_history) == build_stat_profile(
            team, "corners", pool
        )

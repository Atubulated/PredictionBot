from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Team
from predictionbot.engine import (
    score_btts_market,
    score_match_winner_market,
    score_stat_market,
    score_team_total_market,
)


def _strong_home_history():
    # Home scores freely, Away leaks — favours home win + goals.
    return [
        HistoricalMatch(None, "Home", "Team A", 3, 0),
        HistoricalMatch(None, "Home", "Team B", 2, 0),
        HistoricalMatch(None, "Home", "Team C", 4, 1),
        HistoricalMatch(None, "Away", "Team D", 0, 2),
        HistoricalMatch(None, "Away", "Team E", 1, 3),
        HistoricalMatch(None, "Away", "Team F", 0, 2),
    ]


def test_match_winner_scores_home_when_history_favours_home():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    market = MarketOdds("book", "1", MarketFamily.MATCH_WINNER, "1X2", "Home", 2.0)

    prediction = score_match_winner_market(fixture, market, _strong_home_history(), min_edge=0.0)

    assert prediction is not None
    assert prediction.model_probability > 0.5


def test_match_winner_returns_none_without_history():
    fixture = Fixture("test", "1", None, Team("Ghost United"), Team("Nobody FC"))
    market = MarketOdds("book", "1", MarketFamily.MATCH_WINNER, "1X2", "Home", 2.0)

    assert score_match_winner_market(fixture, market, [], min_edge=0.0) is None


def test_btts_yes_scores_with_high_scoring_history():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    history = [
        HistoricalMatch(None, "Home", "Team A", 2, 1),
        HistoricalMatch(None, "Home", "Team B", 3, 2),
        HistoricalMatch(None, "Away", "Team C", 1, 2),
        HistoricalMatch(None, "Away", "Team D", 2, 2),
    ]
    market = MarketOdds("book", "1", MarketFamily.BOTH_TEAMS_TO_SCORE, "BTTS", "Yes", 1.8)

    prediction = score_btts_market(fixture, market, history, min_edge=0.0)

    assert prediction is not None
    assert 0.0 < prediction.model_probability <= 1.0


def test_btts_returns_none_without_history():
    fixture = Fixture("test", "1", None, Team("Ghost"), Team("Nobody"))
    market = MarketOdds("book", "1", MarketFamily.BOTH_TEAMS_TO_SCORE, "BTTS", "Yes", 1.8)

    assert score_btts_market(fixture, market, [], min_edge=0.0) is None


def test_team_total_scores_over_for_prolific_home_side():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    market = MarketOdds("book", "1", MarketFamily.TEAM_TOTALS, "Home Team Total", "Home Over 1.5", 2.0, 1.5)

    prediction = score_team_total_market(fixture, market, _strong_home_history(), min_edge=0.0)

    assert prediction is not None
    assert prediction.model_probability > 0.0


def _stat_history_with_shots():
    rows = []
    # Both sides have 5 matches with shots recorded.
    for i in range(5):
        rows.append(HistoricalMatch(None, "Home", f"Opp{i}", 1, 0, home_shots=15, away_shots=8))
    for i in range(5):
        rows.append(HistoricalMatch(None, "Away", f"Opp{i}", 1, 0, home_shots=13, away_shots=9))
    return rows


def test_stat_market_scores_shots_with_enough_samples():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    market = MarketOdds("book", "1", MarketFamily.SHOTS, "Total Shots", "Over 20.5", 1.9, 20.5)

    prediction = score_stat_market(fixture, market, _stat_history_with_shots(), min_edge=0.0)

    assert prediction is not None
    assert 0.0 < prediction.model_probability < 1.0


def test_stat_market_returns_none_with_insufficient_samples():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    history = [
        HistoricalMatch(None, "Home", "Opp", 1, 0, home_shots=15, away_shots=8),
        HistoricalMatch(None, "Away", "Opp", 1, 0, home_shots=13, away_shots=9),
    ]
    market = MarketOdds("book", "1", MarketFamily.SHOTS, "Total Shots", "Over 20.5", 1.9, 20.5)

    # Only 1 sample per team, below the min_samples gate → no model prediction.
    assert score_stat_market(fixture, market, history, min_edge=0.0) is None


def test_stat_market_returns_none_when_stat_absent():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    # Shots never recorded → samples stay 0.
    history = [HistoricalMatch(None, "Home", f"Opp{i}", 1, 0) for i in range(6)]
    history += [HistoricalMatch(None, "Away", f"Opp{i}", 1, 0) for i in range(6)]
    market = MarketOdds("book", "1", MarketFamily.SHOTS, "Total Shots", "Over 20.5", 1.9, 20.5)

    assert score_stat_market(fixture, market, history, min_edge=0.0) is None

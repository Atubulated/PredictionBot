from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Team
from predictionbot.engine import score_double_chance_market, score_handicap_market


def test_double_chance_market_uses_historical_profiles() -> None:
    fixture = Fixture("test", "1", None, Team("Arsenal FC"), Team("Coventry City"))
    history = [
        HistoricalMatch(None, "Arsenal", "Chelsea", 3, 0),
        HistoricalMatch(None, "Arsenal", "Liverpool", 2, 1),
        HistoricalMatch(None, "Coventry", "Leeds", 1, 2),
        HistoricalMatch(None, "Coventry", "Burnley", 0, 2),
    ]
    market = MarketOdds("book", "1", MarketFamily.DOUBLE_CHANCE, "Double Chance", "Home or Draw", 1.25)

    prediction = score_double_chance_market(fixture, market, history, min_edge=0.0)

    assert prediction is not None
    assert prediction.model_probability > prediction.implied_probability


def test_handicap_market_can_score_strong_away_line() -> None:
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    history = [
        HistoricalMatch(None, "Home", "Average", 0, 2),
        HistoricalMatch(None, "Home", "Average", 1, 3),
        HistoricalMatch(None, "Away", "Average", 4, 0),
        HistoricalMatch(None, "Away", "Average", 3, 0),
    ]
    market = MarketOdds("book", "1", MarketFamily.HANDICAP, "Asian Handicap -1.5", "Away -1.5", 2.0, -1.5)

    prediction = score_handicap_market(fixture, market, history, min_edge=0.0)

    assert prediction is not None
    assert prediction.model_probability > 0.5

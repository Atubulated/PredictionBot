from predictionbot.domain import MarketFamily, MarketOdds
from predictionbot.odds import implied_probability, no_vig_probabilities


def test_implied_probability_for_decimal_odds() -> None:
    assert implied_probability(2.0) == 0.5


def test_no_vig_probabilities_normalize_market() -> None:
    markets = [
        MarketOdds("book", "1", MarketFamily.TOTALS, "Total", "Over", 1.8),
        MarketOdds("book", "1", MarketFamily.TOTALS, "Total", "Under", 2.0),
    ]

    probabilities = no_vig_probabilities(markets)

    assert round(sum(probabilities.values()), 6) == 1
    assert probabilities["Over"] > probabilities["Under"]

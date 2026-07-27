from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Team
from predictionbot.scanner import scan_events
from predictionbot.sources.bet9ja import Bet9jaListedEvent


def test_scan_events_filters_market_family() -> None:
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    event = Bet9jaListedEvent(
        fixture=fixture,
        markets=[
            MarketOdds("book", "1", MarketFamily.TOTALS, "Total Goals Over/Under 2.5", "Over 2.5", 1.8, 2.5),
            MarketOdds("book", "1", MarketFamily.DOUBLE_CHANCE, "Double Chance", "Home or Draw", 1.2),
        ],
    )
    history = [
        HistoricalMatch(None, "Home", "Other", 3, 1),
        HistoricalMatch(None, "Away", "Other", 2, 2),
    ]

    result = scan_events([event], history, min_edge=0.0, market_families={MarketFamily.TOTALS})

    assert all(prediction.market.family == MarketFamily.TOTALS for prediction in result.predictions)

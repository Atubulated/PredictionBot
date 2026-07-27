from __future__ import annotations

from collections.abc import Iterable

from predictionbot.domain import MarketFamily, MarketOdds


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1.0")
    return 1 / decimal_odds


def overround(markets: Iterable[MarketOdds]) -> float:
    return sum(implied_probability(market.odds) for market in markets)


def no_vig_probabilities(markets: Iterable[MarketOdds]) -> dict[str, float]:
    items = list(markets)
    total = overround(items)
    if total <= 0:
        return {}
    return {item.selection: implied_probability(item.odds) / total for item in items}


def classify_market(name: str) -> MarketFamily:
    normalized = name.lower()
    if "corner" in normalized:
        return MarketFamily.CORNERS
    if "card" in normalized or "booking" in normalized:
        return MarketFamily.BOOKINGS
    if "both teams" in normalized or "btts" in normalized:
        return MarketFamily.BOTH_TEAMS_TO_SCORE
    if "double chance" in normalized:
        return MarketFamily.DOUBLE_CHANCE
    if "handicap" in normalized:
        return MarketFamily.HANDICAP
    if "over" in normalized or "under" in normalized or "total" in normalized:
        return MarketFamily.TOTALS
    return MarketFamily.UNKNOWN

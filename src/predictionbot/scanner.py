from __future__ import annotations

from dataclasses import dataclass

from predictionbot.domain import HistoricalMatch, MarketFamily, Prediction
from predictionbot.engine import score_fixture_markets
from predictionbot.sources.bet9ja import Bet9jaListedEvent


@dataclass(frozen=True)
class ScanResult:
    events_scanned: int
    markets_scored: int
    predictions: list[Prediction]


def scan_events(
    events: list[Bet9jaListedEvent],
    history: list[HistoricalMatch],
    min_edge: float = 0.05,
    market_families: set[MarketFamily] | None = None,
) -> ScanResult:
    predictions = []
    markets_scored = 0
    for event in events:
        markets = event.markets
        if market_families:
            markets = [market for market in markets if market.family in market_families]
        scored = score_fixture_markets(event.fixture, markets, history, min_edge=min_edge)
        markets_scored += len(scored)
        predictions.extend(scored)

    predictions = sorted(predictions, key=lambda prediction: (-prediction.model_probability, -prediction.edge))
    return ScanResult(events_scanned=len(events), markets_scored=markets_scored, predictions=predictions)

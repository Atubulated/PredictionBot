from datetime import datetime

from predictionbot.accumulator import build_accumulator, build_progressive_accumulator
from predictionbot.domain import Fixture, MarketFamily, MarketOdds, Prediction, Team
from predictionbot.engine import demo_accumulator_predictions
from predictionbot.risk import DEFAULT_SAFE_ODDS_RULE, SafeOddsBand


def test_accumulator_reaches_demo_target_odds() -> None:
    accumulator = build_accumulator(demo_accumulator_predictions(), target_odds=10)

    assert accumulator.reached_target
    assert accumulator.total_odds >= 10
    # Demo predictions are classified with the current 0.95 VERY_SAFE threshold.
    assert all(leg.safe_odds_band == SafeOddsBand.SAFE for leg in accumulator.legs)


def test_accumulator_uses_one_leg_per_fixture() -> None:
    fixture = Fixture("demo", "fixture-1", datetime.now(), Team("Home"), Team("Away"))
    predictions = [
        _prediction(fixture, "Over 0.5", 1.2, 0.95),
        _prediction(fixture, "Home over 0.5", 1.3, 0.94),
    ]

    accumulator = build_accumulator(predictions, target_odds=1.1)

    assert len(accumulator.legs) == 1


def test_accumulator_filters_to_requested_band() -> None:
    fixture = Fixture("demo", "fixture-1", datetime.now(), Team("Home"), Team("Away"))
    predictions = [
        _prediction(fixture, "Safe leg", 1.5, 0.85),
    ]

    accumulator = build_accumulator(
        predictions, target_odds=1.2, band=SafeOddsBand.VERY_SAFE
    )

    assert accumulator.legs == []


def test_progressive_accumulator_reports_target_unreachable_when_odds_cap_filters_high_legs() -> None:
    # The 2.0 per-leg cap intentionally excludes the demo's 2.05+ longshots, so
    # 1000x is unreachable even after risk relaxation.
    accumulator = build_progressive_accumulator(demo_accumulator_predictions(), target_odds=1000)

    assert not accumulator.reached_target
    assert accumulator.legs
    assert all(leg.market.odds <= 2.0 for leg in accumulator.legs)
    assert SafeOddsBand.SAFE.value in accumulator.risk_bands_used


def test_progressive_accumulator_reports_unreachable_target() -> None:
    accumulator = build_progressive_accumulator(
        demo_accumulator_predictions(),
        target_odds=1_000_000_000,
        max_risk_band=SafeOddsBand.HIGH_RISK,
    )

    assert not accumulator.reached_target


def _prediction(fixture: Fixture, selection: str, odds: float, probability: float) -> Prediction:
    band = DEFAULT_SAFE_ODDS_RULE.classify(probability)
    return Prediction(
        fixture=fixture,
        market=MarketOdds("book", fixture.source_id, MarketFamily.UNKNOWN, "Demo market", selection, odds),
        model_probability=probability,
        implied_probability=1 / odds,
        edge=probability - (1 / odds),
        confidence=band.value,
        safe_odds_band=band,
        reason="Test prediction",
    )

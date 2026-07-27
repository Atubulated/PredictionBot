from datetime import datetime

from predictionbot.accumulator import build_accumulator, build_progressive_accumulator
from predictionbot.domain import Fixture, MarketFamily, MarketOdds, Prediction, Team
from predictionbot.engine import demo_accumulator_predictions
from predictionbot.risk import SafeOddsBand


def test_accumulator_reaches_demo_target_odds() -> None:
    accumulator = build_accumulator(demo_accumulator_predictions(), target_odds=10)

    assert accumulator.reached_target
    assert accumulator.total_odds >= 10
    assert all(leg.safe_odds_band == SafeOddsBand.VERY_SAFE for leg in accumulator.legs)


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

    accumulator = build_accumulator(predictions, target_odds=1.2)

    assert accumulator.legs == []


def test_progressive_accumulator_relaxes_risk_to_reach_bigger_target() -> None:
    accumulator = build_progressive_accumulator(demo_accumulator_predictions(), target_odds=1000)

    assert accumulator.reached_target
    assert SafeOddsBand.VERY_SAFE.value in accumulator.risk_bands_used
    assert SafeOddsBand.SAFE.value in accumulator.risk_bands_used
    assert SafeOddsBand.MEDIUM_RISK.value in accumulator.risk_bands_used


def test_progressive_accumulator_reports_unreachable_target() -> None:
    accumulator = build_progressive_accumulator(
        demo_accumulator_predictions(),
        target_odds=1_000_000_000,
        max_risk_band=SafeOddsBand.HIGH_RISK,
    )

    assert not accumulator.reached_target


def _prediction(fixture: Fixture, selection: str, odds: float, probability: float) -> Prediction:
    if probability >= 0.90:
        band = SafeOddsBand.VERY_SAFE
    elif probability >= 0.80:
        band = SafeOddsBand.SAFE
    elif probability >= 0.65:
        band = SafeOddsBand.MEDIUM_RISK
    else:
        band = SafeOddsBand.HIGH_RISK
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

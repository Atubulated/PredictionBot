from __future__ import annotations

from dataclasses import dataclass
from math import prod

from predictionbot.domain import Prediction
from predictionbot.risk import RISK_LADDER, SafeOddsBand


@dataclass(frozen=True)
class Accumulator:
    target_odds: float
    legs: list[Prediction]
    max_risk_band: SafeOddsBand

    @property
    def total_odds(self) -> float:
        return round(prod(leg.market.odds for leg in self.legs), 3)

    @property
    def combined_probability(self) -> float:
        return round(prod(leg.model_probability for leg in self.legs), 4)

    @property
    def reached_target(self) -> bool:
        return self.total_odds >= self.target_odds

    @property
    def risk_bands_used(self) -> list[str]:
        seen = []
        for leg in self.legs:
            value = leg.safe_odds_band.value
            if value not in seen:
                seen.append(value)
        return seen


def build_accumulator(
    predictions: list[Prediction],
    target_odds: float,
    band: SafeOddsBand = SafeOddsBand.VERY_SAFE,
    max_legs: int = 12,
) -> Accumulator:
    return build_progressive_accumulator(
        predictions=predictions,
        target_odds=target_odds,
        max_risk_band=band,
        max_legs=max_legs,
    )


def build_progressive_accumulator(
    predictions: list[Prediction],
    target_odds: float,
    max_risk_band: SafeOddsBand = SafeOddsBand.HIGH_RISK,
    max_legs: int = 30,
) -> Accumulator:
    if target_odds <= 1:
        raise ValueError("Target odds must be greater than 1.0")
    if max_legs < 1:
        raise ValueError("Accumulator must allow at least one leg")

    allowed_bands = _allowed_bands(max_risk_band)

    legs = []
    used_fixtures = set()
    used_outcomes = set()
    running_odds = 1.0

    for risk_band in allowed_bands:
        ranked = _rank_predictions(predictions, risk_band)
        for prediction in ranked:
            fixture_key = (prediction.fixture.source, prediction.fixture.source_id)
            outcome_key = (
                prediction.fixture.source,
                prediction.fixture.source_id,
                prediction.market.market.casefold(),
                prediction.market.selection.casefold(),
            )
            if fixture_key in used_fixtures or outcome_key in used_outcomes:
                continue

            legs.append(prediction)
            used_fixtures.add(fixture_key)
            used_outcomes.add(outcome_key)
            running_odds *= prediction.market.odds

            if running_odds >= target_odds or len(legs) >= max_legs:
                return Accumulator(target_odds=target_odds, legs=legs, max_risk_band=max_risk_band)

    return Accumulator(target_odds=target_odds, legs=legs, max_risk_band=max_risk_band)


def _allowed_bands(max_risk_band: SafeOddsBand) -> list[SafeOddsBand]:
    max_index = RISK_LADDER.index(max_risk_band)
    return RISK_LADDER[: max_index + 1]


def _rank_predictions(predictions: list[Prediction], risk_band: SafeOddsBand) -> list[Prediction]:
    eligible = [
        prediction
        for prediction in predictions
        if prediction.safe_odds_band == risk_band and prediction.market.odds > 1
    ]
    return sorted(
        eligible,
        key=lambda prediction: (
            -prediction.model_probability,
            -prediction.edge,
            prediction.market.odds,
        ),
    )

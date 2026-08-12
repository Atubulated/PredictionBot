from __future__ import annotations

from dataclasses import dataclass
from math import prod

from predictionbot.domain import MarketFamily, Prediction, PredictionSource
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
    band: SafeOddsBand = SafeOddsBand.HIGH_RISK,
    max_legs: int = 12,
    max_odds_per_leg: float = 2.0,
) -> Accumulator:
    return build_progressive_accumulator(
        predictions=predictions,
        target_odds=target_odds,
        max_risk_band=band,
        max_legs=max_legs,
        max_odds_per_leg=max_odds_per_leg,
    )


def build_progressive_accumulator(
    predictions: list[Prediction],
    target_odds: float,
    max_risk_band: SafeOddsBand = SafeOddsBand.HIGH_RISK,
    max_legs: int = 30,
    max_odds_per_leg: float = 2.0,
    value_first: bool = False,
    filler_families: set[MarketFamily] | None = None,
    filler_caps: dict[MarketFamily, int] | None = None,
) -> Accumulator:
    """Greedily assemble legs up the risk ladder until the target odds are met.

    ``value_first`` ranks by model EDGE (value over the book) instead of raw
    probability, so a genuine 2.90 out-ranks a no-value 1.35 lock instead of the
    reverse. Off by default so cli.py and the existing tests keep their behavior.

    ``filler_families`` demotes hedge markets (Asian Handicap, Double Chance) to a
    last resort: primary markets fill the slip first, and a filler leg is only
    pulled — capped per family by ``filler_caps`` — when the target still hasn't
    been reached. This is what stops the slip from being a stack of near-identical
    hedges when decisive, data-fit markets were available.
    """
    if target_odds <= 1:
        raise ValueError("Target odds must be greater than 1.0")
    if max_legs < 1:
        raise ValueError("Accumulator must allow at least one leg")

    filler_families = filler_families or set()
    filler_caps = filler_caps or {}

    allowed_bands = _allowed_bands(max_risk_band)

    state = _FillState()

    # Pass 1: primary (non-filler) markets only, so the backbone of the slip is
    # the market each game's data actually supports.
    primary = [p for p in predictions if p.market.family not in filler_families]
    result = _fill(
        primary, allowed_bands, target_odds, max_legs, max_odds_per_leg,
        value_first, state,
    )
    if result is not None:
        return result

    # Pass 2: only if we're still short of the target, top up with filler
    # (hedge) legs — but no more than filler_caps allows of each family.
    if filler_families:
        filler = [p for p in predictions if p.market.family in filler_families]
        result = _fill(
            filler, allowed_bands, target_odds, max_legs, max_odds_per_leg,
            value_first, state, family_caps=filler_caps,
        )
        if result is not None:
            return result

    return Accumulator(target_odds=target_odds, legs=state.legs, max_risk_band=max_risk_band)


class _FillState:
    """Mutable accumulator-in-progress shared across the primary/filler passes."""

    def __init__(self) -> None:
        self.legs: list[Prediction] = []
        self.used_fixtures: set = set()
        self.used_outcomes: set = set()
        self.family_counts: dict = {}
        self.running_odds: float = 1.0


def _fill(
    predictions: list[Prediction],
    allowed_bands: list[SafeOddsBand],
    target_odds: float,
    max_legs: int,
    max_odds_per_leg: float,
    value_first: bool,
    state: _FillState,
    family_caps: dict[MarketFamily, int] | None = None,
) -> Accumulator | None:
    """Add legs from ``predictions`` into ``state``; return a finished Accumulator
    the moment the target or leg cap is hit, else None (caller may run more passes)."""
    family_caps = family_caps or {}
    for risk_band in allowed_bands:
        ranked = _rank_predictions(predictions, risk_band, value_first=value_first)
        for prediction in ranked:
            # STRICT GUARDRAIL: Prevent single massive odds from hijacking the accumulator
            if prediction.market.odds > max_odds_per_leg:
                continue

            family = prediction.market.family
            if family in family_caps and state.family_counts.get(family, 0) >= family_caps[family]:
                continue

            fixture_key = (prediction.fixture.source, prediction.fixture.source_id)
            outcome_key = (
                prediction.fixture.source,
                prediction.fixture.source_id,
                prediction.market.market.casefold(),
                prediction.market.selection.casefold(),
            )
            if fixture_key in state.used_fixtures or outcome_key in state.used_outcomes:
                continue

            state.legs.append(prediction)
            state.used_fixtures.add(fixture_key)
            state.used_outcomes.add(outcome_key)
            state.family_counts[family] = state.family_counts.get(family, 0) + 1
            state.running_odds *= prediction.market.odds

            if state.running_odds >= target_odds or len(state.legs) >= max_legs:
                return Accumulator(
                    target_odds=target_odds, legs=state.legs, max_risk_band=allowed_bands[-1]
                )

    return None


def _allowed_bands(max_risk_band: SafeOddsBand) -> list[SafeOddsBand]:
    max_index = RISK_LADDER.index(max_risk_band)
    return RISK_LADDER[: max_index + 1]


def _is_model_backed(prediction: Prediction) -> bool:
    """True when a prediction came from a real scorer, not the consensus fallback.

    Works for both frozen Prediction objects and duck-typed MockPrediction:
    anything without a `source` attribute is treated as model-backed so legacy
    paths that never set provenance keep their old ranking behavior.
    """
    source = getattr(prediction, "source", None)
    if source is None:
        return True
    return getattr(source, "value", source) != PredictionSource.CONSENSUS.value


def _rank_predictions(
    predictions: list[Prediction],
    risk_band: SafeOddsBand,
    value_first: bool = False,
) -> list[Prediction]:
    eligible = [
        prediction
        for prediction in predictions
        if prediction.safe_odds_band == risk_band and prediction.market.odds > 1
    ]
    if value_first:
        # Value (edge) leads: a real +8% at 2.90 beats a no-value lock at 1.35.
        # Probability only breaks ties. This is what surfaces the market the
        # game's data supports instead of the shortest price on the board.
        key = lambda prediction: (
            not _is_model_backed(prediction),  # model-backed legs first
            -prediction.edge,
            -prediction.model_probability,
            prediction.market.odds,
        )
    else:
        key = lambda prediction: (
            not _is_model_backed(prediction),  # model-backed legs first
            -prediction.model_probability,
            -prediction.edge,
            prediction.market.odds,
        )
    return sorted(eligible, key=key)
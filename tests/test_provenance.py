from datetime import datetime

from predictionbot.accumulator import build_progressive_accumulator, _is_model_backed
from predictionbot.domain import (
    Fixture,
    MarketFamily,
    MarketOdds,
    Prediction,
    PredictionSource,
    Team,
)
from predictionbot.risk import SafeOddsBand


def _prediction(source_id, selection, odds, probability, provenance):
    fixture = Fixture("demo", source_id, datetime.now(), Team(f"H{source_id}"), Team(f"A{source_id}"))
    return Prediction(
        fixture=fixture,
        market=MarketOdds("book", source_id, MarketFamily.TOTALS, "Total Goals", selection, odds),
        model_probability=probability,
        implied_probability=1 / odds,
        edge=probability - (1 / odds),
        confidence="safe",
        safe_odds_band=SafeOddsBand.SAFE,
        reason="test",
        source=provenance,
    )


def test_is_model_backed_distinguishes_source():
    model = _prediction("m1", "Over 1.5", 1.5, 0.85, PredictionSource.MODEL)
    consensus = _prediction("c1", "Over 1.5", 1.5, 0.85, PredictionSource.CONSENSUS)

    assert _is_model_backed(model) is True
    assert _is_model_backed(consensus) is False


def test_model_backed_legs_rank_ahead_of_consensus():
    # Consensus leg has a HIGHER probability but must still rank behind the model leg.
    consensus = _prediction("c1", "Over 1.5", 1.5, 0.90, PredictionSource.CONSENSUS)
    model = _prediction("m1", "Over 2.5", 1.8, 0.82, PredictionSource.MODEL)

    accumulator = build_progressive_accumulator(
        [consensus, model],
        target_odds=1.4,
        max_odds_per_leg=2.0,
    )

    assert len(accumulator.legs) == 1
    assert accumulator.legs[0].source == PredictionSource.MODEL


def test_consensus_used_only_as_topup_when_model_insufficient():
    model = _prediction("m1", "Over 1.5", 1.5, 0.85, PredictionSource.MODEL)
    consensus = _prediction("c1", "Over 1.5", 1.5, 0.85, PredictionSource.CONSENSUS)

    # Target requires two legs (1.5 * 1.5 = 2.25), so the consensus top-up is pulled in
    # only after the model leg.
    accumulator = build_progressive_accumulator(
        [consensus, model],
        target_odds=2.0,
        max_odds_per_leg=2.0,
    )

    assert len(accumulator.legs) == 2
    assert accumulator.legs[0].source == PredictionSource.MODEL
    assert accumulator.legs[1].source == PredictionSource.CONSENSUS


def test_missing_source_attribute_treated_as_model_backed():
    class Bare:
        source = None
        model_probability = 0.8

    assert _is_model_backed(Bare()) is True

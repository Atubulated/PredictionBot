"""Market-anchoring calibration in score_market.

The raw Poisson scorers can print over-confident probabilities (e.g. 97% Under
3.5, a +22% "edge") on any league. score_market blends that toward the bookmaker
line so the model is a *tilt* on an efficient market, not a replacement:

    calibrated = book + MODEL_MAX_TRUST × data_confidence × (model − book)

These lock in that contract: the edge shrinks, the raw value is preserved, deep
history keeps a solid (un-zeroed) edge, and thin history collapses toward the
market.
"""
from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Team
from predictionbot.engine import (
    DATA_CONFIDENCE_ANCHOR,
    MODEL_MAX_TRUST,
    score_market,
    score_totals_market,
)


def _totals_market(odds=1.40):
    return MarketOdds("book", "1", MarketFamily.TOTALS, "Total Goals Over/Under 1.5", "Over 1.5", odds, 1.5)


def _high_scoring_history(n_per_side: int):
    """n matches per side, all high-scoring so Over 1.5 is strongly favoured —
    the raw model prints a big, over-confident edge to calibrate down."""
    rows = []
    for i in range(n_per_side):
        rows.append(HistoricalMatch(None, "Home", f"H{i}", 3, 2))
    for i in range(n_per_side):
        rows.append(HistoricalMatch(None, "Away", f"A{i}", 2, 3))
    return rows


def test_calibration_shrinks_edge_and_preserves_raw():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    history = _high_scoring_history(int(DATA_CONFIDENCE_ANCHOR))  # deep -> conf 1.0

    raw = score_totals_market(fixture, _totals_market(), history, min_edge=0.0)
    cal = score_market(fixture, _totals_market(), history, min_edge=0.0)

    assert raw is not None and cal is not None
    # The uncalibrated model output is retained for transparency.
    assert cal.raw_model_probability == raw.model_probability
    # Edge is anchored: exactly trust × raw edge (trust = MODEL_MAX_TRUST at conf 1.0).
    assert cal.edge < raw.edge
    assert abs(cal.edge - MODEL_MAX_TRUST * raw.edge) < 1e-9
    # Calibrated probability sits BETWEEN the market and the raw model.
    assert cal.implied_probability < cal.model_probability < raw.model_probability


def test_deep_history_keeps_a_solid_edge():
    # The whole point of the fix: major-league depth must NOT be zeroed out.
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    cal = score_market(fixture, _totals_market(), _high_scoring_history(10), min_edge=0.0)

    assert cal is not None
    assert cal.data_confidence == 1.0
    assert cal.edge > 0.0  # still a real, bettable edge


def test_thin_history_collapses_toward_market():
    # 2 matches/side -> conf 0.25 -> trust 0.5*0.25 -> edge is a quarter of deep.
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    thin = score_market(fixture, _totals_market(), _high_scoring_history(2), min_edge=0.0)
    deep = score_market(fixture, _totals_market(), _high_scoring_history(8), min_edge=0.0)

    assert thin is not None and deep is not None
    # Same raw model view, but the thin pick is trusted far less vs the market.
    assert thin.edge < deep.edge
    assert thin.model_probability < deep.model_probability


def test_overconfident_probability_is_pulled_down():
    # Reproduce the "Model: 97% / +22% edge" shape: model loves the Over, book
    # prices it modestly. Calibration must haul the shown probability toward the
    # book instead of parading a 20-point disagreement as free money.
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    market = _totals_market(odds=1.33)  # implied ~0.752
    cal = score_market(fixture, market, _high_scoring_history(10), min_edge=0.0)
    raw = score_totals_market(fixture, market, _high_scoring_history(10), min_edge=0.0)

    assert cal is not None and raw is not None
    # If the raw model was over-confident, the calibrated number is strictly lower
    # and the edge is at most half of the raw disagreement.
    if raw.edge > 0:
        assert cal.model_probability < raw.model_probability
        assert cal.edge <= raw.edge * MODEL_MAX_TRUST + 1e-9

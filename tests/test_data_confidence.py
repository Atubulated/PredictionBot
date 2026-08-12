"""Root-cause calibration: score_market stamps a data_confidence on every pick
so the slip builder can drop thin-history over-confident legs, while deep-history
(major-league) fixtures keep full confidence and solid edges.

These lock in the additive contract:
  * the individual scorers keep the default 1.0 (tests that call them directly,
    and any legacy path, are untouched);
  * score_market (the single dispatcher both production paths flow through)
    derives confidence from the WEAKER side's sample depth, ramping to 1.0 at
    engine.DATA_CONFIDENCE_ANCHOR matches/side.
"""
from predictionbot.domain import Fixture, HistoricalMatch, MarketFamily, MarketOdds, Team
from predictionbot.engine import (
    DATA_CONFIDENCE_ANCHOR,
    score_market,
    score_totals_market,
)


def _totals_market():
    # Over 1.5 goals — trivially likely given the scoring histories below, so the
    # scorer returns a real positive-edge prediction we can inspect.
    return MarketOdds("book", "1", MarketFamily.TOTALS, "Total Goals Over/Under 1.5", "Over 1.5", 1.4, 1.5)


def _scoring_history(n_per_side: int):
    """n matches per side, all with goals so Over 1.5 is strongly favoured."""
    rows = []
    for i in range(n_per_side):
        rows.append(HistoricalMatch(None, "Home", f"H-Opp{i}", 2, 1))
    for i in range(n_per_side):
        rows.append(HistoricalMatch(None, "Away", f"A-Opp{i}", 1, 2))
    return rows


def test_deep_history_earns_full_confidence():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    history = _scoring_history(int(DATA_CONFIDENCE_ANCHOR))  # 8 per side → anchor met

    prediction = score_market(fixture, _totals_market(), history, min_edge=0.0)

    assert prediction is not None
    assert prediction.data_confidence == 1.0
    # Deep history still yields a solid, un-suppressed edge — the whole point of
    # the fix: major leagues in season produce buildable slips.
    assert prediction.edge > 0.0


def test_thin_history_is_low_confidence():
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    history = _scoring_history(3)  # 3 per side → 3/8 = 0.375

    prediction = score_market(fixture, _totals_market(), history, min_edge=0.0)

    assert prediction is not None
    # Below the slip builder's 0.75 floor → this leg gets quarantined, not staked.
    assert prediction.data_confidence < 0.75
    assert abs(prediction.data_confidence - 3 / DATA_CONFIDENCE_ANCHOR) < 1e-9


def test_confidence_tracks_the_weaker_side():
    # Home deep (8), Away thin (2): the pick is only as trustworthy as the thin side.
    rows = [HistoricalMatch(None, "Home", f"H{i}", 2, 1) for i in range(8)]
    rows += [HistoricalMatch(None, "Away", f"A{i}", 1, 2) for i in range(2)]
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))

    prediction = score_market(fixture, _totals_market(), rows, min_edge=0.0)

    assert prediction is not None
    assert abs(prediction.data_confidence - 2 / DATA_CONFIDENCE_ANCHOR) < 1e-9


def test_direct_scorer_keeps_default_confidence():
    # Calling a scorer directly (as the unit tests do) must NOT get the stamp —
    # this is what keeps the additive change from touching existing assertions.
    fixture = Fixture("test", "1", None, Team("Home"), Team("Away"))
    prediction = score_totals_market(fixture, _totals_market(), _scoring_history(3), min_edge=0.0)

    assert prediction is not None
    assert prediction.data_confidence == 1.0

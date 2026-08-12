"""Selection-policy tests: value-first ranking, primary-before-filler tiering,
filler caps, lay-goals-handicap exclusion, and the handicap line sign parse.

These lock in the "stop building slips out of stacked hedges" behavior:
the slip's backbone must be decisive, data-fit markets ranked by genuine edge,
with Asian Handicap / Double Chance only as capped last-resort filler.
"""
import re

from predictionbot.accumulator import build_progressive_accumulator
from predictionbot.domain import (
    Fixture,
    MarketFamily,
    MarketOdds,
    Prediction,
    PredictionSource,
    Team,
)
from predictionbot.risk import SafeOddsBand


def _leg(fixture_id, family, selection, odds, edge, probability, band=SafeOddsBand.HIGH_RISK):
    return Prediction(
        fixture=Fixture("book", fixture_id, None, Team("Home"), Team("Away")),
        market=MarketOdds("book", fixture_id, family, f"{family.value}", selection, odds),
        model_probability=probability,
        implied_probability=1 / odds,
        edge=edge,
        confidence=band.value,
        safe_odds_band=band,
        reason="test",
        source=PredictionSource.MODEL,
    )


def test_value_first_prefers_a_real_edge_over_a_no_value_lock():
    # A genuine +9% at 2.90 must out-rank a no-value +1% lock at 1.35 — the whole
    # point: value, not the shortest price.
    lock = _leg("f1", MarketFamily.TOTALS, "Under 3.5", 1.35, 0.01, 0.75)
    value = _leg("f2", MarketFamily.TOTALS, "Over 2.5", 2.90, 0.09, 0.43)

    acca = build_progressive_accumulator(
        [lock, value], target_odds=1.2, max_legs=1, max_odds_per_leg=3.5, value_first=True
    )

    assert len(acca.legs) == 1
    assert acca.legs[0].market.odds == 2.90  # value leg chosen first


def test_primary_markets_fill_before_filler():
    # A primary (totals) leg reaches the target on its own; the higher-edge
    # Double Chance filler must NOT be used.
    primary = _leg("f1", MarketFamily.TOTALS, "Over 2.5", 1.80, 0.05, 0.61)
    filler = _leg("f2", MarketFamily.DOUBLE_CHANCE, "Draw/Away", 1.55, 0.20, 0.85)

    acca = build_progressive_accumulator(
        [primary, filler], target_odds=1.7, max_legs=6, max_odds_per_leg=3.5,
        value_first=True, filler_families={MarketFamily.HANDICAP, MarketFamily.DOUBLE_CHANCE},
        filler_caps={MarketFamily.HANDICAP: 1, MarketFamily.DOUBLE_CHANCE: 1},
    )

    families = [leg.market.family for leg in acca.legs]
    assert MarketFamily.TOTALS in families
    assert MarketFamily.DOUBLE_CHANCE not in families


def test_filler_used_only_to_close_the_gap_and_capped():
    # Primary alone (1.30) can't reach 2.0; filler tops up but is capped at 1 DC.
    primary = _leg("f1", MarketFamily.TOTALS, "Over 1.5", 1.30, 0.06, 0.83)
    dc1 = _leg("f2", MarketFamily.DOUBLE_CHANCE, "Home/Draw", 1.50, 0.10, 0.77)
    dc2 = _leg("f3", MarketFamily.DOUBLE_CHANCE, "Draw/Away", 1.45, 0.08, 0.77)

    acca = build_progressive_accumulator(
        [primary, dc1, dc2], target_odds=2.0, max_legs=6, max_odds_per_leg=3.5,
        value_first=True, filler_families={MarketFamily.HANDICAP, MarketFamily.DOUBLE_CHANCE},
        filler_caps={MarketFamily.HANDICAP: 1, MarketFamily.DOUBLE_CHANCE: 1},
    )

    dc_legs = [leg for leg in acca.legs if leg.market.family == MarketFamily.DOUBLE_CHANCE]
    assert len(dc_legs) == 1  # capped, even though two DC legs were available
    assert dc_legs[0].market.odds == 1.50  # the higher-edge one picked first


def test_safety_first_prefers_the_safer_leg_over_a_fatter_edge_longshot():
    # Reproduces the bad slip: a +12% edge on a 43.6% away win (@3.20) is a
    # coin-flip; a smaller-edge 68% leg (@1.55) is the safer bet. Production ranks
    # safety-first (value_first=False), so the safe leg must be chosen, NOT the
    # longshot with the bigger "value".
    longshot = _leg("f1", MarketFamily.MATCH_WINNER, "Away", 3.20, 0.124, 0.436)
    safe = _leg("f2", MarketFamily.MATCH_WINNER, "Home", 1.55, 0.05, 0.68)

    acca = build_progressive_accumulator(
        [longshot, safe], target_odds=1.2, max_legs=1, max_odds_per_leg=3.5,
        value_first=False,
    )

    assert len(acca.legs) == 1
    assert acca.legs[0].model_probability == 0.68  # safer leg leads, not the coin-flip


def test_no_filler_families_keeps_legacy_single_pass():
    # Without filler config, handicaps rank as equals (backward-compatible path).
    primary = _leg("f1", MarketFamily.TOTALS, "Over 2.5", 1.50, 0.04, 0.71)
    handicap = _leg("f2", MarketFamily.HANDICAP, "Home +1", 1.60, 0.12, 0.75)

    acca = build_progressive_accumulator(
        [primary, handicap], target_odds=100, max_legs=6, max_odds_per_leg=3.5, value_first=True
    )

    families = {leg.market.family for leg in acca.legs}
    assert MarketFamily.HANDICAP in families  # not demoted when no filler set given


# --- MockMarket.line sign parse (mirrors telegram_bot.MockMarket.line) ---
# telegram_bot can't be imported without live Supabase creds, so we assert the
# exact regex it uses. If you change one, change the other.
_LINE_RE = r"[-+]?\d+(?:\.\d+)?"


def _parse_line(selection: str) -> float:
    matches = re.findall(_LINE_RE, selection)
    return float(matches[-1]) if matches else 0.0


def test_handicap_line_keeps_sign_on_whole_numbers():
    # The bug: "Away -2" used to parse to +2.0 (sign dropped on integers), so the
    # scorer graded the wrong side of the line.
    assert _parse_line("Away -2") == -2.0
    assert _parse_line("Home +1.5") == 1.5
    assert _parse_line("Over 2.5") == 2.5
    assert _parse_line("Away +2") == 2.0
    assert _parse_line("Home -1") == -1.0

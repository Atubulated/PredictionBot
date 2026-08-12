from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, replace
from difflib import get_close_matches
from unicodedata import normalize as _nfd

from predictionbot.domain import (
    Fixture,
    HistoricalMatch,
    MarketFamily,
    Prediction,
    Team,
)
from predictionbot.engine import score_fixture_markets
from predictionbot.features import index_history_by_team, history_for_fixture
from predictionbot.risk import SafeOddsBand
from predictionbot.sources.bet9ja import Bet9jaListedEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanResult:
    events_scanned: int
    markets_scored: int
    predictions: list[Prediction]
    raw_predictions: int = 0
    dropped_by_adjustment: int = 0
    fuzzy_renamed_events: int = 0


# -------------------------------------------------------------------------- #
# Fixture normalisation (fixes 'MockFixture' object has no attribute 'source')
# -------------------------------------------------------------------------- #
def _ensure_fixture_attrs(fixture):
    """Guarantee .source and .source_id exist (the accumulator needs them)."""
    if hasattr(fixture, "source") and hasattr(fixture, "source_id"):
        return fixture

    # Tier 1: shallow-copy and patch missing attrs (keeps MockFixture extras)
    try:
        clone = copy.copy(fixture)
        if not hasattr(clone, "source"):
            clone.source = "wide_net"
        if not hasattr(clone, "source_id"):
            clone.source_id = str(
                getattr(clone, "id", None)
                or getattr(clone, "fixture_id", None)
                or f"{clone.home.name}|{clone.away.name}"
            )
        return clone
    except Exception:
        pass

    # Tier 2: wrap into a real domain Fixture
    try:
        home = fixture.home if isinstance(fixture.home, Team) else Team(
            name=getattr(fixture.home, "name", str(fixture.home)))
        away = fixture.away if isinstance(fixture.away, Team) else Team(
            name=getattr(fixture.away, "name", str(fixture.away)))
        return Fixture(
            source="wide_net",
            source_id=str(
                getattr(fixture, "id", None)
                or getattr(fixture, "fixture_id", None)
                or f"{home.name}|{away.name}"
            ),
            starts_at=getattr(fixture, "starts_at", None)
            or getattr(fixture, "kickoff", None)
            or getattr(fixture, "date", None),
            home=home,
            away=away,
            league=getattr(fixture, "league", None),
        )
    except Exception:
        return fixture


# -------------------------------------------------------------------------- #
# Team-name normalisation
# -------------------------------------------------------------------------- #
_STRIP_SUFFIXES = ("fc", "sc", "cf", "ac", "club", "team")


def _canon(name: str) -> str:
    if not name:
        return ""
    text = _nfd("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ")
    parts = text.split()
    if len(parts) > 1 and parts[-1] in _STRIP_SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts).strip()


def _build_history_alias_map(history: list[HistoricalMatch]) -> dict[str, str]:
    alias: dict[str, str] = {}
    for match in history:
        for attr in ("home", "away", "home_team", "away_team", "home_name", "away_name"):
            value = getattr(match, attr, None)
            raw = value if isinstance(value, str) else getattr(value, "name", None)
            if raw:
                alias.setdefault(_canon(raw), str(raw))
    return alias


def _resolve_team_name(name: str, alias: dict[str, str]) -> str | None:
    canon = _canon(name)
    if not canon:
        return None
    if canon in alias:
        return alias[canon]
    hits = get_close_matches(canon, list(alias.keys()), n=1, cutoff=0.85)
    return alias[hits[0]] if hits else None


def _rename_fixture(fixture, new_home: str, new_away: str):
    try:
        return replace(
            fixture,
            home=replace(fixture.home, name=new_home),
            away=replace(fixture.away, name=new_away),
        )
    except Exception:
        pass
    try:
        clone = copy.deepcopy(fixture)
        clone.home.name = new_home
        clone.away.name = new_away
        return clone
    except Exception:
        return fixture


# -------------------------------------------------------------------------- #
# Supabase team stats (ONE bulk query)
# -------------------------------------------------------------------------- #
def _fetch_team_stats(names: set[str]) -> dict[str, dict]:
    if not names:
        return {}
    try:
        from supabase import create_client
        url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
        if not (url and key):
            return {}
        client = create_client(url, key)
        rows = (
            client.table("team_stats")
            .select("team_name, wins, matches_played, xg_difference")
            .in_("team_name", sorted(names))
            .execute()
        )
        stats: dict[str, dict] = {}
        for row in rows.data or []:
            played = max(1, int(row.get("matches_played") or 1))
            stats[row["team_name"]] = {
                "win_rate": float(row.get("wins") or 0) / played,
                "xg_diff": float(row.get("xg_difference") or 0),
            }
        return stats
    except Exception as exc:
        logger.warning("team_stats fetch failed: %s", exc)
        return {}


# -------------------------------------------------------------------------- #
# Market-aware probability adjustment
# -------------------------------------------------------------------------- #
def _classify(text: str) -> str | None:
    t = text.strip().lower()
    if any(k in t for k in ("over", "under", "btts", "gg", "total", "goal")):
        return "totals"
    if "home" in t or t in ("1", "w1", "hw"):
        return "home"
    if "away" in t or t in ("2", "w2", "aw"):
        return "away"
    if "draw" in t or t in ("x", "d"):
        return "draw"
    return None


def _selection_side(pred: Prediction) -> str | None:
    for obj in (pred, getattr(pred, "market", None)):
        if obj is None:
            continue
        for attr in ("selection", "pick", "outcome", "side", "label", "name", "title"):
            value = getattr(obj, attr, None)
            if isinstance(value, str) and value.strip():
                side = _classify(value)
                if side:
                    return side
    return None


def _adjust_probability(pred: Prediction, side: str | None,
                        home_stats: dict | None, away_stats: dict | None) -> float:
    p = pred.model_probability
    if side == "home" and home_stats:
        p = p * 0.7 + home_stats["win_rate"] * 0.3
    elif side == "away" and away_stats:
        p = p * 0.7 + away_stats["win_rate"] * 0.3
    elif side == "totals" and home_stats and away_stats:
        xg = (home_stats["xg_diff"] + away_stats["xg_diff"]) / 20.0
        p = p + xg * 0.05
    return max(0.01, min(0.99, p))


def _rebuild(pred: Prediction, prob: float, edge: float) -> Prediction:
    try:
        return replace(pred, model_probability=prob, edge=edge)
    except Exception:
        try:
            pred.model_probability = prob
            pred.edge = edge
        except Exception:
            pass
        return pred


# -------------------------------------------------------------------------- #
# CONSENSUS FALLBACK (market odds become the model when history is missing)
# -------------------------------------------------------------------------- #
def _build_consensus_predictions(
    events: list[Bet9jaListedEvent],
    market_families: set[MarketFamily] | None,
) -> list[Prediction]:
    preds = []
    for event in events:
        fixture = _ensure_fixture_attrs(event.fixture)
        markets = event.markets
        if market_families:
            markets = [m for m in markets if m.family in market_families]

        for market in markets:
            # Keep only realistic accumulator legs (no 1.01 junk, no lottery tickets)
            if market.odds < 1.20 or market.odds > 5.00:
                continue

            implied = 1.0 / market.odds

            if market.odds < 1.60:
                band = SafeOddsBand.VERY_SAFE
            elif market.odds < 2.20:
                band = SafeOddsBand.SAFE
            elif market.odds < 3.50:
                band = SafeOddsBand.MEDIUM_RISK
            else:
                band = SafeOddsBand.HIGH_RISK

            preds.append(Prediction(
                fixture=fixture,
                market=market,
                model_probability=implied,
                implied_probability=implied,
                                edge=0.0001,          # hair above zero so "> 0" edge filters accept it
                confidence=band.value,  # speak the same language as risk_progression
                safe_odds_band=band,
                reason=f"Market consensus (no history). Implied prob: {implied:.0%}",
            ))
    return preds


# -------------------------------------------------------------------------- #
# Main scan
# -------------------------------------------------------------------------- #
def scan_events(
    events: list[Bet9jaListedEvent],
    history: list[HistoricalMatch],
    min_edge: float = -0.15,
    market_families: set[MarketFamily] | None = None,
) -> ScanResult:
    alias = _build_history_alias_map(history)
    logger.info("📚 History alias map: %d matches, %d unique team names.",
                len(history), len(alias))

    # Bucket the pool by team once so each score_fixture_markets call only sees
    # the two teams' matches instead of the whole list (the profile builders
    # re-filter and take [-limit:], so results are identical).
    team_history = index_history_by_team(history)

    all_names = set()
    for event in events:
        all_names.add(event.fixture.home.name)
        all_names.add(event.fixture.away.name)
    team_stats = _fetch_team_stats(all_names)

    predictions: list[Prediction] = []
    engine_preds = 0
    dropped = 0
    fuzzy_renamed = 0
    name_hits = 0

    for event in events:
        home_name = event.fixture.home.name
        away_name = event.fixture.away.name
        canon_home = _resolve_team_name(home_name, alias)
        canon_away = _resolve_team_name(away_name, alias)
        name_hits += bool(canon_home) + bool(canon_away)

        fixture = _ensure_fixture_attrs(event.fixture)
        if canon_home and canon_away and (canon_home != home_name or canon_away != away_name):
            fixture = _rename_fixture(fixture, canon_home, canon_away)
            fuzzy_renamed += 1
            logger.info("🔁 Fuzzy rename: '%s'->'%s', '%s'->'%s'",
                        home_name, canon_home, away_name, canon_away)

        markets = event.markets
        if market_families:
            markets = [m for m in markets if m.family in market_families]

        fx_history = history_for_fixture(team_history, fixture.home.name, fixture.away.name)
        scored = score_fixture_markets(fixture, markets, fx_history, min_edge=min_edge)
        if not scored and fixture is not event.fixture:
            orig_history = history_for_fixture(
                team_history, event.fixture.home.name, event.fixture.away.name
            )
            scored = score_fixture_markets(event.fixture, markets, orig_history, min_edge=min_edge)
        engine_preds += len(scored)

        home_stats = team_stats.get(canon_home or home_name) or team_stats.get(home_name)
        away_stats = team_stats.get(canon_away or away_name) or team_stats.get(away_name)

        kept = []
        for pred in scored:
            side = _selection_side(pred)
            new_p = _adjust_probability(pred, side, home_stats, away_stats)
            new_edge = (pred.market.odds * new_p) - 1.0
            if new_edge < min_edge:
                dropped += 1
                continue
            kept.append(_rebuild(pred, new_p, new_edge))
        predictions.extend(kept)

    predictions.sort(key=lambda pr: (-pr.model_probability, -pr.edge))

    logger.info(
        "📊 Scan summary: events=%d | engine_preds=%d | final_preds=%d | "
        "fuzzy_renames=%d | name_coverage=%d/%d",
        len(events), engine_preds, len(predictions),
        fuzzy_renamed, name_hits, len(events) * 2,
    )

    # === CONSENSUS FALLBACK ===
    if engine_preds == 0:
        logger.warning("⚠️ Quant engine found 0 predictions (missing history). "
                       "Switching to CONSENSUS MODE (Market Odds).")
        predictions = _build_consensus_predictions(events, market_families)
        predictions.sort(key=lambda pr: (-pr.model_probability, -pr.edge))
        logger.info("🤖 Consensus Mode active: generated %d potential legs.", len(predictions))
    # ==========================

    if predictions:
        top = predictions[0]
        logger.info("🏆 Top pick: prob=%.2f edge=%.2f odds=%.2f | %s",
                    top.model_probability, top.edge, top.market.odds, top.reason)
    else:
        logger.warning("⚠️ 0 predictions even in Consensus mode. Check odds data.")

    return ScanResult(
        events_scanned=len(events),
        markets_scored=engine_preds,
        predictions=predictions,
        raw_predictions=engine_preds,
        dropped_by_adjustment=dropped,
        fuzzy_renamed_events=fuzzy_renamed,
    )
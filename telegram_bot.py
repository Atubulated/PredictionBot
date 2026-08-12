# telegram_bot.py
import asyncio
import logging
import os
import re
import sys
import uuid
import datetime
import threading
import urllib.request
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from supabase import create_client, Client

from predictionbot.config import load_settings
from predictionbot.http import JsonHttpClient, HttpClientError
from predictionbot.intent_router import IntentRouter
from predictionbot.sources.bet9ja import Bet9jaClient, BET9JA_LEAGUES
from predictionbot.sources.openfootball import OpenFootballClient
from predictionbot.sources.api_football import ApiFootballProvider, ApiFootballNetworkError
from predictionbot.accumulator import build_progressive_accumulator
from predictionbot.domain import MarketFamily, HistoricalMatch
from predictionbot.risk import SafeOddsBand
from predictionbot.matching import FixtureMatcher
from predictionbot.evaluator import evaluate_bet, is_corners_market
from predictionbot.league_calendar import (
    MAJOR_LEAGUES,
    format_comeback_hint,
    format_leagues_status,
)
from predictionbot.stats import StatCode
from predictionbot.engine import score_market
from predictionbot.features import index_history_by_team, history_for_fixture
from predictionbot.settlement import evaluate_slip_settlement

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

settings = load_settings()
http = JsonHttpClient(settings.user_agent)
bet9ja = Bet9jaClient(http)
router = IntentRouter(http, api_key=settings.nvidia_api_key)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in .env")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

WAT = ZoneInfo("Africa/Lagos")
CONSENSUS_FALLBACK = True


def _leg_is_model_backed(leg) -> bool:
    """True when a leg came from a real scorer, not the consensus fallback.

    Mirrors accumulator._is_model_backed: anything without a `source`
    attribute is treated as model-backed so legacy paths keep old behavior.
    """
    from predictionbot.domain import PredictionSource
    source = getattr(leg, "source", None)
    if source is None:
        return True
    return getattr(source, "value", source) != PredictionSource.CONSENSUS.value


def _combined_probability(legs) -> float:
    """Product of the legs' model win probabilities — the acca's real win chance."""
    p = 1.0
    for leg in legs:
        p *= max(0.0, min(1.0, leg.model_probability))
    return p


def _acca_risk_label(legs) -> str:
    """Risk band computed from the COMBINED win probability, not the worst leg.

    A 17-fold at 1.3% and a single 64% leg are worlds apart; the old
    worst-leg label called both 'High Risk'. This grades the whole slip.
    """
    p = _combined_probability(legs)
    if p >= 0.50:
        return "Low"
    if p >= 0.30:
        return "Moderate"
    if p >= 0.15:
        return "High"
    if p >= 0.05:
        return "Very High"
    return "Extreme (Lottery)"


# A punt is 2-6 legs. Beyond that the combined probability collapses and the
# slip stops being a bet and becomes a lottery ticket, however big the odds.
PRO_MAX_LEGS = 15
# Single-leg edges above this are almost always model error, not free money.
SUSPICIOUS_EDGE = 0.15
# A leg is only trustworthy if the model had real history behind it. 0.75 ==
# ~6 recent matches per side (see engine.DATA_CONFIDENCE_ANCHOR = 8). Major
# leagues in season clear this; thin pre-season / lower-tier fixtures don't and
# get dropped from the slip rather than printing an over-confident edge.
MIN_DATA_CONFIDENCE = 0.75
# Per-leg odds ceiling. High enough that a genuine-value 2.90 can anchor a slip
# (a real edge at 2.90 is safer than a no-value 1.35 lock), but low enough that a
# single longshot can't hijack the accumulator's price.
MAX_ODDS_PER_LEG = 3.5
# SAFETY FLOOR — the real fix for "two coin-flips taped together". A leg is judged
# by its model WIN PROBABILITY, not its price: no leg under this makes an acca, so
# a 41.9% "Under 1.5" is out even if its edge looks fat, while a genuine 62% at
# 2.90 is in. Legs are ranked safest-first, so the highest-probability picks lead
# the slip. Note the math: you CANNOT build 10x out of only 80%+ legs (80% ≈ 1.25
# odds, six of them ≈ 3.8x), so reaching a big target forces a few 60–70% legs —
# this floor just guarantees the worst leg is still a ~3-in-5 shot, not a toss-up.
MIN_LEG_PROBABILITY = 0.80
# Hedge markets. Asian Handicap and Double Chance are demoted to last-resort
# filler: the slip is built from decisive, data-fit markets first, and at most one
# of each of these is added, and only if the target still isn't reached.
FILLER_FAMILIES = {MarketFamily.HANDICAP, MarketFamily.DOUBLE_CHANCE}
FILLER_CAPS = {MarketFamily.HANDICAP: 1, MarketFamily.DOUBLE_CHANCE: 1}


def _slip_stats_line(legs) -> str:
    """The honest header stats: combined win chance, acca risk band, flag count.

    This is what turns a vanity odds figure into a professional read — the
    punter sees the real probability the whole slip lands, not just the price.
    """
    p = _combined_probability(legs)
    risk = _acca_risk_label(legs)
    if p > 0:
        prob_str = f"{p:.1%} (≈1 in {max(1, round(1 / p)):,})"
    else:
        prob_str = "≈0%"
    flagged = sum(1 for leg in legs if leg.edge > SUSPICIOUS_EDGE)
    line = f"🎲 *Win probability:* {prob_str}\n🌡️ *Risk:* {risk}"
    if flagged:
        line += (
            f"\n🚩 *{flagged} leg(s) flagged* "
            f"(edge > {SUSPICIOUS_EDGE:.0%} — likely model error, treat with caution)"
        )
    return line


def _format_slip_chunks(header: str, legs) -> list[str]:
    """Render legs into Telegram-sized message chunks under a given header."""
    message_chunks = []
    current_chunk = header
    for i, leg in enumerate(legs, 1):
        clean_label = re.sub(r'[_*\[\]()`]', '', leg.fixture.label).strip()
        clean_market = re.sub(r'[_*\[\]()`]', '', leg.market.market).strip()
        clean_selection = re.sub(r'[_*\[\]()`]', '', leg.market.selection).strip()
        source_tag = "🧠 Model" if _leg_is_model_backed(leg) else "📕 Book Consensus"
        flag = " ⚠️" if leg.edge > SUSPICIOUS_EDGE else ""
        leg_text = f"{i}. *{format_wat_time(leg.fixture.starts_at)}* - {clean_label}\n"
        leg_text += f"   ➔ *{clean_market} | {clean_selection}* @ {leg.market.odds:.2f}\n"
        leg_text += f"   📊 _Edge: +{leg.edge:.1%}{flag} (Model: {leg.model_probability:.1%})_ · _{source_tag}_\n\n"
        if len(current_chunk) + len(leg_text) > 3800:
            message_chunks.append(current_chunk)
            current_chunk = leg_text
        else:
            current_chunk += leg_text
    if current_chunk:
        message_chunks.append(current_chunk)
    return message_chunks

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".event_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")

def _cache_get(key: str, max_age_hours: float = 12.0):
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        age_h = (datetime.datetime.now().timestamp() - os.path.getmtime(path)) / 3600.0
        if age_h > max_age_hours:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _cache_set(key: str, payload):
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning(f"Cache write failed for {key}: {e}")

def format_wat_time(dt) -> str:
    if dt is None: return "TBD"
    if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(WAT).strftime("%b %d, %H:%M WAT")

SHOWING_FULL_SLIP = False
SUMMARY_TEXT = ""
FULL_TEXT = ""
EVENT_CACHE = {}
_SUPA_HISTORY_CACHE = {"ts": None, "rows": []}

OPENFOOTBALL_LEAGUE_FILES = {
    "premier_league": "en.1.json", "championship": "en.2.json",
    "league_one": "en.3.json", "league_two": "en.4.json",
    "bundesliga": "de.1.json", "bundesliga_2": "de.2.json",
    "laliga": "es.1.json", "ligue_1": "fr.1.json",
    "ligue_2": "fr.2.json", "serie_a": "it.1.json",
}

# Trusted-league calendar (MAJOR_LEAGUES whitelist, /leagues status, come-back hint)
# lives in predictionbot.league_calendar so it can be unit-tested without live creds.

@dataclass
class MockTeam:
    name: str

@dataclass
class MockFixture:
    source_id: str
    home: MockTeam
    away: MockTeam
    starts_at: datetime.datetime
    label: str
    league_name: str = ""
    source: str = "api_football"

@dataclass
class MockMarket:
    family: MarketFamily
    market: str
    selection: str
    odds: float

    @property
    def line(self) -> float:
        # Keep the sign on whole-number lines too: "Away -2" must parse to -2.0,
        # not +2.0. The old pattern ([-+]?\d*\.\d+|\d+) only kept the sign on
        # decimals, so integer Asian-handicap lines flipped sign and the handicap
        # scorer graded the wrong side of the line (a fat, bogus ~90%).
        matches = re.findall(r"[-+]?\d+(?:\.\d+)?", self.selection)
        return float(matches[-1]) if matches else 0.0

@dataclass
class MockEvent:
    fixture: MockFixture
    markets: list

class MockPrediction:
    def __init__(self, fixture, market, model_probability, edge, source=None, data_confidence=1.0):
        from predictionbot.domain import PredictionSource
        self.fixture = fixture
        self.market = market
        self.model_probability = model_probability
        self.edge = edge
        self.source = source or PredictionSource.CONSENSUS
        # Mirrors Prediction.data_confidence. Consensus mocks are quarantined out
        # before the confidence gate anyway, so the default is harmless.
        self.data_confidence = data_confidence

    @property
    def safe_odds_band(self):
        if self.model_probability >= 0.95: return SafeOddsBand.VERY_SAFE
        elif self.model_probability >= 0.80: return SafeOddsBand.SAFE
        elif self.model_probability >= 0.65: return SafeOddsBand.MEDIUM_RISK
        else: return SafeOddsBand.HIGH_RISK

def _optional_int(value):
    """Coerce a DB value to int, or None when missing/unparseable (stat markets gate on this)."""
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _optional_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 🛑 CONVERT HISTORY ROWS INTO HistoricalMatch OBJECTS (the engine needs objects, not dicts)
def _to_historical_match(item):
    """Coerce a dict or existing HistoricalMatch into a HistoricalMatch the engine can score.

    Returns None if the row lacks usable teams/scores, so bad rows are skipped
    instead of crashing score_market() with 'dict' object has no attribute 'home'.
    """
    if isinstance(item, HistoricalMatch):
        return item
    if isinstance(item, dict):
        home = item.get("home") or item.get("home_team")
        away = item.get("away") or item.get("away_team")
        hg = item.get("home_goals", item.get("home_score"))
        ag = item.get("away_goals", item.get("away_score"))
        if not home or not away or hg is None or ag is None:
            return None
        try:
            hg, ag = int(hg), int(ag)
        except (TypeError, ValueError):
            return None
        raw_date = item.get("date") or item.get("match_date")
        parsed_date = None
        if isinstance(raw_date, datetime.datetime):
            parsed_date = raw_date
        elif isinstance(raw_date, str) and raw_date:
            try:
                parsed_date = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                parsed_date = None
        return HistoricalMatch(
            date=parsed_date,
            home=str(home),
            away=str(away),
            home_goals=hg,
            away_goals=ag,
            league=item.get("league"),
            home_corners=_optional_int(item.get("home_corners")),
            away_corners=_optional_int(item.get("away_corners")),
            home_cards=_optional_int(item.get("home_cards")),
            away_cards=_optional_int(item.get("away_cards")),
            home_shots=_optional_int(item.get("home_shots")),
            away_shots=_optional_int(item.get("away_shots")),
            home_possession=_optional_float(item.get("home_possession")),
            away_possession=_optional_float(item.get("away_possession")),
            home_saves=_optional_int(item.get("home_saves")),
            away_saves=_optional_int(item.get("away_saves")),
            home_offsides=_optional_int(item.get("home_offsides")),
            away_offsides=_optional_int(item.get("away_offsides")),
            home_passes=_optional_int(item.get("home_passes")),
            away_passes=_optional_int(item.get("away_passes")),
            raw=item,
        )
    # Fallback: any object already exposing .home/.away is engine-compatible.
    if hasattr(item, "home") and hasattr(item, "away"):
        return item
    return None

def safe_get_market_family(intent_str) -> MarketFamily:
    if isinstance(intent_str, MarketFamily):
        return intent_str
    value = str(intent_str).strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "1x2": MarketFamily.MATCH_WINNER,
        "match_winner": MarketFamily.MATCH_WINNER,
        "match_winner_1x2": MarketFamily.MATCH_WINNER,
        "btts": MarketFamily.BOTH_TEAMS_TO_SCORE,
        "both_teams_score": MarketFamily.BOTH_TEAMS_TO_SCORE,
        "both_teams_to_score": MarketFamily.BOTH_TEAMS_TO_SCORE,
        "goals_over_under": MarketFamily.TOTALS,
        "over_under": MarketFamily.TOTALS,
        "total_goals": MarketFamily.TOTALS,
        "asian_handicap": MarketFamily.HANDICAP,
        "draw_no_bet": MarketFamily.HANDICAP,
    }
    if value in aliases:
        return aliases[value]
    try:
        return MarketFamily(value)
    except ValueError:
        return MarketFamily.UNKNOWN

def _name_of(x) -> str:
    if isinstance(x, str): return x
    if isinstance(x, dict): return str(x.get("name") or x.get("team") or "")
    return str(getattr(x, "name", "") or getattr(x, "team", "") or "")

def normalize_event(e):
    if isinstance(e, MockEvent):
        return e
    fx = e.get("fixture") if isinstance(e, dict) else getattr(e, "fixture", None)
    if fx is None:
        return None
    if isinstance(fx, MockFixture):
        fixture = fx
    else:
        home = _name_of(fx.get("home") if isinstance(fx, dict) else getattr(fx, "home", ""))
        away = _name_of(fx.get("away") if isinstance(fx, dict) else getattr(fx, "away", ""))
        label = (fx.get("label") if isinstance(fx, dict) else getattr(fx, "label", "")) or f"{home} vs {away}"
        starts = fx.get("starts_at") if isinstance(fx, dict) else getattr(fx, "starts_at", None)
        src_id = str(fx.get("source_id") if isinstance(fx, dict) else getattr(fx, "source_id", "") or label)
        league = (fx.get("league_name") if isinstance(fx, dict) else getattr(fx, "league_name", "")) or ""
        fixture = MockFixture(source_id=src_id, home=MockTeam(home), away=MockTeam(away),
                              starts_at=starts, label=label, league_name=league, source="bet9ja")
    markets = []
    raw_markets = e.get("markets") if isinstance(e, dict) else getattr(e, "markets", [])
    for m in raw_markets or []:
        try:
            g = (lambda k, d=None: m.get(k, d)) if isinstance(m, dict) else (lambda k, d=None: getattr(m, k, d))
            fam = safe_get_market_family(g("family"))
            markets.append(MockMarket(family=fam, market=str(g("market", "")),
                                      selection=str(g("selection", "")), odds=float(g("odds", 0))))
        except Exception:
            continue
    if not markets or fixture.starts_at is None:
        return None
    return MockEvent(fixture=fixture, markets=markets)

def fetch_global_events(target_date: date) -> list:
    logger.info(f"🌍 Fetching Wide Net events from API-Football for {target_date}...")
    api_key = settings.api_football_key
    if not api_key:
        logger.warning("API_FOOTBALL_KEY missing. Falling back to Bet9ja.")
        return []

    provider = ApiFootballProvider()
    cache_key = f"apifootball_{target_date.isoformat()}"
    raw = _cache_get(cache_key, max_age_hours=12)

    if raw is not None:
        fixtures = raw.get("fixtures", [])
        odds_map = raw.get("odds_map", {})
        logger.info(f" Cache hit: {len(fixtures)} fixtures for {target_date} (0 API calls).")
    else:
        try:
            fixtures = provider.fixtures_by_date(target_date.isoformat())
        except ApiFootballNetworkError as e:
            logger.error(f"API-Football network error for {target_date}: {e}")
            # Try to serve from stale cache if it exists and is < 48h old
            stale = _cache_get(cache_key, max_age_hours=48.0)
            if stale:
                logger.info(f"Serving STALE cache for {target_date} due to network error.")
                fixtures = stale.get("fixtures", [])
                odds_map = stale.get("odds_map", {})
            else:
                raise # Re-raise so telegram_bot knows the primary source is completely down
        except Exception as e:
            logger.error(f"Unexpected error fetching fixtures for {target_date}: {e}")
            fixtures = []
            
        logger.info(f" API-Football returned {len(fixtures)} fixtures for {target_date}.")

        odds_map = {}
        if fixtures:
            for page in range(1, 3):
                url = f"https://v3.football.api-sports.io/odds?date={target_date.isoformat()}&bookmaker=8&page={page}"
                req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        data = json.loads(response.read().decode())
                    if data.get("errors"):
                        logger.error(f"API-Football odds errors ({target_date} page {page}): {data['errors']}")
                    for item in data.get("response", []):
                        f_id = str(item["fixture"]["id"])
                        bms = item.get("bookmakers", [])
                        if bms:
                            odds_map[f_id] = bms[0].get("bets", [])
                    paging = data.get("paging", {})
                    if paging.get("current", 1) >= paging.get("total", 1):
                        break
                except Exception as e:
                    logger.error(f"Odds fetch failed on page {page}: {e}")
                    break
            _cache_set(cache_key, {"fixtures": fixtures, "odds_map": odds_map})

    if not fixtures:
        return []

    events = []
    for f in fixtures:
        f_id = str(f['fixture']['id'])
        if f_id not in odds_map:
            continue
        home_name = f['teams']['home']['name']
        away_name = f['teams']['away']['name']
        dt = datetime.datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
        league_name = f.get('league', {}).get('name', '')
        fixture_obj = MockFixture(source_id=f_id, home=MockTeam(home_name), away=MockTeam(away_name),
                                  starts_at=dt, label=f"{home_name} vs {away_name}", league_name=league_name)
        markets = []
        for bet in odds_map[f_id]:
            b_name = bet.get("name")
            if b_name == "Match Winner":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("1x2"), market="1X2", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Goals Over/Under":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("totals"), market=f"O/U {val['value']}", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Both Teams Score":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("btts"), market="BTTS", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Double Chance":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("double_chance"), market="Double Chance", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Asian Handicap":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("asian_handicap"), market=f"AH {val['value']}", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Handicap Result":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("handicap"), market=f"Handicap {val['value']}", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Home/Away":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("draw_no_bet"), market="Draw No Bet", selection=val["value"], odds=float(val["odd"])))
            elif b_name == "Corners Over Under":
                for val in bet.get("values", []):
                    markets.append(MockMarket(family=safe_get_market_family("corners"), market=f"Corners O/U {val['value']}", selection=val["value"], odds=float(val["odd"])))
        if markets:
            events.append(MockEvent(fixture=fixture_obj, markets=markets))

    logger.info(f"✅ Wide Net assembled {len(events)} matches with full market odds.")
    return events


def load_history_for_leagues(leagues: list) -> list:
    client = OpenFootballClient(http)
    history = []
    if "all" in leagues: leagues = list(OPENFOOTBALL_LEAGUE_FILES.keys())
    for league in leagues:
        league_file = OPENFOOTBALL_LEAGUE_FILES.get(league)
        if not league_file: continue
        for season in "2023-24,2024-25".split(","):
            try:
                matches = client.fetch_season(season.strip(), league_file)
                # OpenFootball returns HistoricalMatch objects; keep them as-is for the engine.
                history.extend(_to_historical_match(m) for m in matches)
            except HttpClientError: continue
    return [m for m in history if m is not None]

def load_history_from_supabase() -> list:
    now = datetime.datetime.now()
    if _SUPA_HISTORY_CACHE["ts"] and (now - _SUPA_HISTORY_CACHE["ts"]).total_seconds() < 6 * 3600:
        return _SUPA_HISTORY_CACHE["rows"]
    rows_all = []
    try:
        offset = 0
        while True:
            rows = supabase.table("match_results").select("*").not_.is_("home_score", "null").range(offset, offset + 999).execute().data
            rows_all.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000
    except Exception as e:
        logger.warning(f"Supabase history load failed: {e}")
    history = []
    for r in rows_all:
        hm = _to_historical_match({
            "home_team": r.get("home_team"), "away_team": r.get("away_team"),
            "home_score": r.get("home_score"), "away_score": r.get("away_score"),
            "match_date": r.get("match_date"), "league": r.get("league"),
        })
        if hm is not None:
            history.append(hm)
    logger.info(f"📚 Added {len(history)} Supabase rows to historical pool.")
    _SUPA_HISTORY_CACHE["ts"] = now
    _SUPA_HISTORY_CACHE["rows"] = history
    return history

def get_api_football_id(home: str, away: str, match_date: date):
    try:
        cache_key = f"apifootball_{match_date.isoformat()}"
        raw = _cache_get(cache_key, max_age_hours=24) or {}
        fixtures = raw.get("fixtures")
        if fixtures is None:
            provider = ApiFootballProvider()
            fixtures = provider.fixtures_by_date(match_date.isoformat())
            if fixtures:
                raw["fixtures"] = fixtures
                _cache_set(cache_key, raw)
            else:
                return None
        matcher = FixtureMatcher()
        candidates = [type('Ext', (object,), {
            'id': str(f['fixture']['id']),
            'home_team': f['teams']['home']['name'],
            'away_team': f['teams']['away']['name'],
            'date': datetime.datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
        })() for f in fixtures]
        match = matcher.match(home, away, datetime.datetime.combine(match_date, datetime.datetime.min.time()), candidates)
        return int(match.id) if match else None
    except Exception as e:
        logger.warning(f"Could not map {home} vs {away} to API-Football: {e}")
        return None

async def fetch_and_store_yesterday_results():
    yesterday = date.today() - timedelta(days=1)
    logger.info(f"📊 Fetching results for {yesterday}...")
    provider = ApiFootballProvider()
    try:
        fixtures = provider.fixtures_by_date(yesterday.isoformat())
        for fixture in fixtures:
            if fixture['fixture']['status']['short'] not in ['FT', 'AET', 'PEN']:
                continue
            
            # Fetch stats for corners/cards backfill
            home_corners = away_corners = home_cards = away_cards = None
            try:
                stats = provider.fixture_stats(str(fixture['fixture']['id']))
                home_name = fixture['teams']['home']['name']
                away_name = fixture['teams']['away']['name']
                
                for stat in stats.team_stats:
                    if stat.subject == home_name:
                        if stat.code == StatCode.CORNERS: home_corners = int(stat.value)
                        if stat.code in (StatCode.YELLOW_CARDS, StatCode.RED_CARDS):
                            home_cards = (home_cards or 0) + int(stat.value)
                    elif stat.subject == away_name:
                        if stat.code == StatCode.CORNERS: away_corners = int(stat.value)
                        if stat.code in (StatCode.YELLOW_CARDS, StatCode.RED_CARDS):
                            away_cards = (away_cards or 0) + int(stat.value)
            except Exception as e:
                logger.warning(f"Could not fetch stats for fixture {fixture['fixture']['id']}: {e}")

            # Use upsert to avoid duplicates if job runs twice
            supabase.table('match_results').upsert({
                'api_football_id': int(fixture['fixture']['id']),
                'home_team': fixture['teams']['home']['name'],
                'away_team': fixture['teams']['away']['name'],
                'home_score': fixture['goals']['home'],
                'away_score': fixture['goals']['away'],
                'match_date': yesterday.isoformat(),
                'league': fixture['league']['name'],
                'home_corners': home_corners,
                'away_corners': away_corners,
                'home_cards': home_cards,
                'away_cards': away_cards,
            }, on_conflict='api_football_id').execute()
            
        logger.info(f"✅ Stored results and stats from {yesterday}")
        await update_team_statistics()
    except ApiFootballNetworkError as e:
        logger.error(f"Failed to fetch yesterday's results (Network Down): {e}")
    except Exception as e:
        logger.error(f"Failed to fetch yesterday's results: {e}")

async def update_team_statistics():
    logger.info("📈 Updating team statistics...")
    teams_response = supabase.table('match_results').select('home_team', 'away_team').execute()
    all_teams = set()
    for row in teams_response.data:
        all_teams.add(row['home_team'])
        all_teams.add(row['away_team'])
    for team in all_teams:
        matches = supabase.table('match_results').select('*').or_(
            f"home_team.eq.{team},away_team.eq.{team}"
        ).order('match_date', desc=True).limit(10).execute()
        if not matches.data:
            continue
        wins = draws = losses = 0
        goals_scored = goals_conceded = 0
        form = []
        for match in matches.data:
            is_home = match['home_team'] == team
            team_goals = match['home_score'] if is_home else match['away_score']
            opp_goals = match['away_score'] if is_home else match['home_score']
            goals_scored += team_goals
            goals_conceded += opp_goals
            if team_goals > opp_goals: wins += 1; form.append('W')
            elif team_goals == opp_goals: draws += 1; form.append('D')
            else: losses += 1; form.append('L')
        supabase.table('team_stats').upsert({
            'team_name': team, 'matches_played': len(matches.data),
            'wins': wins, 'draws': draws, 'losses': losses,
            'goals_scored': goals_scored, 'goals_conceded': goals_conceded,
            'last_5_form': '-'.join(form[:5][::-1]), 'last_updated': date.today().isoformat()
        }, on_conflict='team_name').execute()
    logger.info("✅ Team statistics updated")

def save_bet_slip_to_db(chat_id: int, legs: list, slip_type: str):
    slip_id = str(uuid.uuid4())[:8]
    rows_to_insert = []
    for leg in legs:
        match_date = leg.fixture.starts_at.date() if leg.fixture.starts_at else date.today()
        af_id = get_api_football_id(leg.fixture.home.name, leg.fixture.away.name, match_date)
        rows_to_insert.append({
            "chat_id": chat_id, "slip_id": slip_id, "fixture_label": leg.fixture.label,
            "selection": f"{leg.market.market} - {leg.market.selection}", "odds": leg.market.odds,
            "match_time": leg.fixture.starts_at.isoformat() if leg.fixture.starts_at else "",
            "api_football_id": af_id, "status": "pending",
            "source": "model" if _leg_is_model_backed(leg) else "consensus"
        })
    if rows_to_insert:
        supabase.table("user_bets").insert(rows_to_insert).execute()
        logger.info(f"💾 Saved {slip_type} slip {slip_id} to Supabase.")

async def update_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(" **Updating historical database...**\n\nThis may take 1-2 minutes.", parse_mode="Markdown")
    try:
        history = await asyncio.get_event_loop().run_in_executor(None, load_history_for_leagues, ["all"])
        await update.message.reply_text(f"✅ **Database Updated!**\n\n📚 Loaded **{len(history)}** matches.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ **Failed:** {str(e)}", parse_mode="Markdown")

async def daily_history_update(context: ContextTypes.DEFAULT_TYPE):
    logger.info(" Running daily historical database auto-update...")
    try:
        history = load_history_for_leagues(["all"])
        logger.info(f"✅ Daily update complete: {len(history)} matches.")
    except Exception as e:
        logger.error(f"❌ Daily historical update failed: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **PredictionBot Quant Assistant**\n\nI build mathematically diversified accumulators.\n\n"
        "Try: *'Give me a 10 odd accumulator for today'*", parse_mode="Markdown")

async def leagues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Instant status — which trusted leagues are live today, and when the rest start.
    await update.message.reply_text(format_leagues_status(), parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text in ['hello', 'hi', 'hey', 'start', 'help']:
        await update.message.reply_text(" Hi! Try: *'Give me a 5 odd accumulator for today'*", parse_mode="Markdown")
        return

    status_msg = await update.message.reply_text("🔍 **Request Received!** Scanning... ⏳", parse_mode="Markdown")
    intent = router.parse_intent(update.message.text)
    if "error" in intent:
        await status_msg.edit_text("❌ Couldn't understand. Try: 'Give me 10 odds for today'")
        return

    iso_dates = re.findall(r"\d{4}-\d{2}-\d{2}", update.message.text)
    if iso_dates:
        intent["date"] = iso_dates[0]
        if len(iso_dates) > 1:
            intent["end_date"] = iso_dates[1]
        logger.info(f"📅 Regex date override: {intent['date']} -> {intent.get('end_date')}")

    try:
        result = await asyncio.to_thread(process_bet_request, intent, update.effective_chat.id)
        if result.get("success"):
            try: await status_msg.delete()
            except Exception: pass
            if "messages" in result:
                chunks = result["messages"]
                for i, chunk in enumerate(chunks):
                    reply_markup = result.get("keyboard") if i == len(chunks) - 1 else None
                    await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await update.message.reply_text(result["message"], parse_mode="Markdown", reply_markup=result.get("keyboard"))
        else:
            await status_msg.edit_text(result["message"], parse_mode="Markdown")
    except Exception as e:
        logger.error(e)
        err_str = str(e)
        try:
            if any(k in err_str for k in ("getaddrinfo", "ConnectError", "NetworkError", "timed out")):
                await status_msg.edit_text(" *Network hiccup on my end.* Please try again in a minute.", parse_mode="Markdown")
            else:
                await status_msg.edit_text(f"❌ Error: {err_str}")
        except Exception:
            pass

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SHOWING_FULL_SLIP, SUMMARY_TEXT, FULL_TEXT
    query = update.callback_query
    await query.answer()
    if not SUMMARY_TEXT and not FULL_TEXT:
        await query.edit_message_text("️ Bot was restarted. Please request a new slip.", parse_mode="Markdown")
        return
    SHOWING_FULL_SLIP = not SHOWING_FULL_SLIP
    text = FULL_TEXT if SHOWING_FULL_SLIP else SUMMARY_TEXT
    btn_label = " Hide Details" if SHOWING_FULL_SLIP else "📋 View Full Slip Details"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, callback_data="toggle_slip")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def check_finished_matches(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 Checking for finished matches via Supabase...")
    response = supabase.table("user_bets").select("*").eq("status", "pending").execute()
    pending = response.data
    if not pending:
        return

    provider = ApiFootballProvider()
    result_cache: dict[str, dict] = {}
    corners_cache: dict[str, float | None] = {}

    def _fixture_corners(fid: str) -> float | None:
        """Combined corner count for a fixture, or None if the feed has no
        corner stats yet (leg stays pending and retries next cycle)."""
        if fid not in corners_cache:
            try:
                stats = provider.fixture_stats(str(fid))
                total = stats.team_total(StatCode.CORNERS)
                # team_total sums both teams; a feed with no corner rows yields
                # 0.0, which we can't distinguish from a genuine 0-0 corners
                # match, so treat "no corner rows at all" as missing.
                has_corner_rows = any(s.code == StatCode.CORNERS for s in stats.team_stats)
                corners_cache[fid] = total if has_corner_rows else None
            except Exception:
                corners_cache[fid] = None
        return corners_cache[fid]

    # Group pending legs by slip so we can resolve one complete slip at a time.
    slips: dict[str, dict] = {}
    for row in pending:
        slips.setdefault(row["slip_id"], {"chat_id": row["chat_id"], "pending_rows": []})
        slips[row["slip_id"]]["pending_rows"].append(row)

    for slip_id, slip in slips.items():
        try:
            for row in slip["pending_rows"]:
                bet_id = row["id"]
                fixture = row["fixture_label"]
                selection = row["selection"]
                af_id = row["api_football_id"]
                match_time = row["match_time"]

                match_dt = datetime.datetime.fromisoformat(match_time)
                if match_dt.tzinfo is None:
                    match_dt = match_dt.replace(tzinfo=ZoneInfo("UTC"))
                now_utc = datetime.datetime.now(ZoneInfo("UTC"))
                # Keep the existing 2-hour grace period after kickoff.
                if now_utc < match_dt + timedelta(hours=2):
                    continue

                if not af_id:
                    try:
                        home, away = [p.strip() for p in fixture.split(" vs ")]
                        af_id = get_api_football_id(home, away, match_dt.date())
                        if af_id:
                            supabase.table("user_bets").update({"api_football_id": af_id}).eq("id", bet_id).execute()
                    except Exception:
                        af_id = None
                    if not af_id:
                        # No API ID after a week: mark the leg void (terminal, no outcome).
                        if now_utc > match_dt + timedelta(days=7):
                            supabase.table("user_bets").update({"status": "void"}).eq("id", bet_id).execute()
                        continue

                if af_id not in result_cache:
                    result_cache[af_id] = provider.get_fixture_result(str(af_id))
                result = result_cache[af_id]
                if result and result["status"] in ["FT", "AET", "PEN"]:
                    home_score = result["home_score"]
                    away_score = result["away_score"]
                    corners_total = _fixture_corners(af_id) if is_corners_market(selection) else None
                    outcome = evaluate_bet(
                        selection, home_score, away_score, corners_total=corners_total
                    )
                    if outcome == "unsettleable":
                        # Corner stats not published yet: leave pending and retry.
                        # After the 7-day cutoff, void it so the slip can close.
                        if now_utc > match_dt + timedelta(days=7):
                            supabase.table("user_bets").update(
                                {"status": "void", "final_score": f"{home_score}-{away_score}"}
                            ).eq("id", bet_id).execute()
                        continue
                    final_score = f"{home_score}-{away_score}"
                    if is_corners_market(selection) and corners_total is not None:
                        final_score = f"{final_score} ({int(corners_total)} corners)"
                    supabase.table("user_bets").update({
                        "status": outcome,  # "won" | "lost" | "void"
                        "final_score": final_score,
                    }).eq("id", bet_id).execute()
        except Exception as e:
            logger.error(f"Error checking slip {slip_id}: {e}")

    # Reload the COMPLETE slip (settled + still-pending legs) before deciding to notify.
    all_rows = supabase.table("user_bets").select("*").in_("slip_id", list(slips.keys())).execute().data
    by_slip: dict[str, list[dict]] = {}
    for row in all_rows:
        by_slip.setdefault(row["slip_id"], []).append(row)

    for slip_id, legs in by_slip.items():
        try:
            decision = evaluate_slip_settlement(legs)
            if not decision["notify"]:
                if decision["reason"] == "incomplete":
                    logger.info(f"⏳ Slip {slip_id}: waiting for full result sheet.")
                continue

            chat_id = legs[0]["chat_id"]
            await context.bot.send_message(chat_id=chat_id, text=decision["message"], parse_mode="Markdown")
            # Idempotency guard: mark every leg so a later poll cannot resend,
            # even if a leg is voided/re-opened after the fact.
            supabase.table("user_bets").update(
                {"settlement_notified_at": datetime.datetime.now(ZoneInfo("UTC")).isoformat()}
            ).eq("slip_id", slip_id).execute()
            logger.info(f"📬 Notified slip {slip_id} for chat {chat_id}")
        except Exception as e:
            logger.error(f"Failed to notify slip {slip_id}: {e}")


def process_bet_request(intent: dict, chat_id: int) -> dict:
    logger.info(f" Processing request with intent: {intent}")
    target_date = date.fromisoformat(intent.get("date", date.today().isoformat()))
    leagues = intent.get("leagues", ["all"])
    target_odds = intent.get("target_odds")

    history = load_history_for_leagues(leagues)
    history.extend(load_history_from_supabase())
    logger.info(f"📚 Loaded {len(history)} historical matches (total pool)")

    # Bucket the pool by team once so each score_market call only sees the two
    # teams' matches instead of the whole list (the profile builders re-filter
    # and take [-limit:], so results are identical — just far cheaper).
    team_history = index_history_by_team(history)

    events = []
    start_date = target_date
    if intent.get("end_date"):
        try: end_date = date.fromisoformat(intent["end_date"])
        except Exception: end_date = start_date + timedelta(days=4)
    else:
        end_date = start_date + timedelta(days=4)
    if (end_date - start_date).days > 6:
        end_date = start_date + timedelta(days=6)

    api_down_days = 0
    current_d = start_date
    while current_d <= end_date:
        date_str = current_d.isoformat()
        api_cache_key = f"api_football_{date_str}"
        day_events = []
        if api_cache_key in EVENT_CACHE:
            day_events = EVENT_CACHE[api_cache_key]
            logger.info(f" Loaded {len(day_events)} API events for {date_str} from MEMORY CACHE.")
        else:
            try:
                day_events = fetch_global_events(current_d)
                if day_events:
                    EVENT_CACHE[api_cache_key] = day_events
            except ApiFootballNetworkError as e:
                logger.warning(f"⚠️ API-Football NETWORK ERROR for {date_str}: {e}")
                api_down_days += 1
            except Exception as e:
                logger.warning(f"⚠️ API unexpected error for {date_str}: {e}")
        if day_events:
            events.extend(day_events)
        current_d += timedelta(days=1)

    logger.info(f" Multi-day scouting gathered {len(events)} total global events.")

    if not events:
        if api_down_days > 0:
            logger.warning(f"API-Football down for {api_down_days} days. Triggering explicit Bet9ja fallback.")
        else:
            logger.warning("Wide Net returned 0 events (API healthy, but no fixtures/odds). Falling back to Bet9ja.")
        current_d = start_date
        while current_d <= end_date:
            date_str = current_d.isoformat()
            b9ja_cache_key = f"bet9ja_{date_str}_{'-'.join(leagues)}"
            if b9ja_cache_key in EVENT_CACHE:
                day_events = EVENT_CACHE[b9ja_cache_key]
                logger.info(f" Loaded {len(day_events)} Bet9ja events for {date_str} from MEMORY CACHE.")
            else:
                day_events = []
                try:
                    if "all" in leagues:
                        evs = bet9ja.all_supported_events(target_date=current_d)
                        if evs: day_events.extend(evs)
                    else:
                        for league in leagues:
                            if league in BET9JA_LEAGUES:
                                evs = bet9ja.league_events(league, target_date=current_d)
                                if evs: day_events.extend(evs)
                    if day_events:
                        converted = [normalize_event(e) for e in day_events]
                        day_events = [e for e in converted if e is not None]
                        EVENT_CACHE[b9ja_cache_key] = day_events
                except Exception as e:
                    logger.warning(f"⚠️ Bet9ja timeout/error for {date_str}: {e}")
            if day_events:
                events.extend(day_events)
            current_d += timedelta(days=1)

    if not events:
        return {"success": False, "message": "No matches found for the selected dates and leagues."}

    market_families = {MarketFamily(intent["market_family"])} if intent.get("market_family") and intent["market_family"] != "all" else None

    def consensus_prediction(event, market):
        if not CONSENSUS_FALLBACK:
            return None
        if market.odds < 1.3 or market.odds > 3.0:
            return None

        implied_prob = 1.0 / market.odds
        # Remove vigorish: assume book margin is ~5%, so true prob is slightly higher
        true_prob = implied_prob * 1.05  
        true_prob = min(true_prob, 0.95)  # cap at 95%

        # Edge = our estimated true probability - implied probability
        edge = true_prob - implied_prob

        if edge < 0.01:  # require at least 1% edge
            return None
        if not (0.50 <= true_prob <= 0.92):
            return None

        return MockPrediction(event.fixture, market, true_prob, edge)

    MIN_LEG_ODDS = 1.15
    now = datetime.datetime.now(datetime.timezone.utc)

    def build_family_groups(major_only: bool):
        groups = defaultdict(list)
        s = {"score_market_none": 0, "consensus_none": 0, "accepted": 0, "skipped_league": 0, "skipped_time": 0}
        # Markets per family that survived pre-filters and were handed to the
        # scorer. Lets us tell "book never listed this family" (absent here)
        # apart from "listed but produced no edge" (present here, absent in groups).
        tried = defaultdict(int)
        for event in events:
            fx = event.fixture

            if major_only:
                if fx.source == "bet9ja":
                    is_major = True
                else:
                    league_name = (fx.league_name or "").lower()
                    is_major = any(major in league_name for major in MAJOR_LEAGUES)
                if not is_major:
                    s["skipped_league"] += 1
                    continue

            start_time = fx.starts_at
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=datetime.timezone.utc)
            if start_time < now + timedelta(minutes=30):  # instead of hours=2
                s["skipped_time"] += 1
                continue

            fx_history = history_for_fixture(team_history, fx.home.name, fx.away.name)

            for market in event.markets:
                if market_families and market.family not in market_families:
                    continue
                if market.family == MarketFamily.TOTALS:
                    if market.line < 1.5 or market.line > 3.5:
                        continue
                if market.odds < MIN_LEG_ODDS:
                    continue

                tried[market.family] += 1

                pred = None
                try:
                    pred = score_market(event.fixture, market, fx_history, min_edge=0.02)
                except Exception as ex:
                    logger.warning(f"score_market ERROR on {fx.label} [{market.market}]: {ex}")

                if pred is None:
                    s["score_market_none"] += 1
                    pred = consensus_prediction(event, market)
                    if pred is None:
                        s["consensus_none"] += 1
                    else:
                        s["accepted"] += 1
                else:
                    s["accepted"] += 1

                if pred is not None:
                    groups[market.family].append(pred)
        return groups, s, tried

    # Strict pass first: major leagues only (preserves in-season behavior).
    family_groups, stats, tried_families = build_family_groups(major_only=True)
    if not family_groups:
        logger.warning("⚠️ No major-league edges found. Relaxing filter to all active leagues (off-season fallback).")
        family_groups, stats, tried_families = build_family_groups(major_only=False)

    logger.info(f" DIAGNOSTICS: {stats}")
    family_debug = {family: len(preds) for family, preds in family_groups.items()}
    logger.info(f"📊 Market families with predictions: {family_debug}")

    # Explain, per family, WHY it did or didn't contribute legs. Distinguishes
    # families the book never listed for these fixtures ("feed-missing") from
    # families that were scored but produced no edge ("no-edge/insufficient-history").
    ALL_FAMILIES = [
        MarketFamily.TOTALS, MarketFamily.DOUBLE_CHANCE, MarketFamily.MATCH_WINNER,
        MarketFamily.BOTH_TEAMS_TO_SCORE, MarketFamily.HANDICAP, MarketFamily.TEAM_TOTALS,
        MarketFamily.FIRST_HALF_TOTALS, MarketFamily.SECOND_HALF_TOTALS,
        MarketFamily.CORNERS, MarketFamily.SHOTS, MarketFamily.SHOTS_ON_TARGET,
        MarketFamily.BOOKINGS,
    ]
    families_to_report = (
        [f for f in ALL_FAMILIES if f in market_families] if market_families else ALL_FAMILIES
    )
    family_coverage = {}
    for family in families_to_report:
        produced = len(family_groups.get(family, []))
        offered = tried_families.get(family, 0)
        if produced:
            family_coverage[family.value] = f"{produced} legs (from {offered} offered)"
        elif offered:
            family_coverage[family.value] = f"no-edge/insufficient-history (0 of {offered} offered)"
        else:
            family_coverage[family.value] = "feed-missing (book listed none)"
    logger.info(f"🔎 Family coverage: {family_coverage}")

    if not family_groups:
        diagnostic_msg = (
            f"❌ No mathematically viable edges found.\n\n"
            f"📊 **Diagnostics:**\n"
            f"• Events scanned: {len(events)}\n"
            f"• Skipped (minor leagues): {stats['skipped_league']}\n"
            f"• Skipped (starts too soon): {stats['skipped_time']}\n"
            f"• Model predictions failed: {stats['score_market_none']}\n"
            f"• Consensus fallback failed: {stats['consensus_none']}\n"
            f"• Accepted predictions: {stats['accepted']}\n\n"
            f"💡 Try a different date or lower your target odds."
        )
        return {"success": False, "message": diagnostic_msg}

    # Data-fit, one-leg-per-fixture selection. For each game we keep the SINGLE
    # best leg, preferring decisive / data-driven markets (1X2, totals, BTTS,
    # corners, and stat markets when the feed has them) over hedge markets, and
    # ranking by genuine edge. Two deliberate exclusions:
    #   * lay-goals handicaps ("Away -2" when you could just back "Away win") —
    #     they add nothing a straight win bet doesn't, and were exactly what made
    #     the old slips a monotonous Asian-handicap stack;
    #   * anything below the +1% edge floor.
    # Handicap (getting-goals) and Double Chance survive here only as a fixture's
    # best option; the accumulator then treats them as capped last-resort filler.
    PRIMARY_FAMILIES = {
        MarketFamily.MATCH_WINNER, MarketFamily.TOTALS, MarketFamily.BOTH_TEAMS_TO_SCORE,
        MarketFamily.CORNERS, MarketFamily.SHOTS, MarketFamily.SHOTS_ON_TARGET,
        MarketFamily.BOOKINGS, MarketFamily.TEAM_TOTALS,
        MarketFamily.FIRST_HALF_TOTALS, MarketFamily.SECOND_HALF_TOTALS,
    }

    def _is_lay_goals_handicap(pred) -> bool:
        return pred.market.family == MarketFamily.HANDICAP and (pred.market.line or 0) < 0

    # 🎯 DYNAMIC DIVERSITY (Round-Robin Selection)
    # Instead of rigid quotas, we gather all safe bets and take the best available 
    # from EACH market family before circling back. This naturally creates an organic mix 
    # like "2 Overs, 2 Winners, 1 BTTS, 1 Corner".
    safe_by_family = defaultdict(list)
    for family, preds in family_groups.items():
        for pred in preds:
            if pred.model_probability >= MIN_LEG_PROBABILITY and \
               pred.edge >= 0.01 and \
               not _is_lay_goals_handicap(pred):
                safe_by_family[family].append(pred)
                
    # Sort each family's list by probability (safest bets first)
    for family in safe_by_family:
        safe_by_family[family].sort(key=lambda p: p.model_probability, reverse=True)

    diverse_predictions = []
    used_fixtures = set()
    
    # Keep looping until we hit our leg limit (6) or run out of safe options
    while len(diverse_predictions) < PRO_MAX_LEGS:
        added_this_round = False
        
        # Sort families dynamically: prioritize the family whose top remaining bet is the safest today
        sorted_families = sorted(
            [f for f in safe_by_family if safe_by_family[f]], # only families with bets left
            key=lambda f: safe_by_family[f][0].model_probability,
            reverse=True
        )
        
        for family in sorted_families:
            if len(diverse_predictions) >= PRO_MAX_LEGS:
                break
                
            # Find the best unused prediction in this family
            for pred in safe_by_family[family]:
                if pred.fixture.source_id not in used_fixtures:
                    diverse_predictions.append(pred)
                    used_fixtures.add(pred.fixture.source_id)
                    safe_by_family[family].remove(pred) # Remove so it's not picked again in the next lap
                    added_this_round = True
                    break # Move to the next market family
                    
        # If we went through all families and didn't add any new legs, we are out of options
        if not added_this_round:
            break

    if not diverse_predictions:
        return {"success": False, "message": "❌ No mathematically viable edges found (all predictions had negative or near-zero edges)."}

    # === PRO QUARANTINE ===
    # A professional slip is built from REAL model edges only. Book-consensus
    # legs (implied_prob × 1.05 filler) never count toward the target or land
    # on the slip — they inflate the odds with fake confidence, nothing more.
    model_predictions = [p for p in diverse_predictions if _leg_is_model_backed(p)]
    consensus_count = len(diverse_predictions) - len(model_predictions)

    # Second gate: a "model" leg is only credible if the scorer was both sane
    # AND well-fed. Two ways it fails, checked in order so counts don't overlap:
    #   1. Implausibly large edge — a Poisson model starved of history on a
    #      lower-tier side happily prints 97% / +33% nonsense.
    #   2. Thin data — even a modest-looking edge isn't trustworthy when it came
    #      from two or three matches. This is the root-cause fix: once the deep-
    #      history major leagues are in season, data_confidence hits 1.0 and
    #      these same fixtures sail through with solid, un-suppressed edges.
    # A professional does NOT bet a leg it distrusts, so both are EXCLUDED from
    # the slip, not stamped with a ⚠️ and stacked anyway.
    credible_predictions = []
    implausible_count = 0
    thin_data_count = 0
    for p in model_predictions:
        if p.edge > SUSPICIOUS_EDGE:
            implausible_count += 1
        elif getattr(p, "data_confidence", 1.0) < MIN_DATA_CONFIDENCE:
            thin_data_count += 1
        else:
            credible_predictions.append(p)
    logger.info(
        f"🔒 Quarantine: {len(diverse_predictions)} raw → "
        f"{len(model_predictions)} model-backed ({consensus_count} consensus dropped) → "
        f"{len(credible_predictions)} credible "
        f"({implausible_count} implausible-edge, {thin_data_count} thin-history dropped)."
    )

    if not credible_predictions:
        # Nothing survived that a pro would actually stake. Say so plainly and
        # show WHY, rather than dressing up book prices or model errors as picks.
        reasons = []
        if consensus_count:
            reasons.append(f"*{consensus_count}* were book-consensus prices (no real model edge)")
        if implausible_count:
            reasons.append(
                f"*{implausible_count}* had edges above {SUSPICIOUS_EDGE:.0%} "
                f"(model over-confident on thin-history fixtures — not trustworthy)"
            )
        if thin_data_count:
            reasons.append(
                f"*{thin_data_count}* lacked enough team history to trust "
                f"(the model needs deeper stats than these fixtures have yet)"
            )
        why = "; ".join(reasons) if reasons else "no positive-edge picks were found"
        return {"success": False, "message": (
            "🧭 *No professional slip today.*\n\n"
            f"The engine found picks, but none I'd stake: {why}.\n\n"
            "A real accumulator needs calibrated edges — I won't manufacture one "
            "from bookmaker prices or model errors just to hit a number.\n\n"
            f"{format_comeback_hint()}"
        )}

    # Build the acca from CREDIBLE model edges only, capped hard at PRO_MAX_LEGS.
    # With no explicit target we still want the best short slip, so aim absurdly
    # high and let the leg cap stop it.
    has_target = bool(target_odds and target_odds > 1)
    effective_target = target_odds if has_target else 1e9
    acca = None
    try:
        acca = build_progressive_accumulator(
            predictions=credible_predictions, target_odds=effective_target,
            max_risk_band=SafeOddsBand.HIGH_RISK, max_legs=PRO_MAX_LEGS,
            max_odds_per_leg=MAX_ODDS_PER_LEG, value_first=False,
            filler_families=FILLER_FAMILIES, filler_caps=FILLER_CAPS)
    except Exception as e:
        logger.error(f"Accumulator build failed: {e}")

    if acca and acca.legs:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("️ View Edge Details", callback_data="toggle_slip")]]
        )
        if has_target and acca.reached_target:
            header = (
                f"✅ *Quant Target Reached!*\n"
                f"🎯 *Total Odds:* {acca.total_odds:.2f}\n"
                f"📊 *Total Legs:* {len(acca.legs)} (max {PRO_MAX_LEGS})\n"
                f"{_slip_stats_line(acca.legs)}\n\n"
                f"*The Slip:*\n"
            )
        else:
            # PUSH-BACK: genuine edges couldn't reach the target inside the leg
            # cap. Hand back the best HONEST slip instead of padding it out with
            # coin-flip legs that grow the price but not the win chance.
            target_note = (
                f" (target {target_odds:.0f} needs coin-flips I won't add)"
                if has_target else ""
            )
            header = (
                f"🧭 *Best Honest Slip*{target_note}\n"
                f"🎯 *Realistic Odds:* {acca.total_odds:.2f}\n"
                f"📊 *Total Legs:* {len(acca.legs)} (max {PRO_MAX_LEGS})\n"
                f"{_slip_stats_line(acca.legs)}\n\n"
                f"_This is capped at genuine model edges. Chasing a bigger number "
                f"means stacking legs that inflate the odds, not your win chance._\n\n"
                f"*The Slip:*\n"
            )
        message_chunks = _format_slip_chunks(header, acca.legs)
        save_bet_slip_to_db(chat_id, acca.legs, "quant")
        return {"success": True, "messages": message_chunks, "keyboard": keyboard}

    # No credible leg survived the per-leg odds ceiling — can't build a slip.
    mix = Counter(p.market.family.value.replace("_", " ").title() for p in credible_predictions)
    mix_str = ", ".join(f"{name} ×{count}" for name, count in mix.most_common())
    return {"success": False, "message": (
        f"⚠️ *No buildable slip.*\n\nThe engine found *{len(credible_predictions)}* credible "
        f"pick(s), but none priced at or under {MAX_ODDS_PER_LEG:.2f} to anchor the accumulator.\n\n"
        f"📊 *Market mix:* {mix_str}\n\n"
        f"💡 *Try a wider date range for more anchors.*")}

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    class DummyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Bot is alive and running!")
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
        def log_message(self, format, *args):
            pass
    HTTPServer(('0.0.0.0', port), DummyHandler).serve_forever()

def main() -> None:
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leagues", leagues_command))
    app.add_handler(CommandHandler("update_history", update_history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    try:
        from telegram.ext import JobQueue
        app.job_queue.run_repeating(check_finished_matches, interval=1800, first=10)
        app.job_queue.run_daily(daily_history_update, time=datetime.time(3, 0, tzinfo=WAT))
        app.job_queue.run_daily(fetch_and_store_yesterday_results, time=datetime.time(4, 0, tzinfo=WAT))
        logger.info("✅ Background jobs enabled.")
    except ImportError:
        logger.warning("⚠️ Install job-queue: pip install 'python-telegram-bot[job-queue]'")
    print(" Bot running with disk cache + strict positive-edge enforcement + August whitelist...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
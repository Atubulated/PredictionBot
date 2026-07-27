from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any

from predictionbot.domain import Fixture, MarketFamily, MarketOdds, Team
from predictionbot.http import JsonHttpClient
from predictionbot.odds import classify_market

import re
from predictionbot.domain import MarketFamily, MarketOdds

def _extract_line(value: str) -> float | None:
    """Extracts a float line (e.g., 8.5, 3.5) from a market string."""
    for token in value.replace("/", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None

# src/predictionbot/sources/bet9ja.py

def classify_bet9ja_market(market_name: str, selection: str) -> tuple[MarketFamily, float | None]:
    """Determines the MarketFamily and line from Bet9ja's raw text."""
    name = market_name.lower()
    sel = selection.lower()
    combined = f"{name} {sel}"
    
    # 1. First Half Totals (Must check before generic totals)
    if "1st half" in combined or "first half" in combined:
        return MarketFamily.FIRST_HALF_TOTALS, _extract_line(combined)
        
    # 2. Team Totals (Home/Away specific)
    if "home team total" in combined or "away team total" in combined:
        return MarketFamily.TEAM_TOTALS, _extract_line(combined)
        
    # 3. Corners 
    if "corner" in combined:
        return MarketFamily.CORNERS, _extract_line(combined)
        
    # 4. Bookings / Cards
    if "booking" in combined or "card" in combined:
        return MarketFamily.BOOKINGS, _extract_line(combined)
        
    # 5. Both Teams to Score
    if "gg" in combined or "ng" in combined or "both teams" in combined:
        return MarketFamily.BOTH_TEAMS_TO_SCORE, None
        
    # 6. Totals (Match Goals)
    if "total" in name or "over" in sel or "under" in sel:
        return MarketFamily.TOTALS, _extract_line(combined)
        
    # 7. Double Chance
    if "double chance" in name or "home or draw" in sel or "away or draw" in sel:
        return MarketFamily.DOUBLE_CHANCE, None
        
    # 8. Handicap / Asian Handicap
    if "handicap" in name or "asian" in name:
        return MarketFamily.HANDICAP, _extract_line(combined)

    return MarketFamily.UNKNOWN, None

BET9JA_LEAGUES = {
    # --- Original Proven European Leagues ---
    "premier_league": 170880,
    "championship": 170881,
    "league_one": 995354,
    "league_two": 995355,
    "bundesliga": 180923,
    "bundesliga_2": 180924,
    "laliga": 180928,
    "ligue_1": 950503,
    "ligue_2": 958691,
    "serie_a": 167856,

    # --- European Competitions ---
    "champions_league": 201591, 
    "europa_league": 196207,
    "conference_league": 202425,
    "uefa_super_cup": 236511,

    # --- English Domestic Cups ---
    "efl_cup": 170884,
    "fa_cup": 181106,
    "community_shield": 233241,

    # --- Spanish Leagues & Cups ---
    "laliga_2": 180929,
    "copa_del_rey": 251223,

    # --- Italian Leagues ---
    "serie_b": 180919, 

    # --- German Leagues & Cups ---
    "dfb_pokal": 249294,
    "supercup_germany": 244768,

    # --- Other Major European Leagues ---
    "eredivisie_netherlands": 170882, 
    "primeira_liga_portugal": 181843,
    "scottish_premiership": 235545,
    "super_lig_turkey": 168228,
    
    # --- Americas ---
    "mls_usa": 242132,
    "copa_libertadores": 250826,
    "copa_sudamericana": 250824
}


@dataclass(frozen=True)
class Bet9jaListedEvent:
    fixture: Fixture
    markets: list[MarketOdds]


class Bet9jaClient:
    event_url = "https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/GetEvent?EVENTID={event_id}"
    league_url = (
        "https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/"
        "GetEventsInGroupV2?GROUPID={league_id}&DISP=0&GROUPMARKETID=1&matches=true"
    )
    home_url = "https://bet9ja.com"
    headers = {
        "sec-ch-ua": '"Chromium";v="94", "Microsoft Edge";v="94", ";Not A Brand";v="99"',
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "referer": "https://sports.bet9ja.com",
        "user-agent": "Chrome/94.0.4606.81",
    }

    def __init__(self, http: JsonHttpClient) -> None:
        self.http = http
        self._warmed = False

    def league_events(self, league: str, target_date: date | None = None) -> list[Bet9jaListedEvent]:
        self._warm_session()
        league_id = _league_id(league)
        payload = self.http.get_json(self.league_url.format(league_id=league_id), headers=self.headers)
        events = parse_league_events(payload)
        if target_date is None:
            return events
        return [
            event
            for event in events
            if event.fixture.starts_at is not None and event.fixture.starts_at.date() == target_date
        ]

    def all_supported_events(self, target_date: date | None = None) -> list[Bet9jaListedEvent]:
        events = []
        for league in BET9JA_LEAGUES:
            events.extend(self.league_events(league, target_date=target_date))
        return events

    def event_markets(self, event_id: str) -> list[MarketOdds]:
        self._warm_session()
        payload = self.http.get_json(self.event_url.format(event_id=event_id), headers=self.headers)
        return parse_event_markets(event_id=event_id, payload=payload)

    def _warm_session(self) -> None:
        if self._warmed:
            return
        self.http.get_text(self.home_url, headers=self.headers)
        self._warmed = True


def parse_league_events(payload: Any) -> list[Bet9jaListedEvent]:
    raw_events = ((payload or {}).get("D") or {}).get("E") or []
    listed = []
    for raw_event in raw_events:
        event_id = raw_event.get("ID")
        if event_id is None:
            continue

        home, away = _split_match_name(str(raw_event.get("DS") or "Unknown - Unknown"))
        fixture = Fixture(
            source="bet9ja",
            source_id=str(event_id),
            starts_at=_parse_bet9ja_timestamp(raw_event.get("STARTDATE")),
            home=Team(home),
            away=Team(away),
            league=raw_event.get("GN"),
            raw=raw_event,
        )
        listed.append(Bet9jaListedEvent(fixture=fixture, markets=_markets_from_listed_event(fixture, raw_event)))
    return listed


def parse_event_markets(event_id: str, payload: Any) -> list[MarketOdds]:
    markets = []
    for market in _walk_markets(payload):
        market_name = str(
            market.get("name")
            or market.get("Name")
            or market.get("description")
            or market.get("Description")
            or "Unknown market"
        )
        outcomes = (
            market.get("outcomes")
            or market.get("Outcomes")
            or market.get("selections")
            or market.get("Selections")
            or []
        )
        for outcome in outcomes:
            odds_value = (
                outcome.get("odds")
                or outcome.get("Odds")
                or outcome.get("price")
                or outcome.get("Price")
                or outcome.get("Odd")
            )
            try:
                decimal_odds = float(odds_value)
            except (TypeError, ValueError):
                continue

            selection = str(
                outcome.get("name")
                or outcome.get("Name")
                or outcome.get("selection")
                or outcome.get("Selection")
                or "Unknown selection"
            )
            
            # 1. Try to get line from JSON first
            line = _coerce_float(outcome.get("line") or outcome.get("Line") or market.get("line") or market.get("Line"))
            
            # 2. Use our new classifier to get the family AND a fallback line
            family, classified_line = classify_bet9ja_market(market_name, selection)
            
            # If JSON didn't have a line, use the one our classifier extracted from the text
            if line is None:
                line = classified_line

            # Only add if we successfully identified the market family
            if family != MarketFamily.UNKNOWN and decimal_odds > 1.0:
                markets.append(
                    MarketOdds(
                        bookmaker="bet9ja",
                        fixture_id=str(event_id),
                        family=family,
                        market=market_name,
                        selection=selection,
                        odds=decimal_odds,
                        line=line,
                        raw={"market": market, "outcome": outcome},
                    )
                )
    return markets


def _walk_markets(node: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(node, dict):
        values = list(node.values())
        if any(key in node for key in ("outcomes", "Outcomes", "selections", "Selections")):
            found.append(node)
        for value in values:
            found.extend(_walk_markets(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_markets(item))
    return found


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _league_id(league: str) -> int:
    normalized = league.casefold().replace("-", "_").replace(" ", "_")
    if normalized not in BET9JA_LEAGUES:
        choices = ", ".join(sorted(BET9JA_LEAGUES))
        raise ValueError(f"Unknown Bet9ja league '{league}'. Choose one of: {choices}")
    return BET9JA_LEAGUES[normalized]


def _split_match_name(value: str) -> tuple[str, str]:
    for separator in (" - ", " vs ", " v "):
        if separator in value:
            home, away = value.split(separator, 1)
            return home.strip(), away.strip()
    return value.strip(), "Unknown"


def _parse_bet9ja_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp)


def _markets_from_listed_event(fixture: Fixture, raw_event: dict[str, Any]) -> list[MarketOdds]:
    odds = raw_event.get("O") or {}
    definitions = [
        ("Match Result", "Home", odds.get("S_1X2_1"), MarketFamily.UNKNOWN),
        ("Match Result", "Draw", odds.get("S_1X2_X"), MarketFamily.UNKNOWN),
        ("Match Result", "Away", odds.get("S_1X2_2"), MarketFamily.UNKNOWN),
        ("Double Chance", "Home or Draw", odds.get("S_DC_1X"), MarketFamily.DOUBLE_CHANCE),
        ("Double Chance", "Home or Away", odds.get("S_DC_12"), MarketFamily.DOUBLE_CHANCE),
        ("Double Chance", "Draw or Away", odds.get("S_DC_X2"), MarketFamily.DOUBLE_CHANCE),
    ]
    markets = []
    for market_name, selection, odds_value, family in definitions:
        decimal_odds = _coerce_float(odds_value)
        if decimal_odds is None:
            continue
        markets.append(
            MarketOdds(
                bookmaker="bet9ja",
                fixture_id=fixture.source_id,
                family=family,
                market=market_name,
                selection=selection,
                odds=decimal_odds,
                raw={"event": raw_event},
            )
        )
    markets.extend(_dynamic_markets_from_odds(fixture, raw_event, odds))
    return markets


def _dynamic_markets_from_odds(
    fixture: Fixture,
    raw_event: dict[str, Any],
    odds: dict[str, Any],
) -> list[MarketOdds]:
    markets = []
    for key, value in odds.items():
        decimal_odds = _coerce_float(value)
        if decimal_odds is None:
            continue

        total_match = re.fullmatch(r"S_OU@(?P<line>-?\d+(?:\.\d+)?)_(?P<side>[OU])", key)
        if total_match:
            line = float(total_match.group("line"))
            side = "Over" if total_match.group("side") == "O" else "Under"
            markets.append(
                MarketOdds(
                    bookmaker="bet9ja",
                    fixture_id=fixture.source_id,
                    family=MarketFamily.TOTALS,
                    market=f"Total Goals Over/Under {line:g}",
                    selection=f"{side} {line:g}",
                    odds=decimal_odds,
                    line=line,
                    raw={"event": raw_event, "odds_key": key},
                )
            )
            continue
        # --- ADD THIS NEW BLOCK FOR CORNERS ---
        corner_match = re.fullmatch(r"S_COU@(?P<line>-?\d+(?:\.\d+)?)_(?P<side>[OU])", key)
        if corner_match:
            line = float(corner_match.group("line"))
            side = "Over" if corner_match.group("side") == "O" else "Under"
            markets.append(
                MarketOdds(
                    bookmaker="bet9ja",
                    fixture_id=fixture.source_id,
                    family=MarketFamily.CORNERS,
                    market=f"Total Corners Over/Under {line:g}",
                    selection=f"{side} {line:g}",
                    odds=decimal_odds,
                    line=line,
                    raw={"event": raw_event, "odds_key": key},
                )
            )
            continue
        # --------------------------------------
        asian_match = re.fullmatch(r"S_AH@(?P<line>-?\d+(?:\.\d+)?)_(?P<side>[12])", key)
        if asian_match:
            line = float(asian_match.group("line"))
            side = "Home" if asian_match.group("side") == "1" else "Away"
            selection_line = line if side == "Home" else -line
            markets.append(
                MarketOdds(
                    bookmaker="bet9ja",
                    fixture_id=fixture.source_id,
                    family=MarketFamily.HANDICAP,
                    market=f"Asian Handicap {line:g}",
                    selection=f"{side} {selection_line:g}",
                    odds=decimal_odds,
                    line=selection_line,
                    raw={"event": raw_event, "odds_key": key},
                )
            )
            continue

        if key == "S_GGNG_Y":
            markets.append(
                MarketOdds(
                    bookmaker="bet9ja",
                    fixture_id=fixture.source_id,
                    family=MarketFamily.BOTH_TEAMS_TO_SCORE,
                    market="Both Teams To Score",
                    selection="Yes",
                    odds=decimal_odds,
                    raw={"event": raw_event, "odds_key": key},
                )
            )
            continue

        if key == "S_GGNG_N":
            markets.append(
                MarketOdds(
                    bookmaker="bet9ja",
                    fixture_id=fixture.source_id,
                    family=MarketFamily.BOTH_TEAMS_TO_SCORE,
                    market="Both Teams To Score",
                    selection="No",
                    odds=decimal_odds,
                    raw={"event": raw_event, "odds_key": key},
                )
            )
    return markets

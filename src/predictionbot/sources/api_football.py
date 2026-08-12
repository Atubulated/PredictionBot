from __future__ import annotations

import logging
import urllib.request
import json
import time
import os
import hashlib
import datetime
from typing import Any

from ..config import load_settings
from ..stats import FixtureStats, StatCode, StatValue

logger = logging.getLogger(__name__)

# Mapping from API-Football stat types to our normalized StatCode
API_FOOTBALL_STAT_MAP = {
    "Shots on Goal": StatCode.SHOTS_ON_TARGET,
    "Shots off Goal": StatCode.SHOTS_OFF_TARGET,
    "Total Shots": StatCode.SHOTS_TOTAL,
    "Blocked Shots": StatCode.SHOTS_BLOCKED,
    "Corner Kicks": StatCode.CORNERS,
    "Fouls": StatCode.FOULS,
    "Yellow Cards": StatCode.YELLOW_CARDS,
    "Red Cards": StatCode.RED_CARDS,
    "Goals": StatCode.GOALS,
    "Assists": StatCode.ASSISTS,
    "expected_goals": StatCode.EXPECTED_GOALS,
    "Ball Possession": StatCode.POSSESSION,
    "Goalkeeper Saves": StatCode.SAVES,
    "Offsides": StatCode.OFFSIDES,
    "Total passes": StatCode.PASSES_TOTAL,
}

class ApiFootballNetworkError(RuntimeError):
    """Raised when the API is unreachable (DNS, timeout, 5xx)."""
    pass

# --- PERMANENT DISK CACHE FOR STATS & RESULTS ---
# Stats and Final Scores never change. We cache them forever to save API credits.
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".api_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _cache_path(key: str) -> str:
    # MD5 hash to keep filenames short and filesystem-safe
    safe_key = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{safe_key}.json")

def _get_cache(key: str, max_age_hours: float = 8760.0): # Default 1 year (8760 hrs)
    path = _cache_path(key)
    if not os.path.exists(path): return None
    try:
        age_h = (datetime.datetime.now().timestamp() - os.path.getmtime(path)) / 3600.0
        if age_h > max_age_hours: return None
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def _set_cache(key: str, payload):
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f: json.dump(payload, f)
    except Exception as e:
        logger.warning(f"Could not write API cache: {e}")
# -------------------------------------------------

class ApiFootballProvider:
    name = "api_football"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or load_settings().api_football_key
        self.base_url = "https://v3.football.api-sports.io"
        self.headers = {
            "x-apisports-key": self.api_key,
            "User-Agent": load_settings().user_agent,
        }

    def _fetch_json(self, url: str, timeout: int = 10) -> dict:
        """Centralized fetch with retries. Raises ApiFootballNetworkError on failure."""
        req = urllib.request.Request(url, headers=self.headers)
        max_retries = 4
        backoff = 2.0
        
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return json.loads(response.read().decode())
            except urllib.error.URLError as e:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                logger.error(f"Network failure for {url} after {max_retries} attempts: {e}")
                raise ApiFootballNetworkError(f"Network error: {e}") from e
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                logger.error(f"Unexpected error for {url} after {max_retries} attempts: {e}")
                raise ApiFootballNetworkError(f"Unexpected error: {e}") from e
                
        raise ApiFootballNetworkError(f"Failed to fetch {url}")

    def get_fixture_result(self, fixture_id: str) -> dict | None:
        if not self.api_key: return None
        
        # 1. Check Cache first
        cache_key = f"result_{fixture_id}"
        cached = _get_cache(cache_key, max_age_hours=1.0) # Cache live games for 1 hr, finished games forever
        
        if cached:
            return cached

        url = f"{self.base_url}/fixtures?id={fixture_id}"
        try:
            data = self._fetch_json(url)
            if data.get("response"):
                fixture = data["response"][0]
                result = {
                    "status": fixture["fixture"]["status"]["short"],
                    "home_score": fixture["goals"]["home"],
                    "away_score": fixture["goals"]["away"]
                }
                # If match is finished, cache it forever. If live/pending, cache it for 1 hr.
                if result["status"] in ("FT", "AET", "PEN"):
                    _set_cache(cache_key, result)
                else:
                    _set_cache(cache_key, result) # Relies on the 1 hr max_age_hours in _get_cache
                return result
        except ApiFootballNetworkError:
            raise 
        return None

    def fixture_stats(self, fixture_id: str) -> FixtureStats:
        if not self.api_key:
            raise ValueError("API_FOOTBALL_KEY is not configured.")

        # 1. Check Cache first (Stats never change, so we cache them indefinitely)
        cache_key = f"stats_{fixture_id}"
        cached_data = _get_cache(cache_key)
        if cached_data is not None:
            data = cached_data
        else:
            url = f"{self.base_url}/fixtures/statistics?fixture={fixture_id}"
            try:
                data = self._fetch_json(url)
                _set_cache(cache_key, data) # Save raw JSON to disk permanently
            except ApiFootballNetworkError:
                raise 

        if not data.get("response"):
            logger.warning(f"No statistics found for API-Football fixture {fixture_id}")
            return FixtureStats(provider=self.name, fixture_id=fixture_id, raw=data)

        team_stats: list[StatValue] = []
        for team_data in data["response"]:
            team_name = team_data.get("team", {}).get("name", "Unknown")
            for stat in team_data.get("statistics", []):
                stat_type = stat.get("type")
                raw_value = stat.get("value")
                if raw_value is None: continue
                try:
                    value = float(raw_value)
                except (ValueError, TypeError):
                    value = 0.0

                code = API_FOOTBALL_STAT_MAP.get(stat_type, StatCode.UNKNOWN)
                team_stats.append(StatValue(
                    code=code, value=value, provider=self.name,
                    subject=team_name, raw_name=stat_type,
                ))

        return FixtureStats(
            provider=self.name, fixture_id=fixture_id,
            team_stats=team_stats, raw=data,
        )

    def fixture_predictions(self, fixture_id: str) -> dict | None:
        if not self.api_key: raise ValueError("API_FOOTBALL_KEY is not configured.")
        url = f"{self.base_url}/predictions?fixture={fixture_id}"
        try:
            data = self._fetch_json(url)
        except ApiFootballNetworkError:
            raise
            
        if not data.get("response"):
            return None
        return data["response"][0]

    def fixtures_by_date(self, date_str: str) -> list[dict]:
        if not self.api_key: raise ValueError("API_FOOTBALL_KEY is not configured.")
        url = f"{self.base_url}/fixtures?date={date_str}"
        try:
            data = self._fetch_json(url)
            return data.get("response", [])
        except ApiFootballNetworkError:
            raise 
# src/predictionbot/sources/api_football.py
from __future__ import annotations

import logging
from typing import Any

from ..config import load_settings
from ..stats import FixtureStats, StatCode, StatValue, StatsProvider

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
    # --- NEW ADDITIONS ---
    "expected_goals": StatCode.EXPECTED_GOALS,
    "Ball Possession": StatCode.POSSESSION,
    "Goalkeeper Saves": StatCode.SAVES,
    "Offsides": StatCode.OFFSIDES,
    "Total passes": StatCode.PASSES_TOTAL,
}


class ApiFootballProvider:
    name = "api_football"

    def get_fixture_result(self, fixture_id: str) -> dict | None:
        """Fetches the final score for a specific API-Football fixture ID."""
        if not self.api_key:
            return None

        url = f"{self.base_url}/fixtures?id={fixture_id}"
        try:
            response = self.http.get_json(url, headers=self.headers)
            if response.get("response"):
                fixture = response["response"][0]
                goals = fixture.get("goals", {})
                return {
                    "home_score": goals.get("home"),
                    "away_score": goals.get("away"),
                    "status": fixture.get("fixture", {}).get("status", {}).get("short")
                }
        except Exception as e:
            logger.error(f"Failed to fetch result for {fixture_id}: {e}")
        return None

    def __init__(self, api_key: str | None = None):
        settings = load_settings()
        self.api_key = api_key or settings.api_football_key
        self.base_url = "https://v3.football.api-sports.io"

    def fixture_stats(self, fixture_id: str) -> FixtureStats:
        if not self.api_key:
            raise ValueError("API_FOOTBALL_KEY is not configured in settings or environment.")

        import urllib.request
        import json

        url = f"{self.base_url}/fixtures/statistics?fixture={fixture_id}"
        req = urllib.request.Request(
            url,
            headers={
                "x-apisports-key": self.api_key,
                "User-Agent": load_settings().user_agent,
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"Failed to fetch API-Football stats for fixture {fixture_id}: {e}")
            return FixtureStats(provider=self.name, fixture_id=fixture_id, raw={})

        if not data.get("response"):
            logger.warning(f"No statistics found for API-Football fixture {fixture_id}")
            return FixtureStats(provider=self.name, fixture_id=fixture_id, raw=data)

        team_stats: list[StatValue] = []
        
        for team_data in data["response"]:
            team_name = team_data.get("team", {}).get("name", "Unknown")
            for stat in team_data.get("statistics", []):
                stat_type = stat.get("type")
                raw_value = stat.get("value")
                
                # API-Football sometimes returns null or string values
                if raw_value is None:
                    continue
                
                try:
                    value = float(raw_value)
                except (ValueError, TypeError):
                    value = 0.0

                code = API_FOOTBALL_STAT_MAP.get(stat_type, StatCode.UNKNOWN)
                
                team_stats.append(StatValue(
                    code=code,
                    value=value,
                    provider=self.name,
                    subject=team_name,
                    raw_name=stat_type,
                ))

        return FixtureStats(
            provider=self.name,
            fixture_id=fixture_id,
            team_stats=team_stats,
            raw=data,
        )

    def fixture_predictions(self, fixture_id: str) -> dict | None:
        """Fetches predictions, xG, and form for an UPCOMING fixture."""
        if not self.api_key:
            raise ValueError("API_FOOTBALL_KEY is not configured.")

        import urllib.request
        import json

        url = f"{self.base_url}/predictions?fixture={fixture_id}"
        req = urllib.request.Request(
            url,
            headers={
                "x-apisports-key": self.api_key,
                "User-Agent": load_settings().user_agent,
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"Failed to fetch predictions for fixture {fixture_id}: {e}")
            return None

        if not data.get("response"):
            return None

        return data["response"][0]  # API returns a list with one object

    def fixtures_by_date(self, date_str: str) -> list[dict]:
        """Fetches all fixtures for a given date (YYYY-MM-DD) in one API call."""
        if not self.api_key:
            raise ValueError("API_FOOTBALL_KEY is not configured.")

        import urllib.request
        import json

        url = f"{self.base_url}/fixtures?date={date_str}"
        req = urllib.request.Request(
            url,
            headers={
                "x-apisports-key": self.api_key,
                "User-Agent": load_settings().user_agent,
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                return data.get("response", [])
        except Exception as e:
            logger.error(f"Failed to fetch fixtures for date {date_str}: {e}")
            return []
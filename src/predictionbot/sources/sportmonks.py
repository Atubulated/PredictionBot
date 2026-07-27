# src/predictionbot/sources/sportmonks.py
from __future__ import annotations

import logging
from typing import Any

from ..config import load_settings
from ..stats import FixtureStats, StatCode, StatValue, StatsProvider

logger = logging.getLogger(__name__)

# Mapping from Sportmonks stat types to our normalized StatCode
SPORTMONKS_STAT_MAP = {
    "shots_on_target": StatCode.SHOTS_ON_TARGET,
    "shots_off_target": StatCode.SHOTS_OFF_TARGET,
    "total_shots": StatCode.SHOTS_TOTAL,
    "blocked_shots": StatCode.SHOTS_BLOCKED,
    "corners": StatCode.CORNERS,
    "fouls": StatCode.FOULS,
    "yellowcards": StatCode.YELLOW_CARDS,
    "redcards": StatCode.RED_CARDS,
    "goals": StatCode.GOALS,
    "assists": StatCode.ASSISTS,
}


class SportmonksProvider:
    name = "sportmonks"

    def __init__(self, api_token: str | None = None):
        settings = load_settings()
        self.api_token = api_token or settings.sportmonks_api_token
        self.base_url = "https://api.sportmonks.com/v3/football"

    def fixture_stats(self, fixture_id: str) -> FixtureStats:
        if not self.api_token:
            raise ValueError("SPORTMONKS_API_TOKEN is not configured in settings or environment.")

        import urllib.request
        import json

        url = f"{self.base_url}/fixtures/{fixture_id}/statistics"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "User-Agent": load_settings().user_agent,
            }
        )

        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"Failed to fetch Sportmonks stats for fixture {fixture_id}: {e}")
            return FixtureStats(provider=self.name, fixture_id=fixture_id, raw={})

        # Sportmonks V3 wraps response in "data"
        stats_data = data.get("data", {})
        if not stats_data:
            logger.warning(f"No statistics found for Sportmonks fixture {fixture_id}")
            return FixtureStats(provider=self.name, fixture_id=fixture_id, raw=data)

        team_stats: list[StatValue] = []
        
        # Sportmonks structure varies, but typically has team-specific stats blocks
        # Adjust this parsing based on exact V3 response shape (e.g., stats might be nested under participants)
        participants = stats_data.get("participants", [])
        for participant in participants:
            team_name = participant.get("team", {}).get("name", "Unknown")
            stats = participant.get("stats", [])
            
            for stat in stats:
                type_code = stat.get("type_id") or stat.get("type", "").lower().replace(" ", "_")
                raw_value = stat.get("value")
                
                if raw_value is None:
                    continue
                
                try:
                    value = float(raw_value)
                except (ValueError, TypeError):
                    value = 0.0

                # Try to match by mapped key or fall back to UNKNOWN
                code = SPORTMONKS_STAT_MAP.get(type_code, StatCode.UNKNOWN)
                
                team_stats.append(StatValue(
                    code=code,
                    value=value,
                    provider=self.name,
                    subject=team_name,
                    raw_name=str(stat.get("type")),
                ))

        return FixtureStats(
            provider=self.name,
            fixture_id=fixture_id,
            team_stats=team_stats,
            raw=data,
        )
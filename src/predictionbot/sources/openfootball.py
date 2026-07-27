from __future__ import annotations

from datetime import datetime
from pathlib import Path

from predictionbot.domain import HistoricalMatch
from predictionbot.http import JsonHttpClient


class OpenFootballClient:
    raw_base_url = "https://raw.githubusercontent.com/openfootball/football.json/master"

    def __init__(self, http: JsonHttpClient) -> None:
        self.http = http

    def fetch_season(self, season: str, league_file: str) -> list[HistoricalMatch]:
        payload = self.http.get_json(f"{self.raw_base_url}/{season}/{league_file}")
        return parse_openfootball_payload(payload)

    def load_file(self, path: str | Path) -> list[HistoricalMatch]:
        import json

        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return parse_openfootball_payload(payload)


def parse_openfootball_payload(payload: dict) -> list[HistoricalMatch]:
    league = payload.get("name")
    matches = []
    for item in payload.get("matches", []):
        score = item.get("score") or {}
        if not isinstance(score, dict):
            continue
        full_time = score.get("ft")
        if not full_time or len(full_time) != 2:
            continue

        date_value = item.get("date")
        parsed_date = datetime.fromisoformat(date_value) if date_value else None
        matches.append(
            HistoricalMatch(
                date=parsed_date,
                home=item["team1"],
                away=item["team2"],
                home_goals=int(full_time[0]),
                away_goals=int(full_time[1]),
                league=league,
            )
        )
    return matches

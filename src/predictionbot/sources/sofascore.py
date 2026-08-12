from __future__ import annotations

from datetime import datetime
from typing import Any

from curl_cffi import requests

from predictionbot.domain import Fixture, Team
from predictionbot.http import JsonHttpClient  # Kept for backward compatibility


class SofascoreClient:
    base_url = "https://api.sofascore.com/api/v1"

    def __init__(self, http: JsonHttpClient | None = None) -> None:
        # Initialize curl_cffi session with Chrome impersonation to bypass Cloudflare
        self.session = requests.Session(impersonate="chrome120")
        self.session.headers.update({
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def scheduled_events(self, sport: str, date: str) -> list[Fixture]:
        url = f"{self.base_url}/sport/{sport}/scheduled-events/{date}"
        response = self.session.get(url)
        response.raise_for_status()
        payload = response.json()
        return [self._fixture_from_event(event) for event in payload.get("events", [])]

    def event_statistics(self, event_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/event/{event_id}/statistics"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def unique_tournament_seasons(self, tournament_id: int) -> list[dict[str, Any]]:
        """List available seasons for a unique tournament (most recent first)."""
        url = f"{self.base_url}/unique-tournament/{tournament_id}/seasons"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json().get("seasons", [])

    def tournament_events_last(
        self, tournament_id: int, season_id: int, page: int = 0
    ) -> list[Fixture]:
        """Return finished events for a tournament season, one page at a time.

        ``/events/last/{page}`` is the working replacement for the retired
        ``scheduled-events/{date}`` endpoint. Page 0 is the most recent block of
        finished matches; higher pages walk further back. A 404 marks the end of
        pagination and is surfaced as an empty list by the caller.
        """
        url = (
            f"{self.base_url}/unique-tournament/{tournament_id}"
            f"/season/{season_id}/events/last/{page}"
        )
        response = self.session.get(url)
        response.raise_for_status()
        payload = response.json()
        return [self._fixture_from_event(event) for event in payload.get("events", [])]

    def event_incidents(self, event_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/event/{event_id}/incidents"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def _fixture_from_event(self, event: dict[str, Any]) -> Fixture:
        start_timestamp = event.get("startTimestamp")
        starts_at = datetime.fromtimestamp(start_timestamp) if start_timestamp else None
        tournament = event.get("tournament") or {}

        home_team = event.get("homeTeam") or {}
        away_team = event.get("awayTeam") or {}

        return Fixture(
            source="sofascore",
            source_id=str(event.get("id")),
            starts_at=starts_at,
            home=Team(
                name=home_team.get("name", "Unknown home"),
                source_id=str(home_team.get("id") or ""),
            ),
            away=Team(
                name=away_team.get("name", "Unknown away"),
                source_id=str(away_team.get("id") or ""),
            ),
            league=tournament.get("name"),
            raw=event,
        )
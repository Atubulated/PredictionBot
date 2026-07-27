from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from predictionbot.risk import SafeOddsBand


class MarketFamily(StrEnum):
    TOTALS = "totals"
    BOTH_TEAMS_TO_SCORE = "btts"
    DOUBLE_CHANCE = "double_chance"
    HANDICAP = "handicap"
    CORNERS = "corners"
    BOOKINGS = "bookings"
    UNKNOWN = "unknown"
    TEAM_TOTALS = "team_totals"
    FIRST_HALF_TOTALS = "first_half_totals"
    SECOND_HALF_TOTALS = "second_half_totals"
    OVER_UNDER = "over_under"


@dataclass(frozen=True)
class Team:
    name: str
    source_id: str | None = None


@dataclass(frozen=True)
class Fixture:
    source: str
    source_id: str
    starts_at: datetime | None
    home: Team
    away: Team
    league: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.home.name} vs {self.away.name}"


@dataclass(frozen=True)
class HistoricalMatch:
    date: datetime | None
    home: str
    away: str
    home_goals: int
    away_goals: int
    league: str | None = None
    # --- ADD THESE LINES ---
    home_corners: int | None = None
    away_corners: int | None = None
    home_cards: int | None = None
    away_cards: int | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_possession: float | None = None
    away_possession: float | None = None
    home_saves: int | None = None
    away_saves: int | None = None
    home_offsides: int | None = None
    away_offsides: int | None = None
    home_passes: int | None = None
    away_passes: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


@dataclass(frozen=True)
class MarketOdds:
    bookmaker: str
    fixture_id: str
    family: MarketFamily
    market: str
    selection: str
    odds: float
    line: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Prediction:
    fixture: Fixture
    market: MarketOdds
    model_probability: float
    implied_probability: float
    edge: float
    confidence: str
    safe_odds_band: SafeOddsBand
    reason: str

    @property
    def fair_odds(self) -> float:
        if self.model_probability <= 0:
            return float("inf")
        return round(1 / self.model_probability, 3)

    @property
    def is_safe_odds(self) -> bool:
        return self.safe_odds_band in {SafeOddsBand.VERY_SAFE, SafeOddsBand.SAFE}

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class StatCode(StrEnum):
    CORNERS = "corners"
    FOULS = "fouls"
    YELLOW_CARDS = "yellow_cards"
    RED_CARDS = "red_cards"
    SHOTS_TOTAL = "shots_total"
    SHOTS_ON_TARGET = "shots_on_target"
    SHOTS_OFF_TARGET = "shots_off_target"
    SHOTS_BLOCKED = "shots_blocked"
    GOALS = "goals"
    ASSISTS = "assists"
    # --- NEW ADDITIONS ---
    EXPECTED_GOALS = "expected_goals"
    POSSESSION = "possession"
    SAVES = "saves"
    OFFSIDES = "offsides"
    PASSES_TOTAL = "passes_total"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StatValue:
    code: StatCode
    value: float
    provider: str
    subject: str | None = None
    subject_id: str | None = None
    period: str = "full_match"
    raw_name: str | None = None


@dataclass(frozen=True)
class FixtureStats:
    provider: str
    fixture_id: str
    team_stats: list[StatValue] = field(default_factory=list)
    player_stats: list[StatValue] = field(default_factory=list)
    raw: dict | list | None = None

    def team_total(self, code: StatCode) -> float:
        return sum(stat.value for stat in self.team_stats if stat.code == code)


@dataclass(frozen=True)
class CombinedFixtureStats:
    fixture_id: str
    sources: list[str]
    team_stats: list[StatValue]
    player_stats: list[StatValue]
    conflicts: list[str]


class StatsProvider(Protocol):
    name: str

    def fixture_stats(self, fixture_id: str) -> FixtureStats:
        ...


def combine_fixture_stats(fixture_id: str, stats: list[FixtureStats]) -> CombinedFixtureStats:
    team_stats = [stat for item in stats for stat in item.team_stats]
    player_stats = [stat for item in stats for stat in item.player_stats]
    return CombinedFixtureStats(
        fixture_id=fixture_id,
        sources=[item.provider for item in stats],
        team_stats=team_stats,
        player_stats=player_stats,
        conflicts=_find_conflicts(team_stats),
    )


def _find_conflicts(stats: list[StatValue], tolerance: float = 0.0) -> list[str]:
    grouped = defaultdict(list)
    for stat in stats:
        key = (stat.subject or "", stat.code.value, stat.period)
        grouped[key].append(stat)

    conflicts = []
    for (subject, code, period), items in grouped.items():
        values = {item.value for item in items}
        providers = {item.provider for item in items}
        if len(providers) > 1 and max(values) - min(values) > tolerance:
            conflicts.append(f"{subject or 'fixture'} {code} {period}: {sorted(values)}")
    return conflicts

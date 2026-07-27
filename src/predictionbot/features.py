from __future__ import annotations

from dataclasses import dataclass

from predictionbot.domain import HistoricalMatch
from predictionbot.names import normalize_team_name


@dataclass(frozen=True)
class TeamGoalProfile:
    team: str
    matches: int
    goals_for_avg: float
    goals_against_avg: float
    total_goals_avg: float
    over_15_rate: float
    over_25_rate: float
    btts_rate: float


def build_goal_profile(team: str, matches: list[HistoricalMatch], limit: int = 10) -> TeamGoalProfile:
    normalized_team = normalize_team_name(team)
    relevant = [
        match
        for match in matches
        if normalize_team_name(match.home) == normalized_team or normalize_team_name(match.away) == normalized_team
    ]
    relevant = relevant[-limit:]
    if not relevant:
        return TeamGoalProfile(team, 0, 0, 0, 0, 0, 0, 0)

    goals_for = []
    goals_against = []
    totals = []
    btts_hits = 0

    for match in relevant:
        if normalize_team_name(match.home) == normalized_team:
            team_goals = match.home_goals
            opponent_goals = match.away_goals
        else:
            team_goals = match.away_goals
            opponent_goals = match.home_goals

        goals_for.append(team_goals)
        goals_against.append(opponent_goals)
        totals.append(match.total_goals)
        if team_goals > 0 and opponent_goals > 0:
            btts_hits += 1

    count = len(relevant)
    return TeamGoalProfile(
        team=team,
        matches=count,
        goals_for_avg=sum(goals_for) / count,
        goals_against_avg=sum(goals_against) / count,
        total_goals_avg=sum(totals) / count,
        over_15_rate=sum(1 for total in totals if total > 1.5) / count,
        over_25_rate=sum(1 for total in totals if total > 2.5) / count,
        btts_rate=btts_hits / count,
    )

from __future__ import annotations

from dataclasses import dataclass

from predictionbot.domain import HistoricalMatch
from predictionbot.names import normalize_team_name


def index_history_by_team(
    matches: list[HistoricalMatch],
) -> dict[str, list[HistoricalMatch]]:
    """Bucket a match pool by normalized team name (each match appears under both sides).

    Building this once lets callers hand each scorer only the handful of matches
    involving a fixture's two teams instead of the whole pool. ``build_goal_profile``
    / ``build_stat_profile`` still re-filter by team and take ``[-limit:]``, so the
    profile they compute from the bucketed subset is identical to what they would
    compute from the full pool — only far cheaper (the per-row ``normalize_team_name``
    scan happens once here rather than once per scorer call).
    """
    index: dict[str, list[HistoricalMatch]] = {}
    for match in matches:
        index.setdefault(normalize_team_name(match.home), []).append(match)
        index.setdefault(normalize_team_name(match.away), []).append(match)
    return index


def history_for_fixture(
    index: dict[str, list[HistoricalMatch]],
    home: str,
    away: str,
) -> list[HistoricalMatch]:
    """Return the matches involving either team, preserving original pool order.

    A match where the two teams played each other would otherwise be duplicated
    (it lives under both buckets), so dedupe by identity while keeping order.
    """
    home_key = normalize_team_name(home)
    away_key = normalize_team_name(away)
    if home_key == away_key:
        return list(index.get(home_key, ()))
    seen: set[int] = set()
    combined: list[HistoricalMatch] = []
    for match in (*index.get(home_key, ()), *index.get(away_key, ())):
        if id(match) not in seen:
            seen.add(id(match))
            combined.append(match)
    return combined


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


@dataclass(frozen=True)
class TeamStatProfile:
    """Recency-weighted average for a single per-team stat (corners, shots, cards, ...)."""

    team: str
    stat: str
    samples: int
    for_avg: float
    against_avg: float


# Which HistoricalMatch attributes hold each stat, keyed by a short stat name.
_STAT_FIELDS = {
    "corners": ("home_corners", "away_corners"),
    "shots": ("home_shots", "away_shots"),
    "cards": ("home_cards", "away_cards"),
    "saves": ("home_saves", "away_saves"),
    "offsides": ("home_offsides", "away_offsides"),
    "passes": ("home_passes", "away_passes"),
    "possession": ("home_possession", "away_possession"),
}


def build_stat_profile(team: str, stat: str, matches: list[HistoricalMatch], limit: int = 10) -> TeamStatProfile:
    """Average a per-team stat over the team's most recent matches with observed data.

    Only rows where the stat is present (not None) count toward ``samples`` so
    callers can gate on a minimum sample size before trusting the average.
    """
    fields = _STAT_FIELDS.get(stat)
    if fields is None:
        return TeamStatProfile(team, stat, 0, 0.0, 0.0)
    home_field, away_field = fields
    normalized_team = normalize_team_name(team)

    for_values: list[float] = []
    against_values: list[float] = []
    relevant = [
        match
        for match in matches
        if normalize_team_name(match.home) == normalized_team or normalize_team_name(match.away) == normalized_team
    ]
    for match in relevant[-limit:]:
        if normalize_team_name(match.home) == normalized_team:
            team_value, opp_value = getattr(match, home_field), getattr(match, away_field)
        else:
            team_value, opp_value = getattr(match, away_field), getattr(match, home_field)
        if team_value is None:
            continue
        for_values.append(float(team_value))
        if opp_value is not None:
            against_values.append(float(opp_value))

    samples = len(for_values)
    if samples == 0:
        return TeamStatProfile(team, stat, 0, 0.0, 0.0)
    return TeamStatProfile(
        team=team,
        stat=stat,
        samples=samples,
        for_avg=sum(for_values) / samples,
        against_avg=(sum(against_values) / len(against_values)) if against_values else 0.0,
    )


def expected_stat_total(
    home: TeamStatProfile,
    away: TeamStatProfile,
    league_avg_team: float,
    shrink: float = 5.0,
) -> float:
    """Bayesian-shrink each side's for/against average toward the league mean, then sum.

    Mirrors the corners model: a team's expected contribution blends its own
    'for' rate with the opponent's 'against' rate, each shrunk toward the league
    average by ``shrink`` pseudo-matches.
    """
    def _shrunk(profile: TeamStatProfile, use_for: bool) -> float:
        value = profile.for_avg if use_for else profile.against_avg
        if profile.samples == 0 or value == 0.0:
            return league_avg_team
        weight = profile.samples / (profile.samples + shrink)
        return value * weight + league_avg_team * (1 - weight)

    home_contrib = (_shrunk(home, True) + _shrunk(away, False)) / 2
    away_contrib = (_shrunk(away, True) + _shrunk(home, False)) / 2
    return home_contrib + away_contrib

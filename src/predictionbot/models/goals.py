from __future__ import annotations

import math

from predictionbot.features import TeamGoalProfile


def poisson_cdf(k: int, expected_goals: float) -> float:
    if expected_goals < 0:
        raise ValueError("Expected goals cannot be negative")
    return sum((math.exp(-expected_goals) * expected_goals**i) / math.factorial(i) for i in range(k + 1))


def poisson_pmf(k: int, expected_goals: float) -> float:
    if expected_goals < 0:
        raise ValueError("Expected goals cannot be negative")
    return (math.exp(-expected_goals) * expected_goals**k) / math.factorial(k)


def probability_over(line: float, expected_total_goals: float) -> float:
    whole_goal_cutoff = math.floor(line)
    return 1 - poisson_cdf(whole_goal_cutoff, expected_total_goals)


def estimate_expected_total(home: TeamGoalProfile, away: TeamGoalProfile, league_avg_total: float = 2.65) -> float:
    home_expected, away_expected = estimate_expected_goals(home, away, league_avg_total=league_avg_total)
    return home_expected + away_expected


def estimate_expected_goals(
    home: TeamGoalProfile,
    away: TeamGoalProfile,
    league_avg_total: float = 2.65,
) -> tuple[float, float]:
    if home.matches == 0 and away.matches == 0:
        return league_avg_total / 2, league_avg_total / 2

    home_component = (home.goals_for_avg + away.goals_against_avg) / 2 if away.matches else home.goals_for_avg
    away_component = (away.goals_for_avg + home.goals_against_avg) / 2 if home.matches else away.goals_for_avg
    estimated_total = home_component + away_component

    if estimated_total <= 0:
        return league_avg_total / 2, league_avg_total / 2

    # Blend with league average so tiny samples do not swing too wildly.
    sample_weight = min((home.matches + away.matches) / 20, 1)
    blended_total = (estimated_total * sample_weight) + (league_avg_total * (1 - sample_weight))
    scale = blended_total / estimated_total
    return home_component * scale, away_component * scale


def outcome_probabilities(home_expected: float, away_expected: float, max_goals: int = 10) -> dict[str, float]:
    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    for home_goals in range(max_goals + 1):
        home_probability = poisson_pmf(home_goals, home_expected)
        for away_goals in range(max_goals + 1):
            score_probability = home_probability * poisson_pmf(away_goals, away_expected)
            if home_goals > away_goals:
                home_win += score_probability
            elif home_goals == away_goals:
                draw += score_probability
            else:
                away_win += score_probability
    total = home_win + draw + away_win
    if total <= 0:
        return {"home": 0.0, "draw": 0.0, "away": 0.0}
    return {"home": home_win / total, "draw": draw / total, "away": away_win / total}


def handicap_probability(
    home_expected: float,
    away_expected: float,
    selection_side: str,
    line: float,
    max_goals: int = 10,
) -> float:
    hit = 0.0
    total = 0.0
    for home_goals in range(max_goals + 1):
        home_probability = poisson_pmf(home_goals, home_expected)
        for away_goals in range(max_goals + 1):
            score_probability = home_probability * poisson_pmf(away_goals, away_expected)
            total += score_probability
            if selection_side == "home" and home_goals + line > away_goals:
                hit += score_probability
            elif selection_side == "away" and away_goals + line > home_goals:
                hit += score_probability
    return hit / total if total > 0 else 0.0

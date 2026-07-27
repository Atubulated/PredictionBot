from predictionbot.models.goals import estimate_expected_total, probability_over
from predictionbot.features import TeamGoalProfile


def test_probability_over_increases_with_expected_goals() -> None:
    assert probability_over(2.5, 3.2) > probability_over(2.5, 1.8)


def test_expected_total_uses_profiles() -> None:
    home = TeamGoalProfile("Home", 10, 2.0, 1.0, 3.0, 0.8, 0.6, 0.5)
    away = TeamGoalProfile("Away", 10, 1.5, 1.5, 3.0, 0.8, 0.6, 0.5)

    assert estimate_expected_total(home, away) == 3.0

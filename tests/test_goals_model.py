from predictionbot.models.goals import estimate_expected_total, probability_over
from predictionbot.features import TeamGoalProfile


def test_probability_over_increases_with_expected_goals() -> None:
    assert probability_over(2.5, 3.2) > probability_over(2.5, 1.8)


def test_expected_total_uses_profiles() -> None:
    # The estimator is now an attack/defense-strength model (not a plain average
    # of total_goals_avg), so it no longer returns exactly 3.0 for these inputs.
    # What must hold: the total is a sensible positive number, and a stronger
    # attacking / weaker defending pair produces a higher total than a weak one.
    home = TeamGoalProfile("Home", 10, 2.0, 1.0, 3.0, 0.8, 0.6, 0.5)
    away = TeamGoalProfile("Away", 10, 1.5, 1.5, 3.0, 0.8, 0.6, 0.5)

    total = estimate_expected_total(home, away)
    assert 2.0 < total < 5.0

    weak_home = TeamGoalProfile("Weak", 10, 0.6, 1.8, 1.5, 0.4, 0.2, 0.3)
    weak_away = TeamGoalProfile("Weak2", 10, 0.5, 2.0, 1.5, 0.4, 0.2, 0.3)
    assert estimate_expected_total(weak_home, weak_away) < total

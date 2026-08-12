from predictionbot.risk import SafeOddsBand, SafeOddsRule


def test_safe_odds_rule_classifies_below_80_as_medium_risk() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.7999) == SafeOddsBand.MEDIUM_RISK


def test_safe_odds_rule_classifies_80_to_below_95_as_safe() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.80) == SafeOddsBand.SAFE
    assert rule.classify(0.9499) == SafeOddsBand.SAFE


def test_safe_odds_rule_classifies_95_plus_as_very_safe() -> None:
    # Very-safe threshold was tightened from 0.90 to 0.95 so only near-locks
    # carry the top band.
    rule = SafeOddsRule()

    assert rule.classify(0.95) == SafeOddsBand.VERY_SAFE
    assert rule.classify(0.90) == SafeOddsBand.SAFE


def test_safe_odds_rule_classifies_below_65_as_high_risk() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.6499) == SafeOddsBand.HIGH_RISK

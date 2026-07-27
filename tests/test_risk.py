from predictionbot.risk import SafeOddsBand, SafeOddsRule


def test_safe_odds_rule_classifies_below_80_as_medium_risk() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.7999) == SafeOddsBand.MEDIUM_RISK


def test_safe_odds_rule_classifies_80_to_90_as_safe() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.80) == SafeOddsBand.SAFE
    assert rule.classify(0.8999) == SafeOddsBand.SAFE


def test_safe_odds_rule_classifies_90_plus_as_very_safe() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.90) == SafeOddsBand.VERY_SAFE


def test_safe_odds_rule_classifies_below_65_as_high_risk() -> None:
    rule = SafeOddsRule()

    assert rule.classify(0.6499) == SafeOddsBand.HIGH_RISK

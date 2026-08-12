from datetime import date, timedelta

from predictionbot.intent_router import IntentRouter


def _router() -> IntentRouter:
    # No API key -> deterministic keyword-fallback path (no network).
    return IntentRouter(http=None, api_key="")


def test_k_suffix_odds_are_multiplied_by_1000() -> None:
    intent = _router().parse_intent("Give me 1k odds for the week")
    assert intent["target_odds"] == 1000.0


def test_decimal_k_suffix_odds() -> None:
    intent = _router().parse_intent("2.5k odd accumulator")
    assert intent["target_odds"] == 2500.0


def test_plain_odds_are_unchanged() -> None:
    intent = _router().parse_intent("10 odds today")
    assert intent["target_odds"] == 10.0


def test_no_odds_mentioned_stays_none() -> None:
    intent = _router().parse_intent("corners for the weekend")
    assert intent["target_odds"] is None


def test_week_sets_seven_day_window() -> None:
    intent = _router().parse_intent("give me a 5 odd for the week")
    start = date.fromisoformat(intent["date"])
    end = date.fromisoformat(intent["end_date"])
    assert (end - start) == timedelta(days=6)


def test_weekend_targets_saturday_and_sunday() -> None:
    intent = _router().parse_intent("20 odd for the weekend")
    start = date.fromisoformat(intent["date"])
    end = date.fromisoformat(intent["end_date"])
    assert start.weekday() == 5  # Saturday
    assert end.weekday() == 6  # Sunday
    assert (end - start) == timedelta(days=1)


def test_explicit_date_wins_over_week_start() -> None:
    intent = _router().parse_intent("5 odds for the week starting 2026-09-01")
    assert intent["date"] == "2026-09-01"
    # end_date is still one week out from the explicit start.
    assert intent["end_date"] == "2026-09-07"


def test_market_family_keyword_survives_range() -> None:
    intent = _router().parse_intent("corners over the week")
    assert intent["market_family"] == "corners"

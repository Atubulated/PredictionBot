# tests/test_matching.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from predictionbot.matching import FixtureMatcher


@dataclass
class MockExternalFixture:
    """A mock object that satisfies the ExternalFixture Protocol."""
    id: str
    home_team: str
    away_team: str
    date: datetime
    league: str | None = None


@pytest.fixture
def matcher():
    return FixtureMatcher(tolerance_days=1)


def test_exact_match(matcher):
    """Test that exact names and exact date return the correct ID."""
    local_date = datetime(2026, 8, 21, 19, 0)
    candidates = [
        MockExternalFixture(
            id="12345",
            home_team="Arsenal",
            away_team="Chelsea",
            date=local_date,
            league="Premier League"
        )
    ]
    
    result = matcher.match("Arsenal", "Chelsea", local_date, candidates)
    
    assert result is not None
    assert result.id == "12345"


def test_normalized_casing_match(matcher):
    """Test that the normalizer handles different casing and whitespace."""
    local_date = datetime(2026, 8, 21, 19, 0)
    candidates = [
        MockExternalFixture(
            id="88888",
            home_team="tottenham hotspur",
            away_team="Aston Villa",
            date=local_date,
        )
    ]
    
    # Bet9ja might return "Tottenham Hotspur" while API returns lowercase
    result = matcher.match("Tottenham Hotspur", "aston villa", local_date, candidates)
    
    assert result is not None
    assert result.id == "88888"


def test_date_tolerance_match(matcher):
    """Test that a match is found even if the date is off by 1 day (timezone shifts)."""
    local_date = datetime(2026, 8, 21, 19, 0)
    # Candidate is scheduled 1 day later in the external API
    candidate_date = local_date + timedelta(days=1)
    
    candidates = [
        MockExternalFixture(
            id="77777",
            home_team="Brighton",
            away_team="Wolves",
            date=candidate_date,
        )
    ]
    
    result = matcher.match("Brighton", "Wolves", local_date, candidates)
    
    assert result is not None
    assert result.id == "77777"


def test_no_match_different_teams(matcher):
    """Test that completely different teams return None."""
    local_date = datetime(2026, 8, 21, 19, 0)
    candidates = [
        MockExternalFixture(
            id="66666",
            home_team="Everton",
            away_team="Fulham",
            date=local_date,
        )
    ]
    
    result = matcher.match("Arsenal", "Chelsea", local_date, candidates)
    
    assert result is None


def test_no_match_date_too_far(matcher):
    """Test that a date difference greater than tolerance returns None."""
    local_date = datetime(2026, 8, 21, 19, 0)
    # Candidate is 3 days later
    candidate_date = local_date + timedelta(days=3)
    
    candidates = [
        MockExternalFixture(
            id="55555",
            home_team="Arsenal",
            away_team="Chelsea",
            date=candidate_date,
        )
    ]
    
    result = matcher.match("Arsenal", "Chelsea", local_date, candidates)
    
    assert result is None
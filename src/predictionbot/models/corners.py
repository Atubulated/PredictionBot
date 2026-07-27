# src/predictionbot/models/corners.py
from __future__ import annotations

import math
from dataclasses import dataclass

from predictionbot.domain import HistoricalMatch, MarketFamily, Prediction, Fixture, MarketOdds
from predictionbot.stats import StatCode, FixtureStats

@dataclass(frozen=True)
class CornerExpectation:
    home_expected: float
    away_expected: float
    total_expected: float

def calculate_expected_corners(
    home_team: str,
    away_team: str,
    history: list[HistoricalMatch],
    live_stats: FixtureStats | None = None,
) -> CornerExpectation:
    """
    Calculates Expected Corners (xC) for a fixture.
    """
    # Fallback league average if no history is found
    league_avg_total = 9.5 
    league_avg_home = 5.2
    league_avg_away = 4.3

    # NOTE: Using 'home' and 'away' to match your HistoricalMatch dataclass
    home_matches = [m for m in history if m.home == home_team]
    away_matches = [m for m in history if m.away == away_team]
    
    # Calculate historical averages
    home_corners = [m.home_corners for m in home_matches if m.home_corners is not None]
    away_corners = [m.away_corners for m in away_matches if m.away_corners is not None]
    
    home_avg = sum(home_corners) / len(home_corners) if home_corners else league_avg_home
    away_avg = sum(away_corners) / len(away_corners) if away_corners else league_avg_away
    
    # Blend historical average with league average (Bayesian shrinkage)
    home_weight = len(home_matches) / (len(home_matches) + 5)
    away_weight = len(away_matches) / (len(away_matches) + 5)
    
    home_xc = (home_avg * home_weight) + (league_avg_home * (1 - home_weight))
    away_xc = (away_avg * away_weight) + (league_avg_away * (1 - away_weight))
    
    return CornerExpectation(
        home_expected=round(home_xc, 2),
        away_expected=round(away_xc, 2),
        total_expected=round(home_xc + away_xc, 2)
    )

def poisson_probability(k: int, lam: float) -> float:
    """Calculate Poisson probability of exactly k events given expected value lam."""
    return (math.exp(-lam) * (lam ** k)) / math.factorial(k)

def corner_over_probability(line: float, expected_total: float) -> float:
    """
    Calculate probability of Total Corners > line.
    """
    threshold = math.ceil(line)
    prob_under_or_push = sum(poisson_probability(k, expected_total) for k in range(threshold))
    return 1.0 - prob_under_or_push

def corner_under_probability(line: float, expected_total: float) -> float:
    """Calculate probability of Total Corners < line."""
    threshold = math.floor(line)
    return sum(poisson_probability(k, expected_total) for k in range(threshold + 1))
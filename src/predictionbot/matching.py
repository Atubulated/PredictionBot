# src/predictionbot/matching.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Protocol

from .names import normalize_team_name # Assuming you have this from your accomplishments

logger = logging.getLogger(__name__)

class ExternalFixture(Protocol):
    id: str
    home_team: str
    away_team: str
    date: datetime
    league: str | None

class FixtureMatcher:
    """
    Matches a local/Bet9ja fixture to an external provider fixture.
    Strategy:
    1. Normalize team names.
    2. Check date proximity (± 1 day to account for timezone/scheduling shifts).
    3. Optional: League name matching for higher confidence.
    """

    def __init__(self, tolerance_days: int = 1):
        self.tolerance_days = tolerance_days

    def match(
        self,
        local_home: str,
        local_away: str,
        local_date: datetime,
        candidates: list[ExternalFixture],
    ) -> ExternalFixture | None:
        norm_home = normalize_team_name(local_home)
        norm_away = normalize_team_name(local_away)

        # Strip timezones to prevent the "offset-naive" crash
        local_date_naive = local_date.replace(tzinfo=None) if local_date.tzinfo else local_date

        best_match = None
        best_score = 0.0

        for candidate in candidates:
            cand_date_naive = candidate.date.replace(tzinfo=None) if candidate.date.tzinfo else candidate.date

            # Date check
            days_diff = abs((cand_date_naive - local_date_naive).days)
            if days_diff > self.tolerance_days:
                continue

            # Name check
            cand_home = normalize_team_name(candidate.home_team)
            cand_away = normalize_team_name(candidate.away_team)

            score = 0.0
            if norm_home == cand_home:
                score += 0.5
            if norm_away == cand_away:
                score += 0.5

            # Bonus for exact date match
            if days_diff == 0:
                score += 0.1

            if score > best_score:
                best_score = score
                best_match = candidate

        # Require at least a 0.9 score (both teams match + same day) to be confident
        if best_score >= 0.9:
            return best_match

        return None
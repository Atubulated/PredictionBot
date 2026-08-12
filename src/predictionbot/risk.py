from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SafeOddsBand(StrEnum):
    VERY_SAFE = "very_safe"
    SAFE = "safe"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK = "high_risk"


@dataclass(frozen=True)
class SafeOddsRule:
    min_safe_probability: float = 0.80
    min_very_safe_probability: float = 0.95
    min_medium_probability: float = 0.65

    def classify(self, probability: float) -> SafeOddsBand:
        if probability >= self.min_very_safe_probability:
            return SafeOddsBand.VERY_SAFE
        if probability >= self.min_safe_probability:
            return SafeOddsBand.SAFE
        if probability >= self.min_medium_probability:
            return SafeOddsBand.MEDIUM_RISK
        return SafeOddsBand.HIGH_RISK

    def is_safe(self, probability: float) -> bool:
        return self.classify(probability) in {SafeOddsBand.VERY_SAFE, SafeOddsBand.SAFE}


RISK_LADDER = [
    SafeOddsBand.VERY_SAFE,
    SafeOddsBand.SAFE,
    SafeOddsBand.MEDIUM_RISK,
    SafeOddsBand.HIGH_RISK,
]


DEFAULT_SAFE_ODDS_RULE = SafeOddsRule()

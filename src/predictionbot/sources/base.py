# src/predictionbot/sources/base.py
from __future__ import annotations
from typing import Protocol

class StatsProvider(Protocol):
    def fixture_stats(self, fixture_id: str) -> FixtureStats:
        ...
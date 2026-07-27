from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from predictionbot.domain import Fixture, MarketOdds, Prediction


SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    starts_at TEXT,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    league TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS market_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bookmaker TEXT NOT NULL,
    fixture_id TEXT NOT NULL,
    family TEXT NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    odds REAL NOT NULL,
    line REAL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_source TEXT NOT NULL,
    fixture_id TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    market TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    odds REAL NOT NULL,
    model_probability REAL NOT NULL,
    implied_probability REAL NOT NULL,
    edge REAL NOT NULL,
    confidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Repository:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def save_fixture(self, fixture: Fixture) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO fixtures
                (source, source_id, starts_at, home, away, league, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fixture.source,
                    fixture.source_id,
                    fixture.starts_at.isoformat() if fixture.starts_at else None,
                    fixture.home.name,
                    fixture.away.name,
                    fixture.league,
                    json.dumps(fixture.raw),
                ),
            )

    def save_market(self, market: MarketOdds) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_odds
                (bookmaker, fixture_id, family, market, selection, odds, line, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market.bookmaker,
                    market.fixture_id,
                    market.family.value,
                    market.market,
                    market.selection,
                    market.odds,
                    market.line,
                    json.dumps(market.raw),
                ),
            )

    def save_prediction(self, prediction: Prediction) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO predictions
                (fixture_source, fixture_id, bookmaker, market, selection_name, odds,
                 model_probability, implied_probability, edge, confidence, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction.fixture.source,
                    prediction.fixture.source_id,
                    prediction.market.bookmaker,
                    prediction.market.market,
                    prediction.market.selection,
                    prediction.market.odds,
                    prediction.model_probability,
                    prediction.implied_probability,
                    prediction.edge,
                    prediction.safe_odds_band.value,
                    prediction.reason,
                ),
            )

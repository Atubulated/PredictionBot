import importlib.util
import os
from datetime import datetime, timezone
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "backfill_usl_brazil",
    os.path.join(os.path.dirname(__file__), "..", "backfill_usl_brazil.py"),
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _fixture(*, ts=1723248000, status="finished", home_score=2, away_score=1):
    raw = {
        "startTimestamp": ts,
        "status": {"type": status},
        "homeScore": {"current": home_score},
        "awayScore": {"current": away_score},
    }
    return SimpleNamespace(
        home=SimpleNamespace(name="Home FC"),
        away=SimpleNamespace(name="Away FC"),
        raw=raw,
    )


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls =[]

    def tournament_events_last(self, tournament_id, season_id, page):
        self.calls.append((tournament_id, season_id, page))
        value = self.pages.get(page, [])
        if isinstance(value, Exception):
            raise value
        return value


def test_target_tournament_ids_cover_usl_and_brazil_lower_tiers():
    assert mod.TARGET_TOURNAMENTS == {
        13363: "USL Championship",
        13362: "USL League One",
        390: "Brazil Serie B",
        1281: "Brazil Serie C",
        10326: "Brazil Serie D",
    }


def test_event_date_comes_from_utc_timestamp():
    fx = _fixture(ts=int(datetime(2024, 8, 10, tzinfo=timezone.utc).timestamp()))
    assert mod._event_date(fx) == "2024-08-10"


def test_finished_rows_paginates_and_normalizes(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    client = _FakeClient({0: [_fixture()], 1:[]})

    rows = mod._finished_rows(client, 13363, 70263, "USL Championship")

    assert len(rows) == 1
    row = rows[0]
    assert row["league"] == "USL Championship"
    assert row["home_team"] == "Home FC"
    assert row["away_team"] == "Away FC"
    assert row["home_score"] == 2
    assert row["away_score"] == 1
    assert client.calls == [(13363, 70263, 0), (13363, 70263, 1)]


def test_finished_rows_skips_unfinished_or_incomplete_events(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    client = _FakeClient({
        0: [
            _fixture(status="scheduled"),
            _fixture(home_score=None),
            _fixture(ts=None),
        ],
        1: [],
    })
    assert mod._finished_rows(client, 390, 1, "Brazil Serie B") ==[]


def test_finished_rows_stops_cleanly_on_404(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    client = _FakeClient({0: [_fixture()], 1: RuntimeError("HTTP Error 404")})
    rows = mod._finished_rows(client, 1281, 1, "Brazil Serie C")
    assert len(rows) == 1

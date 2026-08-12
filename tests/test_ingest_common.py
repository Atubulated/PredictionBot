import importlib.util
import os

# Load ingest_common.py from the repo root (it is not under src/, so import by path).
_spec = importlib.util.spec_from_file_location(
    "ingest_common",
    os.path.join(os.path.dirname(__file__), "..", "ingest_common.py"),
)
ingest_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_common)

build_row = ingest_common.build_row
stable_id = ingest_common.stable_id
upsert_matches = ingest_common.upsert_matches


def test_stable_id_is_deterministic_across_calls():
    a = stable_id("2024-05-01", "Home FC", "Away FC")
    b = stable_id("2024-05-01", "Home FC", "Away FC")
    assert a == b


def test_stable_id_is_case_insensitive():
    # Case folding lets the same fixture from sources with different casing
    # collapse onto one row. (Kept byte-compatible with the pre-existing
    # global_lower_tiers formula so already-stored rows keep their IDs.)
    assert stable_id("2024-05-01", "Home FC", "Away FC") == stable_id(
        "2024-05-01", "home fc", "away fc"
    )


def test_stable_id_differs_for_different_matches():
    assert stable_id("2024-05-01", "A", "B") != stable_id("2024-05-01", "B", "A")
    assert stable_id("2024-05-01", "A", "B") != stable_id("2024-05-02", "A", "B")


def test_build_row_has_schema_columns_and_extras():
    row = build_row(
        "2024-05-01", "Home", "Away", 2, 1, "Test League",
        home_shots=10, away_shots=4,
    )
    assert row["api_football_id"] == stable_id("2024-05-01", "Home", "Away")
    assert row["home_team"] == "Home"
    assert row["away_team"] == "Away"
    assert row["home_score"] == 2
    assert row["away_score"] == 1
    assert row["match_date"] == "2024-05-01"
    assert row["league"] == "Test League"
    assert row["home_shots"] == 10
    assert row["away_shots"] == 4


class _FakeTable:
    def __init__(self, store, fail_first=False):
        self.store = store
        self.fail_first = fail_first
        self._batch = None

    def upsert(self, batch, on_conflict=None):
        self._batch = batch
        self._conflict = on_conflict
        return self

    def execute(self):
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("simulated batch failure")
        self.store.extend(self._batch)
        return self


class _FakeSupabase:
    def __init__(self, fail_first=False):
        self.store = []
        self._fail_first = fail_first

    def table(self, name):
        assert name == "match_results"
        tbl = _FakeTable(self.store, fail_first=self._fail_first)
        self._fail_first = False  # only the very first execute fails
        return tbl


def test_upsert_matches_writes_rows_with_conflict_key():
    supa = _FakeSupabase()
    rows = [build_row("2024-05-01", f"H{i}", f"A{i}", 1, 0, "L") for i in range(3)]
    written = upsert_matches(supa, rows, "L")
    assert written == 3
    assert len(supa.store) == 3


def test_upsert_matches_empty_is_noop():
    supa = _FakeSupabase()
    assert upsert_matches(supa, [], "L") == 0
    assert supa.store == []


def test_upsert_matches_survives_batch_failure():
    # A failing batch is logged, not raised — the count reflects only what wrote.
    supa = _FakeSupabase(fail_first=True)
    rows = [build_row("2024-05-01", f"H{i}", f"A{i}", 1, 0, "L") for i in range(2)]
    written = upsert_matches(supa, rows, "L")
    assert written == 0  # single batch, it failed, nothing persisted
    assert supa.store == []

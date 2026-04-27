"""Tests for mindkeep.storage.Storage."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mindkeep.storage import Storage, SCHEMA_VERSION


PROJECT_HASH = "abcdef123456"

# Minimal valid row templates per table (satisfy NOT NULL constraints).
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fact_row(key: str = "build.tool") -> dict:
    now = _now()
    return {"key": key, "value": "uv", "created_at": now, "updated_at": now}


def _adr_row(number: int = 1) -> dict:
    now = _now()
    return {
        "number": number,
        "title": "Use SQLite",
        "status": "accepted",
        "context": "ctx",
        "decision": "dec",
        "created_at": now,
        "updated_at": now,
    }


def _session_row(session_id: str = "s1") -> dict:
    now = _now()
    return {
        "session_id": session_id,
        "summary": "did stuff",
        "started_at": now,
        "ended_at": now,
        "created_at": now,
    }


def _pref_row(key: str = "ui.language") -> dict:
    now = _now()
    return {"key": key, "value": "en", "created_at": now, "updated_at": now}


@pytest.fixture
def tmp_storage(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        yield s, tmp_path
    finally:
        s.close()


# 1. Empty dir initialisation creates the file and the expected tables.
def test_init_creates_file_and_tables(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        assert s.db_path.exists()
        assert s.db_path == tmp_path / f"{PROJECT_HASH}.db"
        # Inspect sqlite_master to confirm all 5 tables exist.
        rows = s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r["name"] for r in rows}
        for expected in {"meta", "facts", "adrs", "session_summaries", "preferences"}:
            assert expected in names
        # Meta row should have been seeded on first creation.
        meta = s.query("meta")
        assert len(meta) == 1
        assert meta[0]["schema_version"] == SCHEMA_VERSION
        assert meta[0]["project_id"] == PROJECT_HASH
    finally:
        s.close()


# 2. CRUD loop through the 4 data tables.
@pytest.mark.parametrize(
    "table,row_factory,filter_key",
    [
        ("facts", _fact_row, "key"),
        ("adrs", _adr_row, "number"),
        ("session_summaries", _session_row, "session_id"),
        ("preferences", _pref_row, "key"),
    ],
)
def test_insert_query_delete_cycle(tmp_storage, table, row_factory, filter_key):
    s, _ = tmp_storage
    row = row_factory()
    rid = s.insert(table, row)
    assert rid > 0

    got = s.query(table, **{filter_key: row[filter_key]})
    assert len(got) == 1
    assert got[0][filter_key] == row[filter_key]

    removed = s.delete(table, **{filter_key: row[filter_key]})
    assert removed == 1
    assert s.query(table, **{filter_key: row[filter_key]}) == []


# 3. WAL journal mode is active.
def test_wal_mode(tmp_storage):
    s, _ = tmp_storage
    mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    sync = s._conn.execute("PRAGMA synchronous").fetchone()[0]
    # NORMAL = 1
    assert int(sync) == 1


# 4. Sidecar meta.json is present and valid JSON after close.
def test_sidecar_meta_json_after_close(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    s.insert("facts", _fact_row())
    s.close()
    sidecar = tmp_path / f"{PROJECT_HASH}.meta.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["project_hash"] == PROJECT_HASH
    assert "closed_at" in data


# 5. Reopening does not clobber existing data.
def test_reopen_preserves_data(tmp_path):
    s1 = Storage(PROJECT_HASH, data_dir=tmp_path)
    s1.insert("facts", _fact_row("k1"))
    s1.insert("facts", _fact_row("k2"))
    s1.close()

    s2 = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        rows = s2.query("facts")
        keys = sorted(r["key"] for r in rows)
        assert keys == ["k1", "k2"]
        # Meta still has exactly one row (not duplicated on reopen).
        assert len(s2.query("meta")) == 1
    finally:
        s2.close()


# 6. Equality-filter basics.
def test_filter_equality(tmp_storage):
    s, _ = tmp_storage
    s.insert("facts", _fact_row("a"))
    s.insert("facts", _fact_row("b"))
    s.insert("facts", _fact_row("c"))

    assert len(s.query("facts")) == 3
    only_b = s.query("facts", key="b")
    assert len(only_b) == 1 and only_b[0]["key"] == "b"
    missing = s.query("facts", key="zzz")
    assert missing == []


# 7. Closed instance rejects operations; close() is idempotent.
def test_close_is_idempotent_and_blocks_ops(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    s.close()
    s.close()  # must not raise
    with pytest.raises(RuntimeError):
        s.insert("facts", _fact_row())


# 8. delete() without filters is refused (safety net).
def test_delete_requires_filter(tmp_storage):
    s, _ = tmp_storage
    with pytest.raises(ValueError):
        s.delete("facts")


# 9. Unknown tables are rejected (no SQL injection vector).
def test_unknown_table_rejected(tmp_storage):
    s, _ = tmp_storage
    with pytest.raises(ValueError):
        s.insert("robots; DROP TABLE facts;--", {"x": 1})


# 10. Unknown column names are rejected on insert/query/delete (P0-1).
def test_insert_unknown_column_rejected(tmp_storage):
    s, _ = tmp_storage
    row = _fact_row()
    row["no_such_column"] = "x"
    with pytest.raises(ValueError, match="unknown column"):
        s.insert("facts", row)


def test_query_unknown_column_rejected(tmp_storage):
    s, _ = tmp_storage
    with pytest.raises(ValueError, match="unknown column"):
        s.query("facts", bogus_filter="x")


def test_delete_unknown_column_rejected(tmp_storage):
    s, _ = tmp_storage
    with pytest.raises(ValueError, match="unknown column"):
        s.delete("facts", bogus_filter="x")


# 11. Allowed-columns introspection matches the DDL.
def test_allowed_columns_matches_ddl(tmp_storage):
    s, _ = tmp_storage
    cols = s.allowed_columns("facts")
    for expected in ("id", "key", "value", "tags", "source",
                     "confidence", "created_at", "updated_at"):
        assert expected in cols


# 12. upsert() on UNIQUE conflict updates instead of duplicating.
def test_upsert_inserts_then_updates(tmp_storage):
    s, _ = tmp_storage
    row1 = _pref_row("k")
    row1["value"] = "v1"
    s.upsert("preferences", row1, conflict_cols=("key",))

    row2 = _pref_row("k")
    row2["value"] = "v2"
    row2["created_at"] = "1970-01-01T00:00:00Z"  # should be preserved
    s.upsert("preferences", row2, conflict_cols=("key",))

    got = s.query("preferences", key="k")
    assert len(got) == 1
    assert got[0]["value"] == "v2"
    # created_at preserved from the first insert.
    assert got[0]["created_at"] == row1["created_at"]


def test_upsert_unknown_column_rejected(tmp_storage):
    s, _ = tmp_storage
    row = _pref_row("k")
    row["bogus"] = "x"
    with pytest.raises(ValueError, match="unknown column"):
        s.upsert("preferences", row, conflict_cols=("key",))

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


# ─────────────────────────────────────────────────────────────────────
# v0.3.0 — schema v3 migration tests
# ─────────────────────────────────────────────────────────────────────
import sqlite3  # noqa: E402

from mindkeep import storage as storage_mod  # noqa: E402
from mindkeep.storage import fts5_available, migrate_to_v3  # noqa: E402


_V3_FACT_COLS = {
    "last_accessed_at",
    "access_count",
    "pin",
    "archived_at",
    "token_estimate",
}
_V3_ADR_COLS = _V3_FACT_COLS  # same set on adrs


def _table_cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_store_reports_schema_v3(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        assert SCHEMA_VERSION == 3
        meta = s.query("meta")[0]
        assert meta["schema_version"] == 3
        # access_tracking_started_at seeded for fresh stores so future
        # gc work can distinguish legacy NULL rows.
        assert meta["access_tracking_started_at"]
        # Every v3 column present on facts and adrs.
        assert _V3_FACT_COLS <= _table_cols(s._conn, "facts")
        assert _V3_ADR_COLS <= _table_cols(s._conn, "adrs")
    finally:
        s.close()


def _build_v2_store(db_path):
    """Hand-craft a pre-v3 store (the v1 schema this project shipped, with
    schema_version=2 to simulate a v2 store) to exercise the migration."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE meta (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version  INTEGER NOT NULL,
            project_id      TEXT    NOT NULL DEFAULT '',
            display_name    TEXT    NOT NULL DEFAULT '',
            id_source       TEXT    NOT NULL DEFAULT '',
            origin_value    TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        );
        CREATE TABLE facts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL,
            tags        TEXT    NOT NULL DEFAULT '',
            source      TEXT    NOT NULL DEFAULT 'agent',
            confidence  REAL    NOT NULL DEFAULT 1.0,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            UNIQUE(key)
        );
        CREATE TABLE adrs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            number       INTEGER NOT NULL,
            title        TEXT    NOT NULL,
            status       TEXT    NOT NULL,
            context      TEXT    NOT NULL,
            decision     TEXT    NOT NULL,
            alternatives TEXT    NOT NULL DEFAULT '',
            consequences TEXT    NOT NULL DEFAULT '',
            supersedes   INTEGER,
            tags         TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL,
            UNIQUE(number),
            FOREIGN KEY (supersedes) REFERENCES adrs(id)
        );
        CREATE TABLE session_summaries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    TEXT    NOT NULL,
            summary       TEXT    NOT NULL,
            files_touched TEXT    NOT NULL DEFAULT '',
            refs          TEXT    NOT NULL DEFAULT '',
            started_at    TEXT    NOT NULL,
            ended_at      TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            UNIQUE(session_id)
        );
        CREATE TABLE preferences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT    NOT NULL UNIQUE,
            value       TEXT    NOT NULL,
            scope       TEXT    NOT NULL DEFAULT 'user',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );
        INSERT INTO meta(id, schema_version, project_id, created_at, updated_at)
            VALUES (1, 2, 'legacy', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        INSERT INTO facts(key, value, created_at, updated_at)
            VALUES ('legacy.k1', 'v1', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        INSERT INTO facts(key, value, created_at, updated_at)
            VALUES ('legacy.k2', 'v2', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        INSERT INTO adrs(number, title, status, context, decision,
                         created_at, updated_at)
            VALUES (1, 'Use SQLite', 'accepted', 'ctx', 'dec',
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()


def test_v2_to_v3_migration_preserves_data_and_adds_columns(tmp_path):
    db_path = tmp_path / f"{PROJECT_HASH}.db"
    _build_v2_store(db_path)
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        meta = s.query("meta")[0]
        assert meta["schema_version"] == 3
        # Pre-existing meta fields preserved.
        assert meta["project_id"] == "legacy"
        # access_tracking_started_at seeded by migration so future gc work
        # can distinguish legacy NULL last_accessed_at rows from stale ones.
        assert meta["access_tracking_started_at"]
        # All facts/adrs preserved.
        assert {r["key"] for r in s.query("facts")} == {"legacy.k1", "legacy.k2"}
        assert len(s.query("adrs")) == 1
        # Defaults applied to legacy rows.
        for fact in s.query("facts"):
            assert fact["last_accessed_at"] is None
            assert fact["access_count"] == 0
            assert fact["pin"] == 0
            assert fact["archived_at"] is None
            assert fact["token_estimate"] is None
        adr = s.query("adrs")[0]
        assert adr["last_accessed_at"] is None
        assert adr["access_count"] == 0
    finally:
        s.close()


def test_migration_is_idempotent_on_v3(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        # Run again — must be a no-op (no exceptions, no duplicate triggers).
        migrate_to_v3(s._conn)
        migrate_to_v3(s._conn)

        meta = s.query("meta")[0]
        assert meta["schema_version"] == 3

        # Triggers exist exactly once.
        if fts5_available():
            trigger_names = {
                r[0]
                for r in s._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            for expected in {
                "facts_ai", "facts_ad", "facts_au",
                "adrs_ai", "adrs_ad", "adrs_au",
            }:
                assert expected in trigger_names

            # Re-running shouldn't re-populate the FTS table either —
            # insert one row, run migrate again, count must stay at 1.
            s.insert("facts", _fact_row("idem.k"))
            count_before = s._conn.execute(
                "SELECT COUNT(*) FROM facts_fts"
            ).fetchone()[0]
            migrate_to_v3(s._conn)
            count_after = s._conn.execute(
                "SELECT COUNT(*) FROM facts_fts"
            ).fetchone()[0]
            assert count_after == count_before
    finally:
        s.close()


@pytest.mark.skipif(not fts5_available(),
                    reason="SQLite build lacks FTS5")
def test_fts5_search_smoke_including_cjk(tmp_path):
    s = Storage(PROJECT_HASH, data_dir=tmp_path)
    try:
        s.insert("facts", {**_fact_row("auth.method"),
                           "value": "use JWT RS256 for tokens"})
        s.insert("facts", {**_fact_row("build.tool"),
                           "value": "uv for dependency management"})
        s.insert("facts", {**_fact_row("auth.cjk"),
                           "value": "auth.method: 使用 JWT RS256"})

        rows = s._conn.execute(
            "SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?",
            ("JWT",),
        ).fetchall()
        assert len(rows) == 2

        rows = s._conn.execute(
            "SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?",
            ("使用",),
        ).fetchall()
        assert len(rows) == 1
        # And the matched row is the CJK one.
        cjk_rowid = rows[0][0]
        match = s._conn.execute(
            "SELECT key FROM facts WHERE rowid = ?", (cjk_rowid,)
        ).fetchone()
        assert match["key"] == "auth.cjk"
    finally:
        s.close()


def test_migration_without_fts5_logs_warning_and_opens(tmp_path,
                                                       monkeypatch, caplog):
    monkeypatch.setattr(storage_mod, "fts5_available", lambda: False)
    with caplog.at_level("WARNING", logger=storage_mod.__name__):
        s = Storage(PROJECT_HASH, data_dir=tmp_path)
        try:
            # Store opens, schema is v3, salience columns present.
            assert s.query("meta")[0]["schema_version"] == 3
            assert _V3_FACT_COLS <= _table_cols(s._conn, "facts")
            # FTS tables/triggers were skipped.
            objs = {
                r[0]
                for r in s._conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type IN ('table', 'trigger')"
                ).fetchall()
            }
            assert "facts_fts" not in objs
            assert "facts_ai" not in objs
        finally:
            s.close()
    assert any("FTS5" in rec.message for rec in caplog.records)


"""Low-level SQLite/WAL storage primitive.

Implements the persistence contract defined in ARCHITECTURE.md §4 and §7:
- SQLite with WAL journaling and NORMAL synchronous mode.
- Single-file per project (path = <data_dir>/<project-hash>.db).
- Thread-safe connection guarded by an RLock.
- Structured insert/query/delete (no raw SQL exposed upward).
- close() performs final commit, wal_checkpoint(TRUNCATE) and atomic
  sidecar meta.json write.

Only stdlib is used. Python >= 3.11.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1

# Whitelisted tables — we never accept table names from callers unchecked.
_ALLOWED_TABLES: frozenset[str] = frozenset(
    {"meta", "facts", "adrs", "session_summaries", "preferences"}
)

# Frozen DDL mirroring ARCHITECTURE.md §4 / §4.1.  A single storage file
# carries all tables; higher-level code decides which tables are actually
# populated for per-project vs. global preference DBs.
_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        id                INTEGER PRIMARY KEY CHECK (id = 1),
        schema_version    INTEGER NOT NULL,
        project_id        TEXT    NOT NULL DEFAULT '',
        display_name      TEXT    NOT NULL DEFAULT '',
        id_source         TEXT    NOT NULL DEFAULT '',
        origin_value      TEXT    NOT NULL DEFAULT '',
        created_at        TEXT    NOT NULL,
        updated_at        TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS facts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        key          TEXT    NOT NULL,
        value        TEXT    NOT NULL,
        tags         TEXT    NOT NULL DEFAULT '',
        source       TEXT    NOT NULL DEFAULT 'agent',
        confidence   REAL    NOT NULL DEFAULT 1.0,
        created_at   TEXT    NOT NULL,
        updated_at   TEXT    NOT NULL,
        UNIQUE(key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_facts_tags    ON facts(tags)",
    "CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS adrs (
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_adrs_status ON adrs(status)",
    "CREATE INDEX IF NOT EXISTS idx_adrs_tags   ON adrs(tags)",
    """
    CREATE TABLE IF NOT EXISTS session_summaries (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id    TEXT    NOT NULL,
        summary       TEXT    NOT NULL,
        files_touched TEXT    NOT NULL DEFAULT '',
        refs          TEXT    NOT NULL DEFAULT '',
        started_at    TEXT    NOT NULL,
        ended_at      TEXT    NOT NULL,
        created_at    TEXT    NOT NULL,
        UNIQUE(session_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sess_ended ON session_summaries(ended_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS preferences (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        key          TEXT    NOT NULL UNIQUE,
        value        TEXT    NOT NULL,
        scope        TEXT    NOT NULL DEFAULT 'user',
        created_at   TEXT    NOT NULL,
        updated_at   TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prefs_updated ON preferences(updated_at DESC)",
)


def default_data_dir() -> Path:
    """Return the per-OS data directory (ARCHITECTURE.md §3.1).

    Respects ``MINDKEEP_HOME`` if set.
    """
    env = os.environ.get("MINDKEEP_HOME")
    if env:
        return Path(env)
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "mindkeep"
        return Path.home() / "AppData" / "Roaming" / "mindkeep"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mindkeep"
    # Linux / other POSIX
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "mindkeep"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            # Some filesystems (e.g. network mounts) don't support fsync.
            pass
    os.replace(tmp, path)


class Storage:
    """Thread-safe SQLite/WAL storage primitive.

    Parameters
    ----------
    project_hash:
        12-char project identifier; used as the DB filename stem.
    data_dir:
        Optional override for the base directory (primarily for tests).
        Defaults to :func:`default_data_dir`.
    """

    def __init__(
        self,
        project_hash: str,
        *,
        data_dir: Path | str | None = None,
    ) -> None:
        if not project_hash:
            raise ValueError("project_hash must be non-empty")
        self._project_hash = project_hash
        self._data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / f"{project_hash}.db"
        self._meta_path = self._data_dir / f"{project_hash}.meta.json"
        self._lock = threading.RLock()
        self._closed = False

        is_new = not self._db_path.exists()
        # check_same_thread=False because we serialise access with our own RLock.
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=5.0,
            isolation_level=None,  # we manage transactions manually via BEGIN/COMMIT
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._conn.execute("BEGIN")
        try:
            for stmt in _DDL:
                self._conn.execute(stmt)
            if is_new:
                now = _now_iso()
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta "
                    "(id, schema_version, project_id, created_at, updated_at) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (SCHEMA_VERSION, project_hash, now, now),
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        # Cache per-table allowed columns (P0-1 — column-name whitelist).
        # Populated from PRAGMA table_info; used to reject caller-supplied
        # column names that aren't part of the schema.
        self._allowed_columns: dict[str, frozenset[str]] = {}
        for tbl in _ALLOWED_TABLES:
            rows = self._conn.execute(
                f"PRAGMA table_info({tbl})"
            ).fetchall()
            self._allowed_columns[tbl] = frozenset(r["name"] for r in rows)

    # ───────────────────────── internals ─────────────────────────
    def _apply_pragmas(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA wal_autocheckpoint=1000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.close()

    @staticmethod
    def _check_table(table: str) -> None:
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"unknown table {table!r}; allowed: {sorted(_ALLOWED_TABLES)}"
            )

    def _check_columns(self, table: str, cols: Iterable[str]) -> None:
        """Reject column names that aren't in the cached schema for ``table``.

        Defence-in-depth against string-interpolated SQL in
        :meth:`insert` / :meth:`query` / :meth:`delete` / :meth:`upsert`.
        """
        allowed = self._allowed_columns.get(table)
        if not allowed:
            # Fall back to a fresh PRAGMA read (should never happen).
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            allowed = frozenset(r["name"] for r in rows)
            self._allowed_columns[table] = allowed
        for col in cols:
            if col not in allowed:
                raise ValueError(
                    f"unknown column {col!r} for table {table!r}; "
                    f"allowed: {sorted(allowed)}"
                )

    def allowed_columns(self, table: str) -> frozenset[str]:
        """Return the frozenset of legal column names for ``table``."""
        self._check_table(table)
        return self._allowed_columns.get(table, frozenset())

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Storage is closed")

    # ───────────────────────── public API ─────────────────────────
    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def meta_path(self) -> Path:
        return self._meta_path

    def insert(self, table: str, row: dict[str, Any]) -> int:
        """Insert a row; returns the newly assigned rowid."""
        self._check_table(table)
        if not row:
            raise ValueError("row must be a non-empty mapping")
        cols = list(row.keys())
        self._check_columns(table, cols)
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        with self._lock:
            self._ensure_open()
            cur = self._conn.execute(sql, tuple(row[c] for c in cols))
            rowid = cur.lastrowid
            self._conn.commit()
            return int(rowid) if rowid is not None else 0

    def upsert(
        self,
        table: str,
        row: dict[str, Any],
        conflict_cols: Sequence[str],
    ) -> int:
        """Atomic INSERT ... ON CONFLICT(...) DO UPDATE.

        ``conflict_cols`` must be a column-set with a UNIQUE (or PK)
        constraint.  Non-conflict columns are overwritten with ``row``'s
        values on conflict.  Returns the rowid of the affected row.

        Caller is expected to supply ``updated_at`` when relevant;
        ``created_at`` is preserved on conflict because it's not part of
        the DO UPDATE set (excluded = new row, but we only copy non-key
        columns explicitly — and we deliberately leave ``created_at`` out
        of the update list).
        """
        self._check_table(table)
        if not row:
            raise ValueError("row must be a non-empty mapping")
        if not conflict_cols:
            raise ValueError("conflict_cols must be non-empty")
        cols = list(row.keys())
        self._check_columns(table, cols)
        self._check_columns(table, conflict_cols)
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        conflict_list = ",".join(conflict_cols)
        # Everything that isn't a conflict column *or* ``created_at`` is
        # refreshed from the excluded (new) row.
        preserve = set(conflict_cols) | {"created_at"}
        update_cols = [c for c in cols if c not in preserve]
        if update_cols:
            update_clause = ",".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_list}) DO UPDATE SET {update_clause}"
            )
        else:
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_list}) DO NOTHING"
            )
        with self._lock:
            self._ensure_open()
            cur = self._conn.execute(sql, tuple(row[c] for c in cols))
            # On UPDATE, lastrowid may be 0; fall back to a lookup.
            rowid = cur.lastrowid or 0
            if not rowid:
                key_where = " AND ".join(f"{c} = ?" for c in conflict_cols)
                found = self._conn.execute(
                    f"SELECT id FROM {table} WHERE {key_where}",
                    tuple(row[c] for c in conflict_cols),
                ).fetchone()
                if found is not None:
                    rowid = int(found["id"])
            self._conn.commit()
            return int(rowid)

    def query(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        """Equality-match query; returns list of row dicts."""
        self._check_table(table)
        if filters:
            self._check_columns(table, filters.keys())
        with self._lock:
            self._ensure_open()
            if filters:
                where = " AND ".join(f"{k} = ?" for k in filters)
                sql = f"SELECT * FROM {table} WHERE {where}"
                params: Iterable[Any] = tuple(filters.values())
            else:
                sql = f"SELECT * FROM {table}"
                params = ()
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def delete(self, table: str, **filters: Any) -> int:
        """Equality-match delete; returns number of rows removed."""
        self._check_table(table)
        if not filters:
            raise ValueError(
                "delete() requires at least one filter; "
                "refusing to wipe a whole table"
            )
        self._check_columns(table, filters.keys())
        where = " AND ".join(f"{k} = ?" for k in filters)
        sql = f"DELETE FROM {table} WHERE {where}"
        with self._lock:
            self._ensure_open()
            cur = self._conn.execute(sql, tuple(filters.values()))
            self._conn.commit()
            return cur.rowcount

    def set_project_meta(
        self,
        *,
        display_name: str | None = None,
        id_source: str | None = None,
        origin_value: str | None = None,
    ) -> None:
        """Update the singleton ``meta`` row with project identity fields.

        Only non-``None`` fields are written. Empty strings are also skipped
        (we never overwrite a stored display_name with a blank). ``updated_at``
        is refreshed when any column changes.
        """
        updates: dict[str, Any] = {}
        if display_name:
            updates["display_name"] = display_name
        if id_source:
            updates["id_source"] = id_source
        if origin_value:
            updates["origin_value"] = origin_value
        if not updates:
            return
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        sql = f"UPDATE meta SET {set_clause} WHERE id = 1"
        with self._lock:
            self._ensure_open()
            self._conn.execute(sql, tuple(updates.values()))
            self._conn.commit()

    def commit(self) -> None:
        with self._lock:
            self._ensure_open()
            self._conn.commit()

    def checkpoint_truncate(self) -> None:
        with self._lock:
            self._ensure_open()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ───────────────────────── shutdown ─────────────────────────
    def _write_sidecar(self) -> None:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "project_hash": self._project_hash,
            "closed_at": _now_iso(),
        }
        try:
            rows = self._conn.execute(
                "SELECT schema_version, project_id, display_name, id_source, "
                "origin_value, created_at, updated_at FROM meta WHERE id = 1"
            ).fetchall()
            if rows:
                meta = dict(rows[0])
                payload.update(meta)
        except sqlite3.Error:
            # If meta can't be read we still emit the minimal sidecar.
            pass
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        _atomic_write_bytes(self._meta_path, data)

    def close(self) -> None:
        """Idempotent shutdown: commit → checkpoint(TRUNCATE) → sidecar → close."""
        with self._lock:
            if self._closed:
                return
            try:
                try:
                    self._conn.commit()
                except sqlite3.Error:
                    pass
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                try:
                    self._write_sidecar()
                except OSError:
                    # Sidecar is best-effort; never let it block close().
                    pass
            finally:
                try:
                    self._conn.close()
                finally:
                    self._closed = True

    # Context manager sugar — convenient for tests.
    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

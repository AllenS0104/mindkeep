"""Pure data helpers behind ``mindkeep stats`` and ``mindkeep doctor``.

Both helpers return plain dicts and never write to stdout. This is the
hard requirement that lets the same logic feed two callers:

* :mod:`mindkeep.cli` formats them for the terminal (human or ``--json``).
* :mod:`mindkeep.mcp` returns them directly as MCP tool results.

The CLI commands previously interleaved data collection with
``print()`` calls. Reusing them from an MCP tool handler over stdio
would corrupt the JSON-RPC frame channel (DESIGN-v0.4.0 §3.4); the
helpers here are the stdout-clean producers and the printers in
``cli`` are the consumers.

Notes:

* ``collect_stats`` deliberately omits the CLI-only ``session_budget``
  block (DESIGN §11: session budget is a CLI rendering concern; an MCP
  caller has its own context-window accounting).
* ``collect_doctor`` accepts ``verbose``; when ``False`` per-check
  ``details`` blocks are stripped so the MCP tool result stays compact
  (DESIGN §13).
"""

from __future__ import annotations

import importlib
import importlib.metadata as _md
import shutil
import sqlite3
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

from .memory_api import MemoryStore
from .models import ProjectId
from .storage import SCHEMA_VERSION, Storage


__all__ = ["collect_stats", "collect_doctor"]


def collect_stats(
    store: MemoryStore,
    *,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Return per-project store stats as a plain dict (no I/O to stdout).

    Mirrors the JSON shape ``mindkeep stats --json`` emits, minus the
    ``session_budget`` block which is a CLI rendering concern (see
    DESIGN-v0.4.0 §11).
    """
    s = store._storage
    data = s.stats()
    if data_dir is None:
        try:
            data_dir = s.db_path.parent
        except Exception:  # pragma: no cover - defensive
            data_dir = None
    if data_dir is not None:
        data["data_dir"] = str(data_dir)
    ps = store._pref_storage
    if ps is not None:
        data["preferences"] = {"total": len(ps.query("preferences"))}
    else:
        data["preferences"] = {"total": 0}
    return data


# Check ids that belong to the "Environment" section in human output.
# Anything else collected by :func:`collect_doctor` is rendered under
# "Store health". Order is preserved by collect_doctor's traversal.
_ENV_CHECK_IDS: frozenset[str] = frozenset({
    "python-version",
    "package-installed",
    "cli-on-path",
    "data-dir-writable",
    "sqlite-wal-supported",
    "filters-loaded",
    "current-project",
    "known-projects",
})


def collect_doctor(
    data_dir: Path,
    project_id: ProjectId | None,
    *,
    verbose: bool = True,
    schema_version: int | None = None,
) -> dict[str, Any]:
    """Run all doctor checks and return a JSON-shaped dict.

    Parameters
    ----------
    data_dir:
        The mindkeep data directory under which per-project DB files
        live. Same path the CLI uses.
    project_id:
        Resolved project id. ``None`` short-circuits the per-project
        store-health checks (matches CLI behaviour when project
        resolution fails).
    verbose:
        When ``False``, per-check ``details`` blocks are removed from
        the returned dict to keep MCP tool results small (DESIGN §13).
    schema_version:
        Override the on-disk schema-version expectation. Defaults to
        the binary's ``SCHEMA_VERSION``. Tests use this to simulate a
        DB written by a newer mindkeep build.

    Returns
    -------
    ``{"version": 1, "checks": [...], "summary": {"ok", "warn", "fail"}}``
    """
    expected_schema = (
        SCHEMA_VERSION if schema_version is None else int(schema_version)
    )

    checks: list[dict[str, Any]] = []

    def _emit(check_id: str, status: str, msg: str,
              details: dict[str, Any] | None = None) -> None:
        entry: dict[str, Any] = {
            "id": check_id,
            "status": status,
            "message": msg,
        }
        if details:
            entry["details"] = details
        checks.append(entry)

    def ok(cid: str, msg: str, details: dict[str, Any] | None = None) -> None:
        _emit(cid, "OK", msg, details)

    def warn(cid: str, msg: str, details: dict[str, Any] | None = None) -> None:
        _emit(cid, "WARN", msg, details)

    def bad(cid: str, msg: str, details: dict[str, Any] | None = None) -> None:
        _emit(cid, "FAIL", msg, details)

    # ── Environment ──
    v = sys.version_info
    py = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 9):
        ok("python-version", f"Python version: {py} (>= 3.9)",
           {"version": py})
    else:
        bad("python-version", f"Python version: {py} (need >= 3.9)",
            {"version": py})

    try:
        pkg_version = _md.version("mindkeep")
        ok("package-installed", f"mindkeep installed: {pkg_version}",
           {"version": pkg_version})
    except _md.PackageNotFoundError:
        bad("package-installed",
            "mindkeep not installed (importlib.metadata lookup failed)")

    exe = shutil.which("mindkeep")
    if exe:
        ok("cli-on-path", f"CLI on PATH: {exe}", {"path": exe})
    else:
        try:
            scripts_dir = sysconfig.get_path(
                "scripts", "nt_user" if sys.platform == "win32" else "posix_user"
            )
        except KeyError:
            scripts_dir = sysconfig.get_path("scripts")
        if sys.platform == "win32":
            hint = f'$env:Path += ";{scripts_dir}"'
        else:
            hint = 'export PATH="$HOME/.local/bin:$PATH"'
        warn("cli-on-path",
             f"'mindkeep' not in PATH; add scripts dir: {hint}",
             {"scripts_dir": scripts_dir})

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".health"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok("data-dir-writable", f"Data dir writable: {data_dir}",
           {"data_dir": str(data_dir)})
    except (OSError, PermissionError) as exc:
        bad("data-dir-writable",
            f"Data dir not writable ({data_dir}): {exc}",
            {"data_dir": str(data_dir), "error": str(exc)})

    try:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "probe.db"
            conn = sqlite3.connect(str(db_path))
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            finally:
                conn.close()
        if str(mode).lower() == "wal":
            ok("sqlite-wal-supported", "SQLite WAL mode supported",
               {"journal_mode": str(mode)})
        else:
            warn("sqlite-wal-supported",
                 f"SQLite journal_mode returned '{mode}' (expected 'wal')",
                 {"journal_mode": str(mode)})
    except sqlite3.Error as exc:
        bad("sqlite-wal-supported", f"SQLite WAL probe failed: {exc}",
            {"error": str(exc)})

    try:
        mod = importlib.import_module("mindkeep.security")
        redactor_cls = getattr(mod, "SecretsRedactor")
        redactor_cls()
        ok("filters-loaded", "Filters loaded: SecretsRedactor OK")
    except Exception as exc:
        bad("filters-loaded", f"SecretsRedactor failed to load: {exc}",
            {"error": str(exc)})

    if project_id is not None:
        ok("current-project",
           f"Current project: id={project_id.id} source={project_id.source} "
           f"display_name={project_id.display_name}",
           {"id": project_id.id, "source": project_id.source,
            "display_name": project_id.display_name})
    else:
        bad("current-project", "resolve_project_id() failed: no project id")

    try:
        if data_dir.exists():
            dbs = [
                p for p in data_dir.glob("*.db")
                if p.name != "preferences.db"
            ]
            ok("known-projects",
               f"Known projects: {len(dbs)} DB file(s) in {data_dir}",
               {"count": len(dbs), "data_dir": str(data_dir)})
        else:
            warn("known-projects",
                 f"Data dir does not exist yet: {data_dir}",
                 {"data_dir": str(data_dir)})
    except OSError as exc:
        warn("known-projects",
             f"Could not enumerate project DBs: {exc}",
             {"error": str(exc)})

    # ── Store health ──
    _run_store_checks(data_dir, project_id, expected_schema, ok, warn, bad)

    summary = {
        "ok": sum(1 for c in checks if c["status"] == "OK"),
        "warn": sum(1 for c in checks if c["status"] == "WARN"),
        "fail": sum(1 for c in checks if c["status"] == "FAIL"),
    }

    if not verbose:
        for c in checks:
            c.pop("details", None)

    return {"version": 1, "checks": checks, "summary": summary}


def _run_store_checks(
    data_dir: Path,
    pid: ProjectId | None,
    expected_schema: int,
    ok,  # type: ignore[no-untyped-def]
    warn,  # type: ignore[no-untyped-def]
    bad,  # type: ignore[no-untyped-def]
) -> None:
    """Per-project DB health checks (P1-9). Gracefully skips if no DB."""
    if pid is None:
        warn("store-database",
             "no project DB checks (project id unavailable)")
        return

    db_path = data_dir / f"{pid.id}.db"
    if not db_path.exists():
        warn("store-database",
             f"no database initialised yet at {db_path} — "
             f"add a fact to create one",
             {"db_path": str(db_path)})
        return

    from .memory_api import _resolve_cap

    try:
        s = Storage(pid.id, data_dir=data_dir)
    except Exception as exc:
        bad("store-database",
            f"failed to open project DB ({db_path}): {exc}",
            {"db_path": str(db_path), "error": str(exc)})
        return

    try:
        # Check 1 — schema version up-to-date
        try:
            row = s._conn.execute(
                "SELECT schema_version FROM meta WHERE id = 1"
            ).fetchone()
            db_schema = int(row["schema_version"]) if row else None
        except sqlite3.Error as exc:
            bad("schema-version",
                f"could not read meta.schema_version: {exc}",
                {"error": str(exc)})
            db_schema = None
        if db_schema is not None:
            details = {"db": db_schema, "expected": expected_schema}
            if db_schema == expected_schema:
                ok("schema-version",
                   f"Schema version up-to-date: {db_schema}", details)
            elif db_schema < expected_schema:
                warn("schema-version",
                     f"Schema version {db_schema} older than expected "
                     f"{expected_schema}; run an operation to migrate",
                     details)
            else:
                bad("schema-version",
                    f"Schema version {db_schema} NEWER than this "
                    f"binary supports ({expected_schema}); upgrade mindkeep",
                    details)

        # Check 2 — WAL mode active on this DB
        try:
            mode_row = s._conn.execute("PRAGMA journal_mode").fetchone()
            mode = str(mode_row[0]).lower() if mode_row else ""
        except sqlite3.Error as exc:
            bad("wal-mode-active",
                f"PRAGMA journal_mode failed: {exc}", {"error": str(exc)})
            mode = ""
        if mode == "wal":
            ok("wal-mode-active", "WAL mode active on project DB",
               {"journal_mode": mode})
        elif mode:
            warn("wal-mode-active",
                 f"journal_mode is '{mode}', expected 'wal' "
                 f"(concurrency degraded)",
                 {"journal_mode": mode})

        # Check 3 — FTS5 integrity
        try:
            s._conn.execute(
                "INSERT INTO facts_fts(facts_fts) VALUES('integrity-check')"
            )
            ok("fts5-integrity", "FTS5 integrity check passed")
        except sqlite3.Error as exc:
            bad("fts5-integrity", f"FTS5 integrity check failed: {exc}",
                {"error": str(exc)})

        # Check 4 — Storage stats summary
        stats_data: dict[str, Any] | None
        try:
            stats_data = s.stats()
            facts_total = stats_data["facts"]["total"]
            adrs_total = stats_data["adrs"]["total"]
            tokens_total = stats_data["tokens_estimated_total"]
            facts_pinned = stats_data["facts"]["pinned"]
            adrs_pinned = stats_data["adrs"]["pinned"]
            ok("store-stats",
               f"Store stats: {facts_total} fact(s), {adrs_total} adr(s), "
               f"~{tokens_total} tokens, "
               f"pinned facts={facts_pinned} adrs={adrs_pinned}",
               {
                   "facts_total": facts_total,
                   "adrs_total": adrs_total,
                   "tokens_estimated_total": tokens_total,
                   "facts_pinned": facts_pinned,
                   "adrs_pinned": adrs_pinned,
                   "db_size_bytes": stats_data["db_size_bytes"],
               })
        except sqlite3.Error as exc:
            bad("store-stats", f"stats() failed: {exc}", {"error": str(exc)})
            stats_data = None

        # Check 5 — Token-cap pressure
        try:
            fact_cap = _resolve_cap("fact")
            adr_cap = _resolve_cap("adr")
            fact_thresh = int(0.8 * fact_cap)
            adr_thresh = int(0.8 * adr_cap)
            f_near = s._conn.execute(
                "SELECT COUNT(*) AS n FROM facts "
                "WHERE token_estimate IS NOT NULL AND token_estimate > ?",
                (fact_thresh,),
            ).fetchone()["n"]
            a_near = s._conn.execute(
                "SELECT COUNT(*) AS n FROM adrs "
                "WHERE token_estimate IS NOT NULL AND token_estimate > ?",
                (adr_thresh,),
            ).fetchone()["n"]
            details = {
                "fact_cap": fact_cap,
                "adr_cap": adr_cap,
                "facts_near_cap": int(f_near),
                "adrs_near_cap": int(a_near),
                "threshold_pct": 80,
            }
            if f_near or a_near:
                warn("token-cap-pressure",
                     f"{f_near} fact(s) and {a_near} adr(s) above 80% of "
                     f"their token caps ({fact_cap}/{adr_cap}); review "
                     f"or archive",
                     details)
            else:
                ok("token-cap-pressure",
                   f"No entries above 80% of token caps "
                   f"(facts cap={fact_cap}, adrs cap={adr_cap})",
                   details)
        except sqlite3.Error as exc:
            bad("token-cap-pressure",
                f"cap pressure query failed: {exc}", {"error": str(exc)})

        # Check 6 — Stale entries (informational)
        try:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=90)).isoformat()
            stale_facts = s._conn.execute(
                "SELECT COUNT(*) AS n FROM facts "
                "WHERE last_accessed_at IS NULL OR last_accessed_at < ?",
                (cutoff,),
            ).fetchone()["n"]
            stale_adrs = s._conn.execute(
                "SELECT COUNT(*) AS n FROM adrs "
                "WHERE last_accessed_at IS NULL OR last_accessed_at < ?",
                (cutoff,),
            ).fetchone()["n"]
            ok("stale-entries",
               f"Stale entries (>90d or never accessed): "
               f"{stale_facts} fact(s), {stale_adrs} adr(s)",
               {
                   "stale_facts": int(stale_facts),
                   "stale_adrs": int(stale_adrs),
                   "days": 90,
               })
        except sqlite3.Error as exc:
            bad("stale-entries", f"stale query failed: {exc}",
                {"error": str(exc)})

        # Check 7 — DB file size & VACUUM hint
        try:
            size = db_path.stat().st_size
            free_pages = int(s._conn.execute(
                "PRAGMA freelist_count"
            ).fetchone()[0])
            page_count = int(s._conn.execute(
                "PRAGMA page_count"
            ).fetchone()[0])
            free_ratio = (free_pages / page_count) if page_count else 0.0
            details = {
                "db_size_bytes": size,
                "free_pages": free_pages,
                "page_count": page_count,
                "free_ratio": round(free_ratio, 4),
            }
            if size > 50 * 1024 * 1024 and free_ratio > 0.30:
                warn("db-size-vacuum",
                     f"DB is {size} bytes with "
                     f"{free_pages}/{page_count} free pages "
                     f"({free_ratio:.0%}); consider VACUUM",
                     details)
            else:
                ok("db-size-vacuum",
                   f"DB size {size} bytes "
                   f"({free_pages}/{page_count} free pages)",
                   details)
        except (OSError, sqlite3.Error) as exc:
            bad("db-size-vacuum", f"size probe failed: {exc}",
                {"error": str(exc)})

        # Check 8 — Pin sanity
        if stats_data is not None:
            ok("pin-sanity",
               f"Pinned: {stats_data['facts']['pinned']} fact(s), "
               f"{stats_data['adrs']['pinned']} adr(s)",
               {
                   "facts_pinned": stats_data["facts"]["pinned"],
                   "adrs_pinned": stats_data["adrs"]["pinned"],
               })
    finally:
        s.close()


def env_check_ids() -> frozenset[str]:
    """Public read-only view of the env-section check ids.

    Used by the CLI's human-format renderer to know which checks to
    print under "Environment" vs "Store health".
    """
    return _ENV_CHECK_IDS

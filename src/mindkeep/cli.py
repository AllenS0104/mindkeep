"""Command-line interface for ``mindkeep``.

Stdlib-only (argparse). Plain-text table output. Entry point declared in
pyproject.toml as ``mindkeep = "mindkeep.cli:main"``.

Subcommands:
    list     Show a one-row summary for every project DB in ``data_dir``.
    show     Print facts / adrs / preferences / sessions for a project.
    clear    Delete rows (with confirmation unless ``--yes``).
    export   Dump a project's tables to a JSON file.
    import   Load a JSON dump back into a project DB (merge or replace).
    where    Print ``data_dir`` and the project id for the current cwd.
    doctor   Run environment health checks.
    upgrade  Pull the latest mindkeep release (pip/pipx auto-detected).

All errors go to stderr with a non-zero exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from . import _session
from ._tokens import estimate as _estimate_tokens
from .memory_api import MemoryStore
from .models import ProjectId
from .project_id import resolve_project_id
from .storage import SCHEMA_VERSION, Storage, default_data_dir

__all__ = ["main"]

try:
    from importlib.metadata import version as _pkg_version
    VERSION = _pkg_version("mindkeep")
except Exception:
    VERSION = "0.0.0+unknown"
_MAX_CELL = 60

# ───────────────────────── upgrade defaults ─────────────────────────
_DEFAULT_UPGRADE_SOURCE = "git+https://github.com/AllenS0104/mindkeep.git"
_UPGRADE_SOURCE_ENV = "mindkeep_UPGRADE_SOURCE"
_PKG_NAME = "mindkeep"

# kind -> table name (user-facing "sessions" vs internal "session_summaries")
_KIND_TABLE: dict[str, str] = {
    "facts": "facts",
    "adrs": "adrs",
    "preferences": "preferences",
    "sessions": "session_summaries",
}
_ALL_KINDS: tuple[str, ...] = ("facts", "adrs", "preferences", "sessions")


class _ProjectNotFound(Exception):
    """Raised when ``--project <hash|name>`` cannot be resolved."""


# ───────────────────────── formatting helpers ─────────────────────────


def _trunc(value: Any, width: int = _MAX_CELL) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if len(s) > width:
        if width <= 3:
            return s[:width]
        return s[: width - 3] + "..."
    return s


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    cols = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(cols):
            cell = row[i] if i < len(row) else ""
            if len(cell) > widths[i]:
                widths[i] = len(cell)
    lines = []
    lines.append(" | ".join(headers[i].ljust(widths[i]) for i in range(cols)))
    lines.append("-+-".join("-" * widths[i] for i in range(cols)))
    for row in rows:
        cells = [(row[i] if i < len(row) else "") for i in range(cols)]
        lines.append(" | ".join(cells[i].ljust(widths[i]) for i in range(cols)))
    return "\n".join(lines)


def _fmt_size(n: int) -> str:
    """Format a byte count for human reading.

    <1 KB → ``<N>B``; otherwise the largest unit where value < 1024, one
    decimal place (``KB`` / ``MB`` / ``GB``).
    """
    if n < 1024:
        return f"{n}B"
    value = float(n)
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        if value < 1024.0 or unit == "GB":
            return f"{value:.1f}{unit}"
    return f"{n}B"  # unreachable


# Sidecar stem reserved for the global preferences DB.
_PREFS_STEM = "preferences"


# ───────────────────────── project resolution ─────────────────────────


def _iter_metas(data_dir: Path) -> list[dict[str, Any]]:
    if not data_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(data_dir.glob("*.meta.json")):
        # Skip the global preferences DB — it's shared across projects
        # and must not appear in the per-project project listing.
        if p.name == f"{_PREFS_STEM}.meta.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            # Defence-in-depth — also drop sidecars whose stored hash
            # happens to be the reserved name.
            ph = data.get("project_hash") or data.get("project_id")
            if ph == _PREFS_STEM:
                continue
            out.append(data)
    return out


def _resolve_project_hash(data_dir: Path, project: str | None) -> str:
    """Resolve ``--project`` argument to a 12-char project hash.

    ``None`` → hash of current cwd.
    An existing ``<project>.db`` under ``data_dir`` → treated as hash.
    Otherwise match by ``display_name`` in any sidecar meta.
    """
    if project is None:
        return resolve_project_id().id
    if (data_dir / f"{project}.db").exists():
        return project
    for meta in _iter_metas(data_dir):
        names = {
            meta.get("project_hash"),
            meta.get("project_id"),
            meta.get("display_name"),
        }
        if project in names:
            ph = meta.get("project_hash") or meta.get("project_id")
            if ph:
                return ph
    raise _ProjectNotFound(f"project not found: {project}")


def _open_storage(data_dir: Path, project_hash: str) -> Storage:
    return Storage(project_hash, data_dir=data_dir)


def _open_pref_storage(data_dir: Path) -> Storage:
    """Open the global cross-project preferences DB."""
    return Storage(_PREFS_STEM, data_dir=data_dir)


def _counts(storage: Storage, pref_storage: Storage) -> dict[str, int]:
    return {
        "facts": len(storage.query("facts")),
        "adrs": len(storage.query("adrs")),
        "preferences": len(pref_storage.query("preferences")),
        "sessions": len(storage.query("session_summaries")),
    }


def _meta_row(storage: Storage) -> dict[str, Any]:
    try:
        rows = storage.query("meta")
        return rows[0] if rows else {}
    except sqlite3.Error:
        return {}


# ───────────────────────── command: list ─────────────────────────


def _cmd_list(data_dir: Path) -> int:
    metas = _iter_metas(data_dir)
    if not metas:
        print("No projects yet. Agents will populate memory as they work.")
        return 0

    headers = [
        "project_hash",
        "display_name",
        "facts_count",
        "adrs_count",
        "prefs_count",
        "last_sessions",
        "db_size",
        "updated_at",
    ]
    rows: list[list[str]] = []
    for m in metas:
        ph = m.get("project_hash") or m.get("project_id") or ""
        dn = m.get("display_name") or ""
        updated = m.get("updated_at") or m.get("closed_at") or ""
        db_path = data_dir / f"{ph}.db"
        size = db_path.stat().st_size if db_path.exists() else 0
        counts = {"facts": 0, "adrs": 0, "preferences": 0, "sessions": 0}
        if db_path.exists() and ph:
            try:
                s = _open_storage(data_dir, ph)
                ps = _open_pref_storage(data_dir)
                try:
                    counts = _counts(s, ps)
                    meta_row = _meta_row(s)
                    if meta_row.get("updated_at"):
                        updated = meta_row["updated_at"]
                    # Sidecars written by older MemoryStore versions may
                    # be missing display_name even though the DB's meta
                    # table has it (newer MemoryStore stamps it on open).
                    # Prefer the DB value whenever the sidecar is blank.
                    if not dn and meta_row.get("display_name"):
                        dn = meta_row["display_name"]
                finally:
                    s.close()
                    ps.close()
            except sqlite3.Error:
                pass
        rows.append([
            _trunc(ph),
            _trunc(dn),
            str(counts["facts"]),
            str(counts["adrs"]),
            str(counts["preferences"]),
            str(counts["sessions"]),
            _fmt_size(size),
            _trunc(updated),
        ])
    print(_render_table(headers, rows))
    return 0


# ───────────────────────── command: show ─────────────────────────


def _tags_list(raw: str) -> list[str]:
    return [t for t in (raw or "").split(",") if t]


class _BudgetTracker:
    """Track remaining token budget across rendered rows for ``mindkeep show``.

    ``remaining is None`` means unlimited (no ``--budget`` flag was passed).
    """

    __slots__ = ("remaining",)

    def __init__(self, total: int | None) -> None:
        self.remaining: int | None = total

    def try_spend(self, text: str) -> bool:
        """Deduct tokens for *text* if it fits. Return True if accepted."""
        if self.remaining is None:
            return True
        cost = _estimate_tokens(text)
        if cost > self.remaining:
            return False
        self.remaining -= cost
        return True


def _show_kind(
    storage: Storage, kind: str, tag: str | None, limit: int,
    pref_storage: Storage | None = None,
    full: bool = False,
    pinned_only: bool = False,
    top: int | None = None,
    budget: _BudgetTracker | None = None,
) -> None:
    # In ``--full`` mode, large free-text columns render raw (newlines
    # preserved, no width cap). Alignment is sacrificed; see PRD-ux-polish.
    def _cell(value: Any) -> str:
        if full:
            return "" if value is None else str(value)
        return _trunc(value)

    table = _KIND_TABLE[kind]
    if kind == "preferences" and pref_storage is not None:
        rows = pref_storage.query(table)
    else:
        rows = storage.query(table)

    if pinned_only and kind in ("facts", "adrs"):
        rows = [r for r in rows if int(r.get("pin") or 0) == 1]

    if kind == "facts":
        rows.sort(
            key=lambda r: (
                int(r.get("pin") or 0),
                r.get("created_at", ""),
                int(r.get("id") or 0),
            ),
            reverse=True,
        )
        if tag:
            rows = [r for r in rows if tag in _tags_list(r.get("tags", ""))]
        rows = rows[: max(0, limit)]
        headers = ["id", "pin", "key", "value", "tags", "updated_at"]
        data = [
            [
                str(r["id"]),
                "*" if int(r.get("pin") or 0) == 1 else "",
                _trunc(r.get("key", "")),
                _cell(r.get("value", "")),
                _trunc(r.get("tags", "")),
                _trunc(r.get("updated_at", "")),
            ]
            for r in rows
        ]
    elif kind == "adrs":
        rows.sort(
            key=lambda r: (
                int(r.get("pin") or 0),
                r.get("created_at", ""),
                int(r.get("id") or 0),
            ),
            reverse=True,
        )
        if tag:
            rows = [r for r in rows if tag in _tags_list(r.get("tags", ""))]
        rows = rows[: max(0, limit)]
        headers = ["number", "pin", "title", "status", "decision", "tags", "updated_at"]
        data = [
            [
                str(r.get("number", "")),
                "*" if int(r.get("pin") or 0) == 1 else "",
                _trunc(r.get("title", "")),
                _trunc(r.get("status", ""), 16),
                _cell(r.get("decision", "")),
                _trunc(r.get("tags", "")),
                _trunc(r.get("updated_at", "")),
            ]
            for r in rows
        ]
    elif kind == "preferences":
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        # No tag column in preferences schema; tag filter is a no-op.
        rows = rows[: max(0, limit)]
        headers = ["key", "value", "scope", "updated_at"]
        data = [
            [
                _trunc(r.get("key", "")),
                _cell(r.get("value", "")),
                _trunc(r.get("scope", ""), 16),
                _trunc(r.get("updated_at", "")),
            ]
            for r in rows
        ]
    else:  # sessions
        rows.sort(key=lambda r: r.get("ended_at", ""), reverse=True)
        rows = rows[: max(0, limit)]
        headers = ["session_id", "summary", "started_at", "ended_at"]
        data = [
            [
                _trunc(r.get("session_id", "")),
                _cell(r.get("summary", "")),
                _trunc(r.get("started_at", "")),
                _trunc(r.get("ended_at", "")),
            ]
            for r in rows
        ]

    if top is not None:
        data = data[: max(0, top)]

    print(f"== {kind} ==")
    if not data:
        print("(no rows)")
    else:
        rendered = _render_table(headers, data).split("\n")
        header_line, sep_line, *row_lines = rendered
        kept: list[str] = []
        for line in row_lines:
            if budget is not None:
                if not budget.try_spend(line):
                    break
            kept.append(line)
        if not kept:
            print("(no rows)")
        else:
            print("\n".join([header_line, sep_line, *kept]))
    print()


def _cmd_show(data_dir: Path, args: argparse.Namespace) -> int:
    import io
    ph = _resolve_project_hash(data_dir, args.project)
    kinds = _ALL_KINDS if args.kind == "all" else (args.kind,)
    full = bool(getattr(args, "full", False) or getattr(args, "no_truncate", False))
    s = _open_storage(data_dir, ph)
    ps = _open_pref_storage(data_dir)
    buf = io.StringIO()
    real_stdout = sys.stdout
    top = getattr(args, "top", None)
    budget_total = getattr(args, "budget", None)
    tracker = _BudgetTracker(budget_total) if budget_total is not None else None
    try:
        sys.stdout = buf
        print(f"project: {ph}")
        for kind in kinds:
            _show_kind(s, kind, args.tag, args.limit,
                       pref_storage=ps, full=full,
                       pinned_only=bool(getattr(args, "pinned", False)),
                       top=top, budget=tracker)
    finally:
        sys.stdout = real_stdout
        s.close()
        ps.close()
    rendered = buf.getvalue()
    _session.emit_or_suppress(rendered.rstrip("\n"))
    return 0


def _cmd_session(args: argparse.Namespace) -> int:
    sub = getattr(args, "session_cmd", None)
    if sub == "reset":
        removed = _session.reset()
        path = _session.state_path()
        if removed:
            print(f"reset session state: {path}")
        else:
            print(f"no session state to reset (looked at {path})")
        return 0
    # default: status
    st = _session.status()
    print(f"path:       {st['path']}")
    print(f"budget:     {st['budget']}  (env MINDKEEP_SESSION_BUDGET)")
    print(f"spent:      {st['spent']}")
    print(f"calls:      {st['calls']}")
    print(f"started_at: {st['started_at']}")
    print(f"last_call:  {st['last_call']}")
    return 0


# ───────────────────────── command: clear ─────────────────────────


def _cmd_clear(data_dir: Path, args: argparse.Namespace) -> int:
    ph = _resolve_project_hash(data_dir, args.project)
    kinds: list[str] | None = list(args.kind) if args.kind else None

    if not args.yes:
        label = ",".join(kinds) if kinds else "ALL"
        sys.stdout.write(
            f"About to clear [{label}] from project {ph}. Continue? [y/N]: "
        )
        sys.stdout.flush()
        try:
            reply = sys.stdin.readline().strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("aborted")
            return 1

    s = _open_storage(data_dir, ph)
    ps = _open_pref_storage(data_dir)
    pid = ProjectId(id=ph, display_name="", source="cwd_hash", origin="")
    store = MemoryStore(pid, s, pref_storage=ps)
    try:
        total = store.clear(kinds)
    finally:
        store.close()
    print(f"cleared {total} rows from {ph}")
    return 0


# ───────────────────────── command: pin / unpin ─────────────────────────


_PIN_KIND_TABLE: dict[str, str] = {"fact": "facts", "adr": "adrs"}


def _cmd_pin(data_dir: Path, args: argparse.Namespace, *, value: int) -> int:
    """Set or clear ``pin`` on a single fact or ADR row (P1-8)."""
    ph = _resolve_project_hash(data_dir, args.project)
    table = _PIN_KIND_TABLE[args.kind]
    s = _open_storage(data_dir, ph)
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        n = s.update(
            table,
            where={"id": int(args.id)},
            values={"pin": value, "updated_at": now},
        )
    finally:
        s.close()
    if n == 0:
        verb = "pin" if value else "unpin"
        print(f"error: cannot {verb}: {args.kind} id {args.id} not found in {ph}",
              file=sys.stderr)
        return 2
    state = "pinned" if value else "unpinned"
    print(f"{state} {args.kind} {args.id} in {ph}")
    return 0


# ───────────────────────── command: export / import ─────────────────────────


def _cmd_export(data_dir: Path, args: argparse.Namespace) -> int:
    ph = _resolve_project_hash(data_dir, args.project)
    s = _open_storage(data_dir, ph)
    ps = _open_pref_storage(data_dir)
    try:
        payload = {
            "meta": {"project_hash": ph, "schema_version": SCHEMA_VERSION,
                     "meta_row": _meta_row(s)},
            "facts": s.query("facts"),
            "adrs": s.query("adrs"),
            "preferences": ps.query("preferences"),
            "sessions": s.query("session_summaries"),
        }
    finally:
        s.close()
        ps.close()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(f"exported project {ph} → {out_path}")
    return 0


def _cmd_import(data_dir: Path, args: argparse.Namespace) -> int:
    ph = _resolve_project_hash(data_dir, args.project)
    in_path = Path(args.in_path)
    try:
        payload = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {in_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print(f"error: invalid export (expected JSON object): {in_path}",
              file=sys.stderr)
        return 1

    replace = bool(args.replace)  # default: merge

    s = _open_storage(data_dir, ph)
    ps = _open_pref_storage(data_dir)
    try:
        if replace:
            for kind in _ALL_KINDS:
                table = _KIND_TABLE[kind]
                target = ps if kind == "preferences" else s
                for r in target.query(table):
                    target.delete(table, id=r["id"])

        total = 0
        skipped = 0
        for kind in _ALL_KINDS:
            table = _KIND_TABLE[kind]
            target = ps if kind == "preferences" else s
            rows = payload.get(kind) or []
            if not isinstance(rows, list):
                continue
            allowed = target.allowed_columns(table)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                unknown = [
                    k for k in row.keys()
                    if k != "id" and k not in allowed
                ]
                if unknown:
                    print(
                        f"warning: dropping unknown column(s) {unknown!r} "
                        f"from {kind} row",
                        file=sys.stderr,
                    )
                clean = {
                    k: v for k, v in row.items()
                    if k != "id" and k in allowed
                }
                if not clean:
                    skipped += 1
                    continue
                try:
                    target.insert(table, clean)
                    total += 1
                except sqlite3.IntegrityError:
                    skipped += 1
                except sqlite3.Error as exc:
                    print(f"warning: skipped row in {kind}: {exc}",
                          file=sys.stderr)
                    skipped += 1
    finally:
        s.close()
        ps.close()

    mode = "replace" if replace else "merge"
    print(f"imported {total} rows into {ph} (mode={mode}, skipped={skipped})")
    return 0


# ───────────────────────── command: where ─────────────────────────


def _cmd_where(data_dir: Path) -> int:
    pid = resolve_project_id()
    print(f"data_dir: {data_dir}")
    print(f"cwd: {Path.cwd()}")
    print(f"project_id: {pid.id}")
    print(f"display_name: {pid.display_name}")
    print(f"id_source: {pid.source}")
    print(f"origin: {pid.origin}")
    return 0


# ───────────────────────── command: doctor ─────────────────────────


def _cmd_doctor(data_dir: Path, args: argparse.Namespace | None = None) -> int:
    """Environment + store health checks.

    Emits a list of checks, each ``OK`` / ``WARN`` / ``FAIL``. Exit code is
    ``0`` if no ``FAIL`` (warnings are tolerated), ``1`` otherwise. Use
    ``--json`` for machine-readable output (schema ``{"version": 1,
    "checks": [...], "summary": {"ok", "warn", "fail"}}``).

    A missing project DB is *not* a failure — it surfaces as a single
    ``WARN`` so ``mindkeep doctor`` remains a useful setup probe.
    """
    import importlib
    import importlib.metadata as _md
    import shutil
    import sysconfig
    import tempfile

    json_mode = bool(getattr(args, "json", False)) if args is not None else False

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
        if json_mode:
            return
        glyph = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[status]
        if status == "OK":
            print(f"{glyph} {msg}")
        else:
            print(f"{glyph} {msg}")

    def ok(cid: str, msg: str, details: dict[str, Any] | None = None) -> None:
        _emit(cid, "OK", msg, details)

    def warn(cid: str, msg: str, details: dict[str, Any] | None = None) -> None:
        _emit(cid, "WARN", msg, details)

    def bad(cid: str, msg: str, details: dict[str, Any] | None = None) -> None:
        _emit(cid, "FAIL", msg, details)

    def section(title: str) -> None:
        if json_mode:
            return
        print()
        print(title)
        print("-" * 40)

    if not json_mode:
        print("mindkeep doctor")
        print("-" * 40)

    section("Environment")

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

    pid = None
    try:
        pid = resolve_project_id()
        ok("current-project",
           f"Current project: id={pid.id} source={pid.source} "
           f"display_name={pid.display_name}",
           {"id": pid.id, "source": pid.source,
            "display_name": pid.display_name})
    except Exception as exc:
        bad("current-project", f"resolve_project_id() failed: {exc}",
            {"error": str(exc)})

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

    section("Store health")
    _run_store_checks(data_dir, pid, ok, warn, bad)

    summary = {
        "ok": sum(1 for c in checks if c["status"] == "OK"),
        "warn": sum(1 for c in checks if c["status"] == "WARN"),
        "fail": sum(1 for c in checks if c["status"] == "FAIL"),
    }

    if json_mode:
        print(json.dumps(
            {"version": 1, "checks": checks, "summary": summary},
            indent=2, sort_keys=False,
        ))
    else:
        print()
        print("-" * 40)
        if summary["fail"] == 0 and summary["warn"] == 0:
            print("All checks passed 🎉")
        elif summary["fail"] == 0:
            print(
                f"Some warnings ({summary['warn']}), you may proceed"
            )
        else:
            print(
                f"{summary['fail']} failing check(s), "
                f"{summary['warn']} warning(s)"
            )
            print("Run with --fix to attempt auto-repair (not yet implemented)")

    return 1 if summary["fail"] else 0


def _run_store_checks(
    data_dir: Path,
    pid: ProjectId | None,
    ok,  # type: ignore[no-untyped-def]
    warn,  # type: ignore[no-untyped-def]
    bad,  # type: ignore[no-untyped-def]
) -> None:
    """Per-project DB health checks (P1-9). Gracefully skips if no DB.

    A missing DB → single ``WARN`` for ``store-database``; the dependent
    checks (schema, wal, fts5, stats, cap-pressure, stale, db-size, pin)
    are skipped.
    """
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
            details = {"db": db_schema, "expected": SCHEMA_VERSION}
            if db_schema == SCHEMA_VERSION:
                ok("schema-version",
                   f"Schema version up-to-date: {db_schema}", details)
            elif db_schema < SCHEMA_VERSION:
                warn("schema-version",
                     f"Schema version {db_schema} older than expected "
                     f"{SCHEMA_VERSION}; run an operation to migrate",
                     details)
            else:
                bad("schema-version",
                    f"Schema version {db_schema} NEWER than this "
                    f"binary supports ({SCHEMA_VERSION}); upgrade mindkeep",
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


# ───────────────────────── argparse plumbing ─────────────────────────


def _is_pipx_install(python_exe: str | None = None) -> bool:
    """Detect whether the current interpreter lives inside a pipx venv.

    pipx installs each tool into ``~/.local/pipx/venvs/<pkg>/`` (POSIX) or
    ``%USERPROFILE%\\pipx\\venvs\\<pkg>\\`` (Windows). We look for
    ``pipx`` followed by ``venvs`` as adjacent path segments — robust to
    either separator and custom PIPX_HOME.
    """
    exe = python_exe if python_exe is not None else sys.executable
    if not exe:
        return False
    parts = Path(exe).parts
    lowered = [p.lower() for p in parts]
    for i in range(len(lowered) - 1):
        if lowered[i] == "pipx" and lowered[i + 1] == "venvs":
            return True
    return False


def _resolve_upgrade_source(cli_source: str | None) -> str:
    """CLI flag > env var > hard-coded default. Keyword ``pypi`` means
    "install from PyPI" and is translated to the bare package name."""
    src = cli_source or os.environ.get(_UPGRADE_SOURCE_ENV) or _DEFAULT_UPGRADE_SOURCE
    if src == "pypi":
        return _PKG_NAME
    return src


def _build_upgrade_cmd(
    source: str,
    *,
    pre: bool,
    use_pipx: bool,
    python_exe: str,
) -> list[str]:
    """Compose the argv that would perform the upgrade.

    Pure function — no side effects — so tests can assert on it.
    """
    if use_pipx:
        # pipx can't "upgrade" a package to a different source, so a
        # non-PyPI source (git+..., local path) must go through
        # ``pipx install --force``.
        is_remote_or_path = (
            source != _PKG_NAME
            and (source.startswith("git+")
                 or source.startswith("http://")
                 or source.startswith("https://")
                 or "/" in source
                 or "\\" in source)
        )
        if is_remote_or_path:
            cmd = ["pipx", "install", "--force", source]
            if pre:
                cmd.append("--pip-args=--pre")
            return cmd
        cmd = ["pipx", "upgrade", _PKG_NAME]
        if pre:
            cmd.append("--pip-args=--pre")
        return cmd

    cmd = [python_exe, "-m", "pip", "install", "--upgrade", "--user"]
    if pre:
        cmd.append("--pre")
    cmd.append(source)
    return cmd


def _current_version() -> str:
    """Best-effort installed-version lookup; falls back to the constant."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version(_PKG_NAME)
    except Exception:
        return VERSION


def _cmd_upgrade(args: argparse.Namespace) -> int:
    before = _current_version()
    print(f"current mindkeep version: {before}")

    source = _resolve_upgrade_source(args.source)
    use_pipx = _is_pipx_install()
    cmd = _build_upgrade_cmd(
        source,
        pre=bool(args.pre),
        use_pipx=use_pipx,
        python_exe=sys.executable,
    )
    mode = "pipx" if use_pipx else "pip"
    print(f"install mode detected: {mode}")
    print("command: " + " ".join(cmd))

    if args.dry_run:
        print("dry-run: no changes made.")
        return 0

    if not args.yes:
        try:
            reply = input("proceed with upgrade? [y/N]: ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("aborted.", file=sys.stderr)
            return 1

    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:
        print(f"error: command not found: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"error: upgrade command exited with code {result.returncode}",
              file=sys.stderr)
        return result.returncode

    after = _current_version()
    if after != before:
        print(f"upgraded: {before} -> {after}")
    else:
        print(f"version unchanged: {after} "
              "(already latest or restart required)")
    return 0


def _cmd_recall(data_dir: Path, args: argparse.Namespace) -> int:
    """Full-text search across facts and ADRs (P0-4, #9).

    Honors the session budget via the same StringIO-buffering pattern as
    ``_cmd_show``: render the entire output first, then feed it through
    ``_session.emit_or_suppress`` so a single call cannot quietly half-print.
    """
    import io

    from .memory_api import MemoryStore

    raw_query = args.query or ""
    if not raw_query.strip():
        print(
            "error: recall requires a non-empty query",
            file=sys.stderr,
        )
        return 2

    ph = _resolve_project_hash(data_dir, args.project)
    pid = ProjectId(id=ph, source="cli", origin="recall", display_name="")
    storage = _open_storage(data_dir, ph)
    pref_storage = _open_pref_storage(data_dir)
    store = MemoryStore(pid, storage, pref_storage=pref_storage)
    try:
        hits = store.recall(raw_query, top=args.top, kind=args.kind)
    finally:
        store.close()

    if getattr(args, "json", False):
        payload = [h.to_dict() for h in hits]
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        _session.emit_or_suppress(rendered)
        return 0

    if not hits:
        _session.emit_or_suppress("No results.")
        return 0

    buf = io.StringIO()
    buf.write(
        f"recall: {len(hits)} hit(s) for {raw_query!r} "
        f"(lower bm25 = better match)\n"
    )
    headers = ["#", "kind", "id", "score", "tags", "snippet"]
    rows: list[list[str]] = []
    for i, h in enumerate(hits, 1):
        rows.append(
            [
                str(i),
                h.kind,
                str(h.id),
                f"{h.score:.3f}",
                _trunc(",".join(h.tags), 24),
                _trunc(h.snippet, 80),
            ]
        )
    buf.write(_render_table(headers, rows))
    _session.emit_or_suppress(buf.getvalue().rstrip("\n"))
    return 0


def _cmd_integrate(args: argparse.Namespace) -> int:
    from . import _integrations

    if getattr(args, "list", False):
        for name in _integrations.supported():
            print(name)
        return 0

    target = args.target
    if target is None or target not in _integrations.TARGETS:
        supported = ", ".join(_integrations.supported())
        print(f"error: unknown target: {target!r}", file=sys.stderr)
        print(f"supported targets: {supported}", file=sys.stderr)
        return 2

    snippet = _integrations.render(target)

    out = getattr(args, "out", None)
    if out:
        out_path = Path(out)
        if out_path.exists() and not getattr(args, "force", False):
            print(
                f"error: refusing to overwrite {out_path} (pass --force to overwrite)",
                file=sys.stderr,
            )
            return 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(snippet, encoding="utf-8")
        return 0

    sys.stdout.write(snippet)
    return 0


def _cmd_stats(data_dir: Path, args: argparse.Namespace) -> int:
    """Print per-project store stats (human or JSON)."""
    ph = _resolve_project_hash(data_dir, args.project)
    s = _open_storage(data_dir, ph)
    ps = _open_pref_storage(data_dir)
    try:
        data = s.stats()
        prefs_total = ps.query("preferences")
    finally:
        s.close()
        ps.close()
    data["data_dir"] = str(data_dir)
    data["preferences"] = {"total": len(prefs_total)}

    # Optional session-budget integration (P0-2 sibling).
    budget_block: dict[str, Any] | None = None
    try:
        from ._session import current_state  # type: ignore[attr-defined]
        st = current_state()
        if st is not None:
            budget_block = {
                "active": bool(st.get("active", True)),
                "budget": st.get("budget"),
                "spent": st.get("spent"),
                "calls": st.get("calls"),
            }
    except Exception:
        budget_block = None
    data["session_budget"] = budget_block

    if getattr(args, "json", False):
        ordered = {
            "schema_version": data["schema_version"],
            "project_id": data["project_id"],
            "data_dir": data["data_dir"],
            "db_size_bytes": data["db_size_bytes"],
            "facts": data["facts"],
            "adrs": data["adrs"],
            "preferences": data["preferences"],
            "sessions": data["sessions"],
            "top_tags": data["top_tags"],
            "tokens_estimated_total": data["tokens_estimated_total"],
            "oldest_fact_at": data["oldest_fact_at"],
            "newest_fact_at": data["newest_fact_at"],
            "session_budget": data["session_budget"],
        }
        print(json.dumps(ordered, indent=2, sort_keys=False))
        return 0

    label_w = 20
    print(f"mindkeep store · project={ph} · path={data_dir}")
    print("─" * 60)

    def _line(label: str, value: str) -> None:
        print(f"{label.ljust(label_w)}{value}")

    _line("Schema version:", str(data["schema_version"]))
    f = data["facts"]
    _line(
        "Facts:",
        f"{f['total']}  (pinned: {f['pinned']}, archived: {f['archived']})",
    )
    a = data["adrs"]
    _line(
        "ADRs:",
        f"{a['total']}  (pinned: {a['pinned']}, archived: {a['archived']})",
    )
    _line("Preferences:", str(data["preferences"]["total"]))
    _line("Sessions:", str(data["sessions"]["total"]))
    if data["top_tags"]:
        tags_str = ", ".join(
            f"{t['tag']} ({t['count']})" for t in data["top_tags"]
        )
    else:
        tags_str = "(none)"
    _line("Top tags:", tags_str)
    _line("Total tokens (est):", str(data["tokens_estimated_total"]))
    _line("DB file size:", _fmt_size(data["db_size_bytes"]))
    _line("Oldest fact:", data["oldest_fact_at"] or "(none)")
    _line("Newest fact:", data["newest_fact_at"] or "(none)")
    if budget_block is None:
        _line("Session budget:", "not active")
    else:
        _line("Session budget:", "active")
        spent = budget_block.get("spent")
        budget = budget_block.get("budget")
        calls = budget_block.get("calls")
        print(
            f"{''.ljust(label_w)}spent={spent} budget={budget} calls={calls}"
        )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mindkeep",
        description="Inspect and maintain the mindkeep on-disk store.",
    )
    p.add_argument("--version", action="version",
                   version=f"mindkeep {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    sub.add_parser("list", help="list all known projects")

    ps = sub.add_parser("show", help="show rows for a project")
    ps.add_argument("--project", default=None,
                    help="project hash or display_name (default: current cwd)")
    ps.add_argument(
        "--kind",
        default="all",
        choices=["facts", "adrs", "preferences", "sessions", "all"],
    )
    ps.add_argument("--tag", default=None)
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument(
        "--top", type=int, default=None,
        help="cap rows per kind (in addition to --limit); default unlimited (P0-3)",
    )
    ps.add_argument(
        "--budget", type=int, default=None,
        help="cap cumulative rendered token count across all rows shown; "
             "default unlimited (P0-3)",
    )
    ps.add_argument(
        "--pinned", action="store_true",
        help="only show pinned facts/ADRs (P1-8)",
    )
    ps.add_argument(
        "--full", "--no-truncate",
        dest="full", action="store_true",
        help="print full values without the default ~52-char truncation "
             "(newlines preserved; table alignment is sacrificed)",
    )

    pc = sub.add_parser("clear", help="delete rows from a project")
    pc.add_argument("--project", default=None)
    pc.add_argument(
        "--kind", action="append", choices=list(_ALL_KINDS),
        help="may be given multiple times; default = all kinds",
    )
    pc.add_argument("--yes", action="store_true",
                    help="skip confirmation prompt")

    for verb, helptext in (("pin",   "pin a fact or ADR so it surfaces first"),
                           ("unpin", "remove the pinned flag from a fact or ADR")):
        pp = sub.add_parser(verb, help=helptext)
        pp.add_argument("--project", default=None,
                        help="project hash or display_name (default: current cwd)")
        pp.add_argument("kind", choices=["fact", "adr"], metavar="<kind>",
                        help="row type: fact | adr")
        pp.add_argument("id", type=int, metavar="<id>",
                        help="row id (from `mindkeep show`)")

    pe = sub.add_parser("export", help="dump a project to JSON")
    pe.add_argument("--project", default=None)
    pe.add_argument("out", help="output JSON path")

    pi = sub.add_parser("import", help="load a JSON dump into a project")
    pi.add_argument("--project", default=None)
    pi.add_argument("in_path", metavar="in", help="input JSON path")
    g = pi.add_mutually_exclusive_group()
    g.add_argument("--merge", action="store_true",
                   help="keep existing rows (default)")
    g.add_argument("--replace", action="store_true",
                   help="wipe project before import")

    sub.add_parser("where", help="print data_dir and current project id")

    pdoc = sub.add_parser("doctor", help="run environment health checks")
    pdoc.add_argument("--json", action="store_true",
                      help="emit machine-readable JSON instead of human format")

    pst = sub.add_parser("stats", help="print introspection stats for a project")
    pst.add_argument("--project", default=None,
                     help="project hash or display_name (default: current cwd)")
    pst.add_argument("--json", action="store_true",
                     help="emit machine-readable JSON instead of the human format")

    pr = sub.add_parser(
        "recall",
        help="full-text search across facts and adrs (FTS5 + bm25)",
    )
    pr.add_argument("query", metavar="<query>",
                    help="search string; wrapped as a phrase unless it contains "
                         "FTS5 operators (AND/OR/NOT/NEAR, quotes, *, :, ^, parens)")
    pr.add_argument("--project", default=None,
                    help="project hash or display_name (default: current cwd)")
    pr.add_argument("--top", type=int, default=10,
                    help="maximum number of hits to return (default: 10)")
    pr.add_argument("--kind", default="all",
                    choices=["facts", "adrs", "all"],
                    help="restrict search to one kind (default: all)")
    pr.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of the human format")

    pu = sub.add_parser(
        "upgrade",
        help="pull the latest mindkeep (pip/pipx auto-detected)",
    )
    pu.add_argument(
        "--source", default=None,
        help=f"override install source (git+URL, local path, or 'pypi'). "
             f"Default: ${_UPGRADE_SOURCE_ENV} env or "
             f"{_DEFAULT_UPGRADE_SOURCE}",
    )
    pu.add_argument("--pre", action="store_true",
                    help="allow pre-release versions")
    pu.add_argument("--dry-run", action="store_true",
                    help="print the command that would run and exit")
    pu.add_argument("--yes", "-y", action="store_true",
                    help="skip interactive confirmation")

    pint = sub.add_parser(
        "integrate",
        help="emit an integration snippet for an AI coding agent "
             "(claude|copilot|cursor|generic)",
    )
    pint.add_argument(
        "target", nargs="?", default=None, metavar="<target>",
        help="one of: claude, copilot, cursor, generic",
    )
    pint.add_argument(
        "--list", action="store_true",
        help="list supported targets and exit",
    )
    pint.add_argument(
        "--out", default=None, metavar="PATH",
        help="write the snippet to PATH instead of stdout "
             "(creates parent dirs; refuses to overwrite without --force)",
    )
    pint.add_argument(
        "--force", action="store_true",
        help="overwrite --out PATH if it already exists",
    )

    psess = sub.add_parser(
        "session",
        help="inspect or reset the per-shell token budget state",
    )
    psess_sub = psess.add_subparsers(dest="session_cmd", metavar="<subcommand>")
    psess_sub.add_parser("status", help="print current session spend (default)")
    psess_sub.add_parser("reset", help="delete the current session state file")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    data_dir = default_data_dir()

    try:
        if args.cmd == "list":
            return _cmd_list(data_dir)
        if args.cmd == "show":
            return _cmd_show(data_dir, args)
        if args.cmd == "clear":
            return _cmd_clear(data_dir, args)
        if args.cmd == "pin":
            return _cmd_pin(data_dir, args, value=1)
        if args.cmd == "unpin":
            return _cmd_pin(data_dir, args, value=0)
        if args.cmd == "export":
            return _cmd_export(data_dir, args)
        if args.cmd == "import":
            return _cmd_import(data_dir, args)
        if args.cmd == "where":
            return _cmd_where(data_dir)
        if args.cmd == "doctor":
            return _cmd_doctor(data_dir, args)
        if args.cmd == "stats":
            return _cmd_stats(data_dir, args)
        if args.cmd == "recall":
            return _cmd_recall(data_dir, args)
        if args.cmd == "upgrade":
            return _cmd_upgrade(args)
        if args.cmd == "session":
            return _cmd_session(args)
        if args.cmd == "integrate":
            return _cmd_integrate(args)
    except _ProjectNotFound as exc:
        # User asked for a project that doesn't exist → user error.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        # Storage-level failure (corrupt DB, locked, etc.).
        print(f"error: storage failure: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # Filter / validation rejection (unknown column, unknown kind,
        # filter-raised rejection).  Mapped to dedicated code 3 so
        # scripts can distinguish from generic user errors.
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # pragma: no cover - defensive
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Should be unreachable because subparsers is required=True.
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

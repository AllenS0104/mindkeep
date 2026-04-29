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


def _show_kind(
    storage: Storage, kind: str, tag: str | None, limit: int,
    pref_storage: Storage | None = None,
    full: bool = False,
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

    if kind == "facts":
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        if tag:
            rows = [r for r in rows if tag in _tags_list(r.get("tags", ""))]
        rows = rows[: max(0, limit)]
        headers = ["id", "key", "value", "tags", "updated_at"]
        data = [
            [
                str(r["id"]),
                _trunc(r.get("key", "")),
                _cell(r.get("value", "")),
                _trunc(r.get("tags", "")),
                _trunc(r.get("updated_at", "")),
            ]
            for r in rows
        ]
    elif kind == "adrs":
        rows.sort(key=lambda r: int(r.get("number", 0)))
        if tag:
            rows = [r for r in rows if tag in _tags_list(r.get("tags", ""))]
        rows = rows[: max(0, limit)]
        headers = ["number", "title", "status", "decision", "tags", "updated_at"]
        data = [
            [
                str(r.get("number", "")),
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

    print(f"== {kind} ==")
    if not data:
        print("(no rows)")
    else:
        print(_render_table(headers, data))
    print()


def _cmd_show(data_dir: Path, args: argparse.Namespace) -> int:
    ph = _resolve_project_hash(data_dir, args.project)
    kinds = _ALL_KINDS if args.kind == "all" else (args.kind,)
    full = bool(getattr(args, "full", False) or getattr(args, "no_truncate", False))
    s = _open_storage(data_dir, ph)
    ps = _open_pref_storage(data_dir)
    try:
        print(f"project: {ph}")
        for kind in kinds:
            _show_kind(s, kind, args.tag, args.limit,
                       pref_storage=ps, full=full)
    finally:
        s.close()
        ps.close()
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


def _cmd_doctor(data_dir: Path) -> int:
    """Environment health check. Prints ✅/❌/⚠️ per item.

    Exit 0 if no ❌ (warnings allowed); non-zero if any ❌.
    """
    import importlib
    import importlib.metadata as _md
    import shutil
    import sysconfig
    import tempfile

    errors = 0
    warnings = 0

    def ok(msg: str) -> None:
        print(f"✅ {msg}")

    def bad(msg: str) -> None:
        nonlocal errors
        errors += 1
        print(f"❌ {msg}")

    def warn(msg: str) -> None:
        nonlocal warnings
        warnings += 1
        print(f"⚠️  {msg}")

    print("mindkeep doctor")
    print("-" * 40)

    # 1. Python version
    v = sys.version_info
    py = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 9):
        ok(f"Python version: {py} (>= 3.9)")
    else:
        bad(f"Python version: {py} (need >= 3.9)")

    # 2. mindkeep package installed
    try:
        pkg_version = _md.version("mindkeep")
        ok(f"mindkeep installed: {pkg_version}")
    except _md.PackageNotFoundError:
        bad("mindkeep not installed (importlib.metadata lookup failed)")

    # 3. CLI on PATH
    exe = shutil.which("mindkeep")
    if exe:
        ok(f"CLI on PATH: {exe}")
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
        warn(f"'mindkeep' not in PATH; add scripts dir: {hint}")

    # 4. Data dir writable
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".health"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok(f"Data dir writable: {data_dir}")
    except (OSError, PermissionError) as exc:
        bad(f"Data dir not writable ({data_dir}): {exc}")

    # 5. SQLite WAL support
    try:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "probe.db"
            conn = sqlite3.connect(str(db_path))
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            finally:
                conn.close()
        if str(mode).lower() == "wal":
            ok("SQLite WAL mode supported")
        else:
            warn(f"SQLite journal_mode returned '{mode}' (expected 'wal')")
    except sqlite3.Error as exc:
        bad(f"SQLite WAL probe failed: {exc}")

    # 6. Filters (SecretsRedactor) loadable
    try:
        mod = importlib.import_module("mindkeep.security")
        redactor_cls = getattr(mod, "SecretsRedactor")
        redactor_cls()
        ok("Filters loaded: SecretsRedactor OK")
    except Exception as exc:
        bad(f"SecretsRedactor failed to load: {exc}")

    # 7. Current project identification
    try:
        pid = resolve_project_id()
        ok(
            f"Current project: id={pid.id} source={pid.source} "
            f"display_name={pid.display_name}"
        )
    except Exception as exc:
        bad(f"resolve_project_id() failed: {exc}")

    # 8. Existing project count
    try:
        if data_dir.exists():
            dbs = [
                p for p in data_dir.glob("*.db")
                if p.name != "preferences.db"
            ]
            ok(f"Known projects: {len(dbs)} DB file(s) in {data_dir}")
        else:
            warn(f"Data dir does not exist yet: {data_dir}")
    except OSError as exc:
        warn(f"Could not enumerate project DBs: {exc}")

    print("-" * 40)
    if errors == 0 and warnings == 0:
        print("All checks passed 🎉")
        return 0
    if errors == 0:
        print("Some warnings, you may proceed")
        return 0
    print("Run with --fix to attempt auto-repair (not yet implemented)")
    return 1


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

    sub.add_parser("doctor", help="run environment health checks")

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
        if args.cmd == "export":
            return _cmd_export(data_dir, args)
        if args.cmd == "import":
            return _cmd_import(data_dir, args)
        if args.cmd == "where":
            return _cmd_where(data_dir)
        if args.cmd == "doctor":
            return _cmd_doctor(data_dir)
        if args.cmd == "upgrade":
            return _cmd_upgrade(args)
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

"""Crash-recovery regression tests.

Launches ``tests/_crash_child.py`` in a subprocess, SIGKILL /
TerminateProcess the child at various points of its write cycle, then
re-opens the store in the parent and asserts the committed data is
intact.

Contract (ARCHITECTURE.md §7):
    - SQLite journal_mode=WAL + synchronous=NORMAL.
    - ``Storage.insert`` commits each row — therefore any successful
      ``add_fact`` return is durable, even when the child is SIGKILL-ed
      immediately afterwards.
    - A crashed child may only lose data whose commit never landed.
    - The DB must remain ``PRAGMA integrity_check == 'ok'`` after a
      hard kill.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mindkeep.memory_api import MemoryStore
from mindkeep.project_id import resolve_project_id
from mindkeep.storage import Storage

# ──────────────────────────── helpers ────────────────────────────

_CHILD_SCRIPT = Path(__file__).parent / "_crash_child.py"

# Large enough to comfortably beat slow CI startup on Windows.  We never
# actually wait this long — the parent kills the child as soon as the
# ready marker appears.
_READY_TIMEOUT = 30.0


def _launch_child(
    *,
    data_dir: Path,
    cwd_override: Path,
    mode: str,
    count: int,
    ready: Path,
    sleep: float = 30.0,
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["MINDKEEP_HOME"] = str(data_dir)
    # Make the source tree importable without relying on an editable install.
    src_root = Path(__file__).resolve().parent.parent / "src"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{src_root}{os.pathsep}{existing}" if existing else str(src_root)
    )
    cmd = [
        sys.executable,
        "-u",
        str(_CHILD_SCRIPT),
        "--data-dir", str(data_dir),
        "--cwd", str(cwd_override),
        "--count", str(count),
        "--mode", mode,
        "--ready", str(ready),
        "--sleep", str(sleep),
    ]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)


def _wait_ready(proc: subprocess.Popen[bytes], ready: Path,
                timeout: float = _READY_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready.exists():
            return
        if proc.poll() is not None:
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise AssertionError(
                f"child exited prematurely rc={proc.returncode}\n"
                f"STDOUT:\n{out}\nSTDERR:\n{err}"
            )
        time.sleep(0.05)
    raise AssertionError("child never signalled ready within timeout")


def _hard_kill(proc: subprocess.Popen[bytes]) -> None:
    """Terminate without giving the child any chance to clean up.

    On POSIX this raises SIGKILL; on Windows ``Popen.kill`` invokes
    ``TerminateProcess`` which is the same 'drop the process right now'
    semantics as ``taskkill /F``.
    """
    try:
        proc.kill()
    except ProcessLookupError:  # pragma: no cover - already gone
        return
    # Reap so the OS doesn't leave a zombie / keep the DB file locked.
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.terminate()
        proc.wait(timeout=5)


def _assert_integrity(db_path: Path) -> None:
    """Open the DB directly (bypassing our wrapper) and run integrity_check."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        assert row is not None and row[0] == "ok", f"integrity_check={row!r}"
    finally:
        conn.close()


def _project_hash_for(cwd_override: Path) -> str:
    return resolve_project_id(cwd_override).id


def _reopen(data_dir: Path, cwd_override: Path) -> MemoryStore:
    return MemoryStore.open(cwd=cwd_override, data_dir=data_dir)


# ──────────────────────────── fixtures ────────────────────────────


@pytest.fixture()
def child_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (data_dir, cwd_override, ready_marker)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cwd_override = tmp_path / "proj"
    cwd_override.mkdir()
    ready = tmp_path / "ready.flag"
    return data_dir, cwd_override, ready


# ──────────────────────────── scenarios ────────────────────────────


def test_scenario1_auto_commit_writes_survive_sigkill(child_env) -> None:
    """Scenario 1 — every add_fact auto-commits; SIGKILL must not lose them.

    Storage.insert() runs ``conn.commit()`` after every row, and WAL+NORMAL
    fsyncs on commit boundaries.  Therefore even without any explicit
    flush() or close(), all 5 facts must still be visible after we hard-kill
    the child.
    """
    data_dir, cwd_override, ready = child_env

    proc = _launch_child(data_dir=data_dir, cwd_override=cwd_override,
                         mode="auto-only", count=5, ready=ready)
    try:
        _wait_ready(proc, ready)
        _hard_kill(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    # DB must be readable and uncorrupted.
    ph = _project_hash_for(cwd_override)
    db_path = data_dir / f"{ph}.db"
    assert db_path.exists(), f"expected DB at {db_path}"
    _assert_integrity(db_path)

    store = _reopen(data_dir, cwd_override)
    try:
        facts = store.list_facts()
        values = sorted(f["value"] for f in facts)
        assert values == [f"auto-{i}" for i in range(5)], values
    finally:
        store.close()


def test_scenario2_explicit_commit_writes_survive_sigkill(child_env) -> None:
    """Scenario 2 — explicit .commit() before SIGKILL guarantees no loss."""
    data_dir, cwd_override, ready = child_env

    proc = _launch_child(data_dir=data_dir, cwd_override=cwd_override,
                         mode="no-op-commit", count=5, ready=ready)
    try:
        _wait_ready(proc, ready)
        _hard_kill(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    store = _reopen(data_dir, cwd_override)
    try:
        facts = store.list_facts()
        values = sorted(f["value"] for f in facts)
        assert values == [f"cm-{i}" for i in range(5)], values
    finally:
        store.close()

    ph = _project_hash_for(cwd_override)
    _assert_integrity(data_dir / f"{ph}.db")


def test_scenario3_scheduler_flush_survives_sigkill(child_env) -> None:
    """Scenario 3 — FlushScheduler commits on its own; SIGKILL then reopen."""
    data_dir, cwd_override, ready = child_env

    proc = _launch_child(data_dir=data_dir, cwd_override=cwd_override,
                         mode="scheduler", count=5, ready=ready)
    try:
        _wait_ready(proc, ready)
        _hard_kill(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    store = _reopen(data_dir, cwd_override)
    try:
        facts = store.list_facts()
        values = sorted(f["value"] for f in facts)
        assert values == [f"sch-{i}" for i in range(5)], values
    finally:
        store.close()


def test_scenario4_kill_during_concurrent_writes_no_corruption(child_env) -> None:
    """Scenario 4 — kill mid-burst; DB must not be corrupt, committed rows survive."""
    data_dir, cwd_override, ready = child_env

    proc = _launch_child(data_dir=data_dir, cwd_override=cwd_override,
                         mode="concurrent", count=20, ready=ready)
    try:
        _wait_ready(proc, ready)
        # Let writers run a bit longer to maximise the chance of killing
        # right in the middle of an insert.
        time.sleep(0.2)
        _hard_kill(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    ph = _project_hash_for(cwd_override)
    db_path = data_dir / f"{ph}.db"
    # Primary guarantee: the file is not corrupt.
    _assert_integrity(db_path)

    store = _reopen(data_dir, cwd_override)
    try:
        facts = store.list_facts(limit=100000)
        # At least the first ``count`` rows observed before _wait_ready
        # triggered must be visible (their add_fact had already returned).
        assert len(facts) >= 20, f"expected >=20 facts, got {len(facts)}"
        # Every fact must be well-formed — no NULL bytes, no truncation.
        for f in facts:
            assert f["value"].startswith("cc-"), f
            assert f["key"]
    finally:
        store.close()


def test_scenario5_missing_sidecar_is_tolerated(child_env) -> None:
    """Scenario 5 — deleting the .meta.json sidecar must not break reopen."""
    data_dir, cwd_override, ready = child_env

    proc = _launch_child(data_dir=data_dir, cwd_override=cwd_override,
                         mode="scheduler", count=5, ready=ready)
    try:
        _wait_ready(proc, ready)
        _hard_kill(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    ph = _project_hash_for(cwd_override)
    sidecar = data_dir / f"{ph}.meta.json"
    if sidecar.exists():
        sidecar.unlink()

    # Reopen must still work and rebuild whatever it needs from the DB.
    store = _reopen(data_dir, cwd_override)
    try:
        facts = store.list_facts()
        assert len(facts) == 5
    finally:
        store.close()

    # CLI must not crash when sidecars are missing — it falls back to
    # "No projects yet." output, exit code 0.
    src_root = Path(__file__).resolve().parent.parent / "src"
    env = os.environ.copy()
    env["MINDKEEP_HOME"] = str(data_dir)
    env["PYTHONPATH"] = (
        f"{src_root}{os.pathsep}{env.get('PYTHONPATH', '')}"
        if env.get("PYTHONPATH") else str(src_root)
    )
    # Ensure no sidecars remain (handles scheduler-created ones).
    for p in data_dir.glob("*.meta.json"):
        p.unlink()
    rv = subprocess.run(
        [sys.executable, "-m", "mindkeep", "list"],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert rv.returncode == 0, (rv.returncode, rv.stdout, rv.stderr)


def test_scenario6_repeated_crash_restart_cycle_is_monotonic(child_env) -> None:
    """Scenario 6 — 3 rounds of write+kill+reopen; row count only grows."""
    data_dir, cwd_override, ready = child_env

    total_expected = 0
    seen_values: set[str] = set()
    for round_ix in range(3):
        # Each round touches the same DB.  Delete the ready marker so we
        # correctly detect the new child's signal.
        if ready.exists():
            ready.unlink()
        proc = _launch_child(
            data_dir=data_dir,
            cwd_override=cwd_override,
            mode="no-op-commit",
            count=4,
            ready=ready,
        )
        try:
            _wait_ready(proc, ready)
            _hard_kill(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

        ph = _project_hash_for(cwd_override)
        _assert_integrity(data_dir / f"{ph}.db")

        total_expected += 4
        store = _reopen(data_dir, cwd_override)
        try:
            facts = store.list_facts(limit=10000)
            assert len(facts) == total_expected, (
                f"round {round_ix}: expected {total_expected}, got {len(facts)}"
            )
            # All previously-seen values must still be present.
            current = {f["value"] for f in facts}
            assert seen_values <= current, seen_values - current
            seen_values = current
        finally:
            store.close()


def test_scenario7_concurrent_child_processes_commit_survives(tmp_path: Path) -> None:
    """Scenario 7 (optional) — 3 clean writers + 1 killed writer share a DB.

    Proves the single-file WAL can absorb one crashing writer without
    affecting the others' committed rows.  ``busy_timeout=5000`` in
    Storage gives enough slack on Windows.
    """
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd_override = tmp_path / "proj"; cwd_override.mkdir()

    ready_markers = [tmp_path / f"ready-{i}.flag" for i in range(4)]
    procs = []
    for i, rf in enumerate(ready_markers):
        procs.append(
            _launch_child(
                data_dir=data_dir,
                cwd_override=cwd_override,
                # Writer #0 is the sacrificial victim (short sleep so it
                # gets killed mid-flight; the others write, commit, exit).
                mode="no-op-commit",
                count=3,
                ready=rf,
                sleep=30.0 if i == 0 else 0.1,
            )
        )

    victim = procs[0]
    survivors = procs[1:]

    try:
        # Wait for every child to reach its ready point.
        for p, rf in zip(procs, ready_markers):
            _wait_ready(p, rf)

        # Kill the victim hard; let survivors finish on their own.
        _hard_kill(victim)
        for p in survivors:
            rc = p.wait(timeout=30)
            assert rc == 0, rc
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)

    ph = _project_hash_for(cwd_override)
    _assert_integrity(data_dir / f"{ph}.db")

    store = _reopen(data_dir, cwd_override)
    try:
        facts = store.list_facts(limit=10000)
        # 4 writers × 3 rows = 12.  The victim's 3 rows also committed
        # (Storage auto-commits per insert) before its ready signal.
        assert len(facts) == 12, (len(facts), [f["value"] for f in facts])
    finally:
        store.close()


def test_scenario8_mixed_mode_survives_sigkill(child_env) -> None:
    """Scenario 8 — mixed facts+adr+pref writes survive a hard kill.

    Exercises the fixed `_crash_child` mixed branch which previously
    called `add_adr` with a stale signature (P1-9).
    """
    data_dir, cwd_override, ready = child_env

    proc = _launch_child(data_dir=data_dir, cwd_override=cwd_override,
                         mode="mixed", count=3, ready=ready)
    try:
        _wait_ready(proc, ready)
        _hard_kill(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    ph = _project_hash_for(cwd_override)
    _assert_integrity(data_dir / f"{ph}.db")

    store = _reopen(data_dir, cwd_override)
    try:
        facts = sorted(f["value"] for f in store.list_facts())
        assert facts == [f"mx-fact-{i}" for i in range(3)]

        adrs = store.list_adrs()
        assert len(adrs) == 1
        assert adrs[0]["title"] == "crash-mixed"
        assert adrs[0]["decision"] == "dec"
        # rationale maps to the schema's context column.
        assert adrs[0]["context"] == "ctx"
        assert adrs[0]["status"] == "accepted"

        # Preference landed in the cross-project prefs DB.
        assert store.get_preference("mx-pref") == "v"
    finally:
        store.close()

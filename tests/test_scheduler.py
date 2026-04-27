"""Tests for :mod:`mindkeep.scheduler`.

See ARCHITECTURE.md §7.2–§7.3 for the contract these tests enforce.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mindkeep.memory_api import MemoryStore
from mindkeep.scheduler import FlushScheduler


# ──────────────────────────── helpers ────────────────────────────


class _FakeStore:
    """Drop-in stand-in for MemoryStore.commit() / .closed accounting."""

    def __init__(self, raise_on_commit: bool = False) -> None:
        self.commit_calls = 0
        self.closed = False
        self._raise = raise_on_commit
        self._lock = threading.Lock()

    def commit(self) -> None:
        with self._lock:
            self.commit_calls += 1
        if self._raise:
            raise RuntimeError("boom")


# ──────────────────────────── tests ────────────────────────────


def test_periodic_commits_called_multiple_times() -> None:
    """(1) Starting with a short interval triggers repeated commits."""
    store = _FakeStore()
    sched = FlushScheduler(store, interval=0.1)  # type: ignore[arg-type]
    sched.start()
    try:
        time.sleep(0.35)
    finally:
        sched.stop()
    # Expect ≥3 interval ticks in 0.35s (first ~0.1, then ~0.2, ~0.3).
    # stop() also performs a final commit, so the lower bound is safe.
    assert store.commit_calls >= 3, f"only {store.commit_calls} commits observed"


def test_no_commits_after_stop() -> None:
    """(2) Once stopped, no further background commits occur."""
    store = _FakeStore()
    sched = FlushScheduler(store, interval=0.1)  # type: ignore[arg-type]
    sched.start()
    time.sleep(0.25)
    sched.stop()
    snapshot = store.commit_calls
    time.sleep(0.5)
    assert store.commit_calls == snapshot, (
        f"commits kept happening after stop: {snapshot} -> {store.commit_calls}"
    )


def test_stop_is_idempotent() -> None:
    """(3) stop() can be called many times without raising."""
    store = _FakeStore()
    sched = FlushScheduler(store, interval=0.1)  # type: ignore[arg-type]
    sched.start()
    sched.stop()
    sched.stop()  # must not raise
    sched.stop()


def test_background_exception_is_swallowed_and_logged(caplog) -> None:
    """(4) A failing commit is logged but does not crash the thread."""
    store = _FakeStore(raise_on_commit=True)
    sched = FlushScheduler(store, interval=0.05)  # type: ignore[arg-type]
    with caplog.at_level("WARNING", logger="mindkeep.scheduler"):
        sched.start()
        time.sleep(0.25)
        thread_alive_mid = sched._thread is not None and sched._thread.is_alive()
        sched.stop()

    assert thread_alive_mid, "thread died on first exception"
    assert store.commit_calls >= 2
    assert any("commit failed" in rec.message for rec in caplog.records)


def test_context_manager_stops_on_exit() -> None:
    """(5) Using the scheduler as a context manager auto-stops on exit."""
    store = _FakeStore()
    with FlushScheduler(store, interval=0.1) as sched:  # type: ignore[arg-type]
        assert sched.started
        time.sleep(0.15)
    assert sched.stopped
    snapshot = store.commit_calls
    time.sleep(0.3)
    assert store.commit_calls == snapshot


def test_memorystore_open_auto_flush_persists(tmp_path: Path) -> None:
    """(6) MemoryStore.open(auto_flush_interval=…) persists data periodically."""
    data_dir = tmp_path / "am"
    store = MemoryStore.open(
        cwd=tmp_path, data_dir=data_dir, auto_flush_interval=0.1,
    )
    try:
        store.add_fact("persistent via scheduler", tags=["test"])
        # Wait long enough for at least one background flush to run.
        time.sleep(0.35)
    finally:
        store.close()

    # Re-open without scheduler → must observe the previously-flushed row.
    store2 = MemoryStore.open(cwd=tmp_path, data_dir=data_dir)
    try:
        facts = store2.list_facts()
        assert any(
            "persistent via scheduler" in (f.get("value") or f.get("content") or "")
            for f in facts
        )
    finally:
        store2.close()


def test_atexit_register_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """(7) start() registers an atexit callback (without firing it)."""
    registered: list[object] = []

    def fake_register(fn, *args, **kwargs):  # noqa: ANN001
        registered.append(fn)
        return fn

    monkeypatch.setattr("mindkeep.scheduler.atexit.register", fake_register)
    # Also stub unregister so stop() doesn't blow up.
    monkeypatch.setattr(
        "mindkeep.scheduler.atexit.unregister", lambda fn: None,
    )

    store = _FakeStore()
    sched = FlushScheduler(store, interval=0.1)  # type: ignore[arg-type]
    try:
        sched.start()
        assert sched.stop in registered, (
            "atexit.register was not called with scheduler.stop"
        )
    finally:
        sched.stop()


def test_signal_registration_skipped_off_main_thread() -> None:
    """(8) Creating/starting the scheduler off the main thread must not raise."""
    store = _FakeStore()
    errors: list[BaseException] = []
    sched_box: list[FlushScheduler] = []

    def worker() -> None:
        try:
            s = FlushScheduler(store, interval=0.1)  # type: ignore[arg-type]
            s.start()
            sched_box.append(s)
        except BaseException as exc:  # pragma: no cover - on failure
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert not errors, f"scheduler raised on non-main thread: {errors!r}"
    assert sched_box, "scheduler was never constructed"
    sched = sched_box[0]

    # Background thread still alive & ticking.
    time.sleep(0.2)
    assert store.commit_calls >= 1
    sched.stop()
    # No signal handlers were installed from the worker thread.
    assert sched._prev_handlers == {}


# ───────────────────── P1-4 regression ─────────────────────


def test_tick_runs_wal_checkpoint_on_both_storages(tmp_path: Path) -> None:
    """FlushScheduler must issue PRAGMA wal_checkpoint(PASSIVE) after commit
    on both the project Storage and the global preferences Storage (§7.2)."""
    data_dir = tmp_path / "am"
    store = MemoryStore.open(cwd=tmp_path, data_dir=data_dir)

    ckpt_calls: list[str] = []

    def _make_tracer(label: str):
        def _trace(sql: str) -> None:
            if "wal_checkpoint" in sql.lower():
                ckpt_calls.append(label)
        return _trace

    store._storage._conn.set_trace_callback(_make_tracer("proj"))
    store._pref_storage._conn.set_trace_callback(_make_tracer("pref"))

    try:
        sched = FlushScheduler(store, interval=0.1)
        sched.start()
        store.add_fact("trigger-commit", tags=["t"])
        time.sleep(0.45)
        sched.stop()
    finally:
        # Detach tracers before close.
        store._storage._conn.set_trace_callback(None)
        store._pref_storage._conn.set_trace_callback(None)
        store.close()

    assert "proj" in ckpt_calls, f"project checkpoint not seen: {ckpt_calls}"
    assert "pref" in ckpt_calls, f"pref checkpoint not seen: {ckpt_calls}"

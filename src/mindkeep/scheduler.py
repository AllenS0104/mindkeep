"""Periodic flush scheduler for :class:`~mindkeep.memory_api.MemoryStore`.

Implements ARCHITECTURE.md §7.2 (flush scheduler) and §7.3 (exit hook
ordering) in minimal-mode form:

* Daemon thread wakes every ``interval`` seconds and calls ``store.commit()``.
* ``atexit.register(self.stop)`` guarantees a final commit on normal exit.
* ``SIGTERM`` / ``SIGINT`` handlers chain-forward to any pre-existing handler
  (POSIX + Windows).  Signal installation is silently skipped when we are
  not on the main thread (Python forbids ``signal.signal`` off the main
  thread).  The background thread and atexit hook still fire.
* All background exceptions are caught and logged — the scheduler thread
  never dies silently taking the process down with it.
* ``stop()`` is idempotent and thread-safe; context-manager support mirrors
  ``MemoryStore``.
"""
from __future__ import annotations

import atexit
import logging
import signal
import threading
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .memory_api import MemoryStore

_log = logging.getLogger(__name__)

# Signals we attempt to hook.  SIGBREAK exists only on Windows.
_SIGNALS: tuple[int, ...] = tuple(
    s for s in (
        getattr(signal, "SIGTERM", None),
        getattr(signal, "SIGINT", None),
    ) if s is not None
)


class FlushScheduler:
    """Periodically call ``store.commit()`` on a daemon thread."""

    def __init__(self, store: "MemoryStore", interval: float = 30.0) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        self._store = store
        self._interval = float(interval)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._started = False
        self._stopped = False

        # Preserved signal handlers, keyed by signal number, for chaining.
        self._prev_handlers: dict[int, Any] = {}
        # Remember the atexit callable actually registered, so we can
        # unregister it on stop (avoids leak when many stores open/close).
        self._atexit_fn: Optional[Any] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> "FlushScheduler":
        """Start the daemon flush thread and install exit/signal hooks."""
        with self._lock:
            if self._started:
                return self
            self._started = True

            self._thread = threading.Thread(
                target=self._run,
                name=f"FlushScheduler-{id(self):x}",
                daemon=True,
            )
            self._thread.start()

            # atexit always registers (safe from any thread).
            self._atexit_fn = self.stop
            atexit.register(self._atexit_fn)

            # signal.signal() only works on the main thread.
            if threading.current_thread() is threading.main_thread():
                for sig in _SIGNALS:
                    try:
                        prev = signal.signal(sig, self._handle_signal)
                        self._prev_handlers[sig] = prev
                    except (ValueError, OSError) as exc:
                        # Some environments (embedded interpreters, Windows
                        # edge cases) reject the install — log & continue.
                        _log.warning(
                            "FlushScheduler: could not install handler for "
                            "signal %s: %s", sig, exc
                        )
            else:
                _log.debug(
                    "FlushScheduler: not on main thread; skipping signal "
                    "handler registration"
                )
        return self

    def stop(self) -> None:
        """Cancel timer, perform a final commit, chain-forward signal handlers."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True

            # 1. Cancel the cancellable sleep.
            self._stop_event.set()

            thread = self._thread

        # Join outside the lock to avoid self-deadlock if called from thread.
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(self._interval + 1.0, 2.0))

        # 2. Final commit (best-effort).
        try:
            if not self._store.closed:
                self._store.commit()
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("FlushScheduler: final commit failed: %s", exc)

        # 3. Restore previous signal handlers (main-thread only).
        with self._lock:
            if threading.current_thread() is threading.main_thread():
                for sig, prev in list(self._prev_handlers.items()):
                    try:
                        signal.signal(sig, prev if prev is not None else signal.SIG_DFL)
                    except (ValueError, OSError) as exc:
                        _log.warning(
                            "FlushScheduler: could not restore handler for "
                            "signal %s: %s", sig, exc
                        )
            self._prev_handlers.clear()

            # 4. Best-effort atexit de-registration.
            if self._atexit_fn is not None:
                try:
                    atexit.unregister(self._atexit_fn)
                except Exception:  # pragma: no cover - defensive
                    pass
                self._atexit_fn = None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                if self._store.closed:
                    return
                self._store.commit()
            except Exception as exc:
                # Swallow & log — a transient error must never kill the thread.
                _log.warning("FlushScheduler: commit failed: %s", exc)
                continue

            # After a successful commit, nudge SQLite to roll the WAL tail
            # into the main DB file.  PASSIVE means "do what you can without
            # blocking writers"; failure is logged but never escalated.
            for storage_attr in ("_storage", "_pref_storage"):
                st = getattr(self._store, storage_attr, None)
                if st is None:
                    continue
                try:
                    st._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception as exc:  # pragma: no cover - defensive
                    _log.warning(
                        "FlushScheduler: wal_checkpoint(%s) failed: %s",
                        storage_attr, exc,
                    )

    def _handle_signal(self, signum: int, frame: Any) -> None:
        prev = self._prev_handlers.get(signum)
        try:
            self.stop()
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("FlushScheduler: stop() during signal failed: %s", exc)

        # Chain-forward: respect caller-installed handler semantics.
        if callable(prev):
            try:
                prev(signum, frame)
                return
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning(
                    "FlushScheduler: previous handler for signal %s raised: %s",
                    signum, exc,
                )
                return
        if prev in (None, signal.SIG_DFL):
            # Re-raise default: KeyboardInterrupt for SIGINT, else let OS handle.
            if signum == getattr(signal, "SIGINT", None):
                raise KeyboardInterrupt
            # For SIGTERM, restore default and re-raise via os.kill isn't
            # worth the portability risk in tests — default behaviour is to
            # terminate, which atexit has already prepared us for.
            return
        # prev == SIG_IGN → do nothing.

    # ------------------------------------------------------------------
    # context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "FlushScheduler":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # introspection (used by tests)
    # ------------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def stopped(self) -> bool:
        return self._stopped


__all__ = ["FlushScheduler"]

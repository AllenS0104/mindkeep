"""Child process driver for crash-recovery tests.

Invoked by :mod:`tests.test_crash` via ``subprocess.Popen``.  The child
opens a :class:`mindkeep.memory_api.MemoryStore`, performs some
writes according to ``--mode``, signals readiness by touching a marker
file, then sleeps long enough for the parent to SIGKILL / TerminateProcess
it.

The child *never* closes the store cleanly — the whole point is to
observe what survives a hard kill.  Only Storage.insert's per-row
``conn.commit()`` + WAL durability guarantees should carry data across.

Contract (all paths are absolute):
  --data-dir  PATH   data directory (also exported as MINDKEEP_HOME)
  --cwd       PATH   pretend-cwd that feeds resolve_project_id()
  --count     N      number of facts / adrs to write
  --mode      NAME   one of:
                       no-op-commit     ── write N facts, explicit .commit(), sleep
                       auto-only        ── write N facts, no explicit commit, sleep
                       scheduler        ── MemoryStore.open(auto_flush_interval=0.2),
                                           write N, wait > interval, sleep
                       concurrent       ── 4 threads writing in a tight loop until killed
                       mixed            ── write facts + an ADR + a preference, commit, sleep
  --ready     PATH   marker file touched once writes are finished
  --sleep     SEC    seconds to sleep after signalling ready (default 30)
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--mode", required=True)
    p.add_argument("--ready", required=True)
    p.add_argument("--sleep", type=float, default=30.0)
    p.add_argument("--tag", default="crash")
    return p.parse_args()


def _touch(path: Path) -> None:
    path.write_text(str(os.getpid()), encoding="utf-8")


def main() -> int:
    args = _parse_args()

    data_dir = Path(args.data_dir).resolve()
    cwd = Path(args.cwd).resolve()
    ready = Path(args.ready)

    # Isolate the data directory so the child never touches real user data.
    os.environ["MINDKEEP_HOME"] = str(data_dir)

    # Force the resolved project id to match whatever the parent uses —
    # we pass cwd explicitly instead of relying on process-wide os.chdir().
    from mindkeep.memory_api import MemoryStore

    if args.mode == "scheduler":
        store = MemoryStore.open(cwd=cwd, data_dir=data_dir,
                                 auto_flush_interval=0.2)
    else:
        store = MemoryStore.open(cwd=cwd, data_dir=data_dir)

    try:
        if args.mode == "auto-only":
            # Each add_fact auto-commits under Storage.insert, so this
            # covers "no explicit flush" — we're verifying that the
            # implicit per-row commit is still durable over SIGKILL.
            for i in range(args.count):
                store.add_fact(f"auto-{i}", tags=[args.tag])

        elif args.mode == "no-op-commit":
            for i in range(args.count):
                store.add_fact(f"cm-{i}", tags=[args.tag])
            store.commit()

        elif args.mode == "scheduler":
            for i in range(args.count):
                store.add_fact(f"sch-{i}", tags=[args.tag])
            # Allow at least one scheduler tick (interval=0.2) to fire.
            time.sleep(0.6)

        elif args.mode == "mixed":
            for i in range(args.count):
                store.add_fact(f"mx-fact-{i}", tags=[args.tag])
            store.add_adr(
                title="crash-mixed",
                decision="dec",
                rationale="ctx",
                status="accepted",
                tags=[args.tag],
            )
            store.set_preference("mx-pref", "v")
            store.commit()

        elif args.mode == "concurrent":
            stop = threading.Event()
            written = [0]
            written_lock = threading.Lock()

            def _writer(tid: int) -> None:
                local = 0
                while not stop.is_set():
                    try:
                        store.add_fact(f"cc-{tid}-{local}", tags=[args.tag])
                    except Exception:
                        return
                    local += 1
                    with written_lock:
                        written[0] += 1

            threads = [threading.Thread(target=_writer, args=(t,),
                                        daemon=True) for t in range(4)]
            for t in threads:
                t.start()
            # Let the writers ramp up before we signal ready; the parent
            # will SIGKILL some time after that.
            while True:
                with written_lock:
                    if written[0] >= args.count:
                        break
                time.sleep(0.005)

        else:
            raise SystemExit(f"unknown mode {args.mode!r}")

        _touch(ready)
        sys.stdout.write("READY\n")
        sys.stdout.flush()
        # Block long enough that the parent definitely kills us first.
        time.sleep(args.sleep)

    finally:
        # If we ever got here without being killed, close cleanly so
        # the leftover subprocess doesn't hold the DB open.
        try:
            store.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

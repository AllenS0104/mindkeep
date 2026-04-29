"""Session-level token budget tracking (P0-2, issue #7).

Per-shell-session JSON state file recording cumulative token spend.
Schema::

    {"version": 1, "spent": <int>, "started_at": "<iso>",
     "last_call": "<iso>", "calls": <int>}

The PID used in the filename is the immediate parent of the mindkeep
process (``os.getppid()``). That isn't always the *top* shell — if the
user pipes through subshells the budget will scope to the innermost
shell. This is a deliberate simplification; see issue #7. When the
parent shell exits the file orphans naturally and ``mindkeep session
reset`` (or OS temp cleanup) removes it.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._tokens import estimate as estimate_tokens

_ENV_BUDGET = "MINDKEEP_SESSION_BUDGET"
_SCHEMA_VERSION = 1


def _state_dir() -> Path:
    """Return the per-user runtime directory for session state files.

    Linux/macOS: ``$XDG_RUNTIME_DIR/mindkeep`` (fallback ``/tmp/mindkeep``).
    Windows:   ``%LOCALAPPDATA%\\Temp\\mindkeep``.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Temp" / "mindkeep"
        return Path(os.environ.get("TEMP", ".")) / "mindkeep"
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "mindkeep"


def _shell_pid() -> int:
    """Return the PID used to namespace the session file.

    Currently just ``os.getppid()`` — the immediate parent. If the user
    runs mindkeep through nested subshells the budget scopes to the
    innermost shell rather than the top-level terminal. See issue #7.
    """
    return os.getppid()


def state_path() -> Path:
    return _state_dir() / f"session-{_shell_pid()}.json"


def _budget() -> int:
    raw = os.environ.get(_ENV_BUDGET, "").strip()
    if not raw:
        return 0
    try:
        n = int(raw)
    except ValueError:
        return 0
    return max(0, n)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> dict[str, Any]:
    now = _now_iso()
    return {
        "version": _SCHEMA_VERSION,
        "spent": 0,
        "started_at": now,
        "last_call": now,
        "calls": 0,
    }


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return _empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict) or data.get("version") != _SCHEMA_VERSION:
        return _empty_state()
    base = _empty_state()
    base.update({k: data.get(k, base[k]) for k in base})
    return base


def _save_state(state: dict[str, Any]) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # Best-effort — never fail the user's command on bookkeeping.
        pass


def status() -> dict[str, Any]:
    """Return a snapshot of the current session state plus budget."""
    s = load_state()
    s["budget"] = _budget()
    s["path"] = str(state_path())
    return s


def reset() -> bool:
    """Delete the current session state file. Returns True if removed."""
    p = state_path()
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def check_and_record(text: str) -> tuple[bool, dict[str, Any]]:
    """Decide whether *text* may be emitted under the current budget.

    Always increments ``calls`` and updates ``last_call``. If the call
    would fit (or no budget is set), also adds the estimate to ``spent``
    and returns ``(True, state)``. If it would overflow, ``spent`` is
    left untouched and ``(False, state)`` is returned — the caller
    should suppress output and emit a stderr notice.
    """
    state = load_state()
    estimate = estimate_tokens(text)
    budget = _budget()
    state["calls"] = int(state.get("calls", 0)) + 1
    state["last_call"] = _now_iso()
    allowed = True
    if budget > 0 and state["spent"] + estimate >= budget:
        allowed = False
    else:
        state["spent"] = int(state.get("spent", 0)) + estimate
    _save_state(state)
    state["budget"] = budget
    state["estimate"] = estimate
    return allowed, state


def record_session_spend(text: str) -> tuple[bool, dict[str, Any]]:
    """Public hook for sibling commands (e.g. recall) to debit budget.

    Same semantics as :func:`check_and_record`.
    """
    return check_and_record(text)


def emit_or_suppress(text: str, *, stream=None) -> bool:
    """Emit *text* on stdout if the budget allows, else print a notice.

    Returns True if the text was printed, False if suppressed.
    """
    allowed, state = check_and_record(text)
    if allowed:
        out = stream if stream is not None else sys.stdout
        out.write(text)
        if not text.endswith("\n"):
            out.write("\n")
        return True
    sys.stderr.write(
        f"[mindkeep] session budget reached: "
        f"spent={state['spent']} budget={state['budget']}. "
        f"Use {_ENV_BUDGET}=0 to disable, or start a new session.\n"
    )
    return False

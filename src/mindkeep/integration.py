"""Agent integration hooks — convenience façade over :class:`MemoryStore`.

Provides the three helpers agents are expected to use directly:

* :func:`load_project_memory` — open a ``MemoryStore`` with safe defaults
  (SecretsRedactor + SizeLimiter installed when the ``security`` module is
  available) and an optional background auto-flush thread.
* :func:`save_decision` — thin wrapper around ``add_adr(status="accepted")``.
* :func:`recall` — multi-kind snapshot, optionally tag-filtered.

This module keeps the ``security`` dependency **soft**: a parallel agent
is authoring it; if it isn't importable yet, we degrade gracefully to no
filters and emit a stdlib ``warnings`` message.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

from .memory_api import MemoryStore

__all__ = ["load_project_memory", "save_decision", "recall"]

_log = logging.getLogger(__name__)

# Default auto-flush interval (seconds); matches ARCHITECTURE.md §7.2.
_AUTO_FLUSH_INTERVAL = 30.0


# ──────────────────────── soft security import ────────────────────────


def _try_import_filters() -> list[Any]:
    """Return a list of default filter instances, or ``[]`` if unavailable.

    The ``security`` module is being authored by a parallel agent. We catch
    *any* import-time problem (ImportError, AttributeError, surface bugs)
    and degrade to no filters rather than crashing the caller.
    """
    try:
        from . import security  # type: ignore[attr-defined]
    except ImportError:
        warnings.warn(
            "mindkeep.security module not available — "
            "load_project_memory() is running WITHOUT SecretsRedactor/SizeLimiter. "
            "Avoid writing anything sensitive until the module lands.",
            stacklevel=3,
        )
        return []

    filters: list[Any] = []
    redactor = getattr(security, "SecretsRedactor", None)
    limiter = getattr(security, "SizeLimiter", None)
    try:
        if redactor is not None:
            filters.append(redactor())
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("SecretsRedactor construction failed: %s", exc)
    try:
        if limiter is not None:
            filters.append(limiter())
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("SizeLimiter construction failed: %s", exc)
    return filters


# ──────────────────────── public helpers ────────────────────────


def load_project_memory(
    cwd: Path | None = None,
    *,
    auto_flush: bool = True,
    data_dir: Path | None = None,
) -> MemoryStore:
    """Open the per-project ``MemoryStore`` with squad-wide safe defaults.

    * Installs ``SecretsRedactor`` + ``SizeLimiter`` when the ``security``
      module is importable; otherwise warns and proceeds filter-less.
    * When ``auto_flush=True`` (default), starts the official
      :class:`~mindkeep.scheduler.FlushScheduler` inside
      ``MemoryStore.open`` (interval=30s) — commits pending writes so a
      crash doesn't lose the WAL tail.
    * Returns a live ``MemoryStore``; the caller is responsible for
      ``close()`` (or use it as a context manager).
    """
    filters = _try_import_filters()
    interval = _AUTO_FLUSH_INTERVAL if auto_flush else None
    return MemoryStore.open(
        cwd=cwd,
        data_dir=data_dir,
        filters=filters,
        auto_flush_interval=interval,
    )


def save_decision(
    store: MemoryStore,
    title: str,
    decision: str,
    rationale: str = "",
    tags: list[str] | None = None,
) -> int:
    """Record an accepted ADR; returns the new rowid.

    Thin convenience wrapper around :meth:`MemoryStore.add_adr` so agents
    have a one-line "save decision" call-site that matches the protocol
    doc's example snippets.
    """
    if not title or not title.strip():
        raise ValueError("save_decision: title must be a non-empty string")
    if not decision or not decision.strip():
        raise ValueError("save_decision: decision must be a non-empty string")
    return store.add_adr(
        title=title,
        decision=decision,
        rationale=rationale,
        status="accepted",
        tags=list(tags) if tags else None,
    )


def recall(
    store: MemoryStore,
    topic: str | None = None,
    *,
    fact_limit: int = 100,
    session_limit: int = 10,
) -> dict[str, Any]:
    """Return a consolidated view of what the store knows.

    Keys are always present (empty list / dict if nothing stored):

    * ``facts`` — list of fact rows (newest first)
    * ``adrs`` — list of ADR rows (number ascending)
    * ``preferences`` — ``{key: value}`` mapping (all keys, cross-project)
    * ``recent_sessions`` — list of session-summary rows (newest first)

    When ``topic`` is given, ``facts`` and ``adrs`` are filtered to rows
    whose ``tags`` contain that exact tag (case-sensitive). ``preferences``
    and ``recent_sessions`` are unaffected — they don't carry tags.
    """
    if topic is not None:
        facts = store.list_facts(tag=topic, limit=fact_limit)
        all_adrs = store.list_adrs()
        adrs = [a for a in all_adrs if topic in _parse_tags(a.get("tags", ""))]
    else:
        facts = store.list_facts(limit=fact_limit)
        adrs = store.list_adrs()

    prefs_rows = store.list_preferences()
    preferences = {row["key"]: row["value"] for row in prefs_rows}

    return {
        "facts": facts,
        "adrs": adrs,
        "preferences": preferences,
        "recent_sessions": store.recent_sessions(limit=session_limit),
    }


def _parse_tags(raw: str) -> list[str]:
    """Split the schema's comma-joined tag string back to a list."""
    if not raw:
        return []
    return [t for t in raw.split(",") if t]

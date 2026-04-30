"""High-level ``MemoryStore`` API — business-semantic layer.

Composes :class:`~mindkeep.storage.Storage` with a resolved
:class:`~mindkeep.models.ProjectId` and a pluggable ``Filter`` pipeline
(ARCHITECTURE.md §8 hook, minimal shape).

This is the "minimal mode" implementation authorised by the user: the
surface is the subset of ARCHITECTURE.md §6 the agent squad actually needs
to ship end-to-end persistence:

    add_fact / list_facts
    add_adr  / list_adrs
    set_preference / get_preference     (upsert semantics)
    add_session_summary / recent_sessions
    clear(kinds=None | list[str])
    commit / close (+ context manager)

Design notes
------------
* Filters. A ``Filter`` is any object with ``apply(kind, field, value) -> str``.
  Values flow through the filter list in order; each filter may rewrite the
  string. Empty filter list is a no-op.
* Timestamps. All ``created_at`` / ``updated_at`` / ``ended_at`` columns are
  stored as ISO-8601 UTC strings with second precision.
* Schema reuse. The underlying SQLite schema (owned by ``Storage``) carries
  more columns than the minimal API exposes; unused columns are populated
  with safe defaults (empty strings, ``confidence=1.0`` etc.).
* Preference upsert. ``Storage`` only exposes insert/query/delete, so upsert
  is implemented as "query → (optional delete) → insert", preserving the
  original ``created_at`` on conflict.
* ``clear()``. ``Storage.delete`` refuses filter-less deletes, so we iterate
  rows and delete by primary key. This is O(n) but fine for the expected
  dataset size (≤ 10⁴ rows) and avoids bypassing the storage abstraction.
* Write guard (P1-7). Every ``add_fact`` / ``add_adr`` call estimates token
  count after redaction and rejects writes whose post-redaction text exceeds
  a per-kind cap (facts: 100, ADRs: 1500; both env-overridable). A second
  threshold at 2× cap rejects huge pre-redaction blobs outright. Pass
  ``force=True`` to bypass.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from .models import ProjectId
from .project_id import resolve_project_id
from .storage import Storage, WriteGuardError, default_data_dir

# Token estimator — prefer the shared helper from #7 when it lands; fall
# back to a 4-chars-per-token heuristic so this module is self-contained
# until the sibling agent's ``_tokens`` module ships.
try:  # pragma: no cover - exercised once #7 lands
    from ._tokens import estimate as _estimate_tokens
except ImportError:  # pragma: no cover - default path today
    def _estimate_tokens(s: str) -> int:  # TODO(#7): replace with shared helper
        return max(1, len(s) // 4) if s else 0

# ──────────────────────────── filter protocol ────────────────────────────


@runtime_checkable
class Filter(Protocol):
    """Minimal filter hook (ARCHITECTURE.md §8, simplified).

    Implementations receive ``(kind, field, value)`` and return the
    (possibly rewritten) value. Raise to reject outright.

    * ``kind`` — logical record type: ``"fact"``, ``"adr"``, ``"session"``,
      ``"preference"``.
    * ``field`` — which text field within the record
      (e.g. ``"content"``, ``"decision"``, ``"rationale"``, ``"summary"``,
      ``"value"``).
    * ``value`` — the raw string about to be written.
    """

    def apply(self, kind: str, field: str, value: str) -> str:  # pragma: no cover - protocol
        ...


# ──────────────────────────── helpers ────────────────────────────

# Business tables in deterministic order (used by ``clear(None)``).
# Project-local tables only — ``preferences`` lives in the global DB
# (``<data_dir>/preferences.db``, see ARCHITECTURE.md §3.2 / §4.1) and is
# cleared via the dedicated pref_storage path.
_BUSINESS_TABLES: tuple[str, ...] = (
    "facts",
    "adrs",
    "session_summaries",
    "preferences",
)

# Reserved filename stem for the cross-project preferences DB.
_PREFS_DB_STEM = "preferences"

# Map user-facing "kind" names to internal table names.
_KIND_TO_TABLE: dict[str, str] = {
    "facts": "facts",
    "fact": "facts",
    "adrs": "adrs",
    "adr": "adrs",
    "sessions": "session_summaries",
    "session": "session_summaries",
    "session_summaries": "session_summaries",
    "preferences": "preferences",
    "preference": "preferences",
}


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tags_to_str(tags: Sequence[str] | None) -> str:
    """Encode a tag list as a comma-joined string (schema stores TEXT)."""
    if not tags:
        return ""
    # Strip whitespace, drop empties, preserve order.
    cleaned = [t.strip() for t in tags if t and t.strip()]
    return ",".join(cleaned)


def _tags_from_str(raw: str) -> list[str]:
    if not raw:
        return []
    return [t for t in raw.split(",") if t]


# ─────────────────────────── write-guard helpers (P1-7) ───────────────────────────

# Default token caps; overridable via env vars. Resolved per-call so tests
# that ``monkeypatch.setenv`` see fresh values without re-importing.
_DEFAULT_CAPS: dict[str, int] = {"fact": 100, "adr": 1500}
_CAP_ENV: dict[str, str] = {
    "fact": "MINDKEEP_FACTS_TOKEN_CAP",
    "adr": "MINDKEEP_ADRS_TOKEN_CAP",
}


def _resolve_cap(kind: str) -> int:
    raw = os.environ.get(_CAP_ENV[kind])
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_CAPS[kind]


def _enforce_write_guard(
    kind: str,
    *,
    pre: str,
    post: str,
    force: bool,
) -> int:
    """Apply the dual-threshold guard. Returns the post-redaction token estimate.

    * Estimates tokens of both ``pre`` (pre-redaction) and ``post``
      (post-redaction) text.
    * Raises :class:`WriteGuardError` if ``post`` exceeds the cap, or if
      ``pre`` exceeds 2× cap (the "huge unstructured blob" guard).
    * Logs a stderr warning when redaction shrank the content by >50%
      *and* the original was substantial (signals a nearly-secrets-only
      payload — still allowed).
    * ``force=True`` bypasses both rejections (warnings still print).
    """
    cap = _resolve_cap(kind)
    pre_tokens = _estimate_tokens(pre)
    post_tokens = _estimate_tokens(post)

    plural = "facts" if kind == "fact" else "adrs"

    if not force:
        if pre_tokens > 2 * cap:
            raise WriteGuardError(
                f"{plural} pre-redaction content is {pre_tokens} tokens, "
                f"exceeding 2× the {plural} cap ({2 * cap}); refusing to "
                f"accept what looks like a large unstructured blob. "
                f"Pass force=True to override.",
                kind=kind,
                cap=cap,
                post_tokens=post_tokens,
                pre_tokens=pre_tokens,
            )
        if post_tokens > cap:
            raise WriteGuardError(
                f"{plural} post-redaction content is {post_tokens} tokens, "
                f"exceeding the {plural} cap ({cap}). Trim the input or "
                f"pass force=True to override.",
                kind=kind,
                cap=cap,
                post_tokens=post_tokens,
                pre_tokens=pre_tokens,
            )

    # Shrinkage warning — only when redaction actually removed content
    # and the original was substantial (otherwise a 250-token blob of
    # pure secrets that redacts to ~10 tokens is just "redaction did its
    # job" and the warning would be noise). The threshold is half the
    # cap: at that point the reduction is large in absolute terms too.
    if (
        pre_tokens > cap // 2
        and post_tokens < pre_tokens
        and post_tokens <= cap
        and (pre_tokens - post_tokens) * 2 > pre_tokens
    ):
        print(
            f"mindkeep: redaction trimmed {kind} from "
            f"{pre_tokens} → {post_tokens} tokens (>50% reduction); "
            f"consider re-checking the input for accidentally-pasted secrets.",
            file=sys.stderr,
        )

    return post_tokens


# ──────────────────────────── MemoryStore ────────────────────────────


class MemoryStore:
    """Business-semantic wrapper around :class:`Storage` + ``ProjectId``."""

    def __init__(
        self,
        project_id: ProjectId,
        storage: Storage,
        filters: Sequence[Filter] | None = None,
        pref_storage: Storage | None = None,
    ) -> None:
        self._project_id = project_id
        self._storage = storage
        # Global preferences DB (shared across all projects).  Optional
        # for backwards compatibility with ad-hoc callers in the CLI —
        # when ``None`` the project-local ``preferences`` table is used
        # as a fallback so pre-existing rows remain visible.
        self._pref_storage = pref_storage
        self._filters: list[Filter] = list(filters) if filters else []
        self._closed = False
        # Persist project identity (display_name, id_source, origin) into
        # the per-project DB's ``meta`` table on every open. This way the
        # CLI's ``list`` subcommand (and the sidecar written on close) can
        # display a human-readable project name — not just the 12-char
        # hash. Skipped when the caller hands us a placeholder ProjectId
        # (e.g. the CLI's ``clear`` path with display_name="").
        if project_id.display_name:
            try:
                self._storage.set_project_meta(
                    display_name=project_id.display_name,
                    id_source=project_id.source,
                    origin_value=project_id.origin,
                )
            except Exception:  # pragma: no cover - defensive
                # Identity-stamp failures must never prevent MemoryStore use.
                pass
        # Optional periodic flush scheduler; wired up by open() when
        # ``auto_flush_interval`` is supplied.
        self._scheduler: Any | None = None
        # Serialises add_adr's read-max-then-insert sequence (P1-1).
        self._adr_lock = threading.Lock()

    # ---- construction ------------------------------------------------

    @classmethod
    def open(
        cls,
        cwd: Path | None = None,
        data_dir: Path | None = None,
        filters: Sequence[Filter] | None = None,
        auto_flush_interval: float | None = None,
    ) -> "MemoryStore":
        """Open (or create) the per-project DB under ``data_dir``.

        * ``cwd`` — used to resolve the ``ProjectId``. Defaults to
          ``Path.cwd()`` (see :func:`resolve_project_id`).
        * ``data_dir`` — root directory for DB files. Defaults to the
          OS-appropriate application data directory.
        * ``filters`` — optional filter pipeline, applied to every text
          write.
        * ``auto_flush_interval`` — if set, start a background
          :class:`~mindkeep.scheduler.FlushScheduler` that calls
          :meth:`commit` every N seconds.  ``None`` (default) preserves
          legacy behaviour of no background thread.
        """
        pid = resolve_project_id(cwd)
        base = Path(data_dir) if data_dir is not None else default_data_dir()
        storage = Storage(pid.id, data_dir=base)
        # Global preferences DB — its project_hash is a fixed reserved
        # string so every project on this machine hits the same file.
        pref_storage = Storage(_PREFS_DB_STEM, data_dir=base)
        store = cls(pid, storage, filters, pref_storage=pref_storage)
        if auto_flush_interval is not None:
            # Local import to avoid a circular import at module load time.
            from .scheduler import FlushScheduler

            store._scheduler = FlushScheduler(store, interval=auto_flush_interval)
            store._scheduler.start()
        return store

    # ---- properties --------------------------------------------------

    @property
    def project_id(self) -> ProjectId:
        """Resolved ``ProjectId`` for this store."""
        return self._project_id

    @property
    def db_path(self) -> Path:
        """Path of the underlying SQLite file."""
        return self._storage.db_path

    # ---- filter pipeline --------------------------------------------

    def _run_filters(self, kind: str, field: str, value: str) -> str:
        out = value
        for f in self._filters:
            out = f.apply(kind, field, out)
            if not isinstance(out, str):
                raise TypeError(
                    f"filter {type(f).__name__}.apply must return str, got "
                    f"{type(out).__name__}"
                )
        return out

    # ---- facts -------------------------------------------------------

    def add_fact(
        self,
        content: str,
        tags: list[str] | None = None,
        source: str | None = None,
        *,
        force: bool = False,
        pin: bool = False,
    ) -> int:
        """Persist a free-form fact; returns the new rowid.

        Pass ``force=True`` to bypass the post-redaction token cap
        (default 100, override via ``MINDKEEP_FACTS_TOKEN_CAP``).
        ``pin=True`` marks the fact so it surfaces first in
        :meth:`list_facts` (P1-8 / issue #13).
        """
        original = content
        content = self._run_filters("fact", "content", content)
        token_estimate = _enforce_write_guard(
            "fact", pre=original, post=content, force=force
        )
        now = _now_iso()
        # Synthetic UNIQUE key — the minimal API treats facts as an append-only
        # log keyed by content rather than a natural key space.
        key = f"fact-{uuid.uuid4().hex[:12]}"
        return self._storage.insert(
            "facts",
            {
                "key": key,
                "value": content,
                "tags": _tags_to_str(tags),
                "source": source if source is not None else "agent",
                "confidence": 1.0,
                "token_estimate": token_estimate,
                "pin": 1 if pin else 0,
                "created_at": now,
                "updated_at": now,
            },
        )

    def list_facts(
        self,
        tag: str | None = None,
        limit: int = 100,
        *,
        pinned_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return facts ordered ``pin DESC, created_at DESC`` (P1-8).

        * ``tag``         — keep only rows whose tag list contains ``tag``.
        * ``limit``       — cap on returned rows.
        * ``pinned_only`` — return only rows with ``pin = 1``.
        """
        rows = self._storage.query("facts")
        if pinned_only:
            rows = [r for r in rows if int(r.get("pin") or 0) == 1]
        rows.sort(
            key=lambda r: (
                int(r.get("pin") or 0),
                r.get("created_at", ""),
                int(r.get("id") or 0),
            ),
            reverse=True,
        )
        if tag is not None:
            rows = [r for r in rows if tag in _tags_from_str(r["tags"])]
        return rows[: max(0, int(limit))]

    def pin_fact(self, fact_id: int) -> None:
        """Set ``pin = 1`` on the fact with id ``fact_id`` (P1-8)."""
        self._set_pin("facts", int(fact_id), 1)

    def unpin_fact(self, fact_id: int) -> None:
        """Clear ``pin`` on the fact with id ``fact_id`` (P1-8)."""
        self._set_pin("facts", int(fact_id), 0)

    def _set_pin(self, table: str, row_id: int, value: int) -> None:
        n = self._storage.update(
            table,
            where={"id": row_id},
            values={"pin": value, "updated_at": _now_iso()},
        )
        if n == 0:
            kind = "fact" if table == "facts" else "adr"
            raise ValueError(f"{kind} id {row_id} not found")

    # ---- ADRs --------------------------------------------------------

    def add_adr(
        self,
        title: str,
        decision: str,
        rationale: str,
        status: str = "accepted",
        supersedes: int | None = None,
        tags: list[str] | None = None,
        *,
        force: bool = False,
        pin: bool = False,
    ) -> int:
        """Record an Architecture Decision; returns the new rowid.

        The ADR ``number`` is auto-assigned as ``max(number) + 1``.
        ``rationale`` is stored in the schema's ``context`` column.

        ``pin=True`` marks the ADR so it surfaces first in
        :meth:`list_adrs` (P1-8 / issue #13).

        The read-max / insert sequence is serialised per-store with an
        ``RLock`` so concurrent callers never observe the same
        ``max(number)`` and land on duplicate numbers (P1-1).

        Pass ``force=True`` to bypass the post-redaction token cap
        (default 1500, override via ``MINDKEEP_ADRS_TOKEN_CAP``). The cap
        is checked against the combined ``title + decision + rationale``
        text — the same body a downstream consumer would render.
        """
        original_decision = decision
        original_rationale = rationale
        decision = self._run_filters("adr", "decision", decision)
        rationale = self._run_filters("adr", "rationale", rationale)
        # Combine into the body the cap actually applies to. ``title`` is
        # not redacted (no field hook in the legacy pipeline) so it
        # contributes equally to both pre- and post-redaction estimates.
        pre_body = f"{title}\n\n{original_decision}\n\n{original_rationale}"
        post_body = f"{title}\n\n{decision}\n\n{rationale}"
        token_estimate = _enforce_write_guard(
            "adr", pre=pre_body, post=post_body, force=force
        )
        now = _now_iso()
        with self._adr_lock:
            existing = self._storage.query("adrs")
            next_number = max((int(r["number"]) for r in existing), default=0) + 1
            return self._storage.insert(
                "adrs",
                {
                    "number": next_number,
                    "title": title,
                    "status": status,
                    "context": rationale,
                    "decision": decision,
                    "alternatives": "",
                    "consequences": "",
                    "supersedes": supersedes,
                    "tags": _tags_to_str(tags),
                    "token_estimate": token_estimate,
                    "pin": 1 if pin else 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    def list_adrs(
        self,
        status: str | None = None,
        *,
        pinned_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return ADRs ordered ``pin DESC, created_at DESC`` (P1-8).

        * ``status``      — equality-filter on the ADR status column.
        * ``pinned_only`` — return only rows with ``pin = 1``.
        """
        if status is not None:
            rows = self._storage.query("adrs", status=status)
        else:
            rows = self._storage.query("adrs")
        if pinned_only:
            rows = [r for r in rows if int(r.get("pin") or 0) == 1]
        rows.sort(
            key=lambda r: (
                int(r.get("pin") or 0),
                r.get("created_at", ""),
                int(r.get("id") or 0),
            ),
            reverse=True,
        )
        return rows

    def pin_adr(self, adr_id: int) -> None:
        """Set ``pin = 1`` on the ADR with id ``adr_id`` (P1-8)."""
        self._set_pin("adrs", int(adr_id), 1)

    def unpin_adr(self, adr_id: int) -> None:
        """Clear ``pin`` on the ADR with id ``adr_id`` (P1-8)."""
        self._set_pin("adrs", int(adr_id), 0)

    # ---- preferences (upsert, cross-project) -------------------------

    def _prefs(self) -> Storage:
        """Return the storage that owns the preferences table.

        Falls back to the project-local Storage when no global pref
        storage was injected (legacy direct ``MemoryStore(...)`` callers).
        """
        return self._pref_storage if self._pref_storage is not None else self._storage

    def set_preference(
        self, key: str, value: str, scope: str = "project"
    ) -> None:
        """Insert or update a preference by ``key``.

        Preferences are stored in the **global** ``preferences.db`` and
        therefore visible across projects (ARCHITECTURE.md §3.2).  Uses a
        single ``INSERT ... ON CONFLICT(key) DO UPDATE`` so concurrent
        writers never race between lookup and insert (P1-2).
        On conflict, ``created_at`` is preserved and ``updated_at`` refreshed.
        """
        value = self._run_filters("preference", "value", value)
        now = _now_iso()
        self._prefs().upsert(
            "preferences",
            {
                "key": key,
                "value": value,
                "scope": scope,
                "created_at": now,
                "updated_at": now,
            },
            conflict_cols=("key",),
        )

    def get_preference(
        self, key: str, default: str | None = None
    ) -> str | None:
        """Return the preference value for ``key`` or ``default``."""
        rows = self._prefs().query("preferences", key=key)
        if not rows:
            return default
        return rows[0]["value"]

    def list_preferences(
        self, *, prefix: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all preferences (newest first), optionally prefix-filtered."""
        rows = self._prefs().query("preferences")
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        if prefix:
            rows = [r for r in rows if str(r.get("key", "")).startswith(prefix)]
        return rows

    # ---- session summaries ------------------------------------------

    def add_session_summary(
        self,
        summary: str,
        started_at: str,
        ended_at: str,
        turn_count: int = 0,
    ) -> int:
        """Record a session summary; returns the new rowid.

        ``turn_count`` is persisted inside the schema's ``refs`` JSON column
        (the minimal API does not expose the richer refs list from §6).
        """
        summary = self._run_filters("session", "summary", summary)
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        return self._storage.insert(
            "session_summaries",
            {
                "session_id": session_id,
                "summary": summary,
                "files_touched": "",
                "refs": json.dumps({"turn_count": int(turn_count)}),
                "started_at": started_at,
                "ended_at": ended_at,
                "created_at": now,
            },
        )

    def recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent sessions by ``ended_at`` descending."""
        rows = self._storage.query("session_summaries")
        rows.sort(key=lambda r: r["ended_at"], reverse=True)
        return rows[: max(0, int(limit))]

    # ---- clear -------------------------------------------------------

    def clear(self, kinds: list[str] | None = None) -> int:
        """Delete all rows from selected business tables.

        * ``kinds=None`` → wipe all four business tables (``meta`` preserved).
        * Otherwise ``kinds`` is a list containing any of
          ``"facts" / "adrs" / "sessions" / "preferences"``.

        Returns the total number of rows removed.
        """
        if kinds is None:
            tables: list[str] = list(_BUSINESS_TABLES)
        else:
            tables = []
            for k in kinds:
                if k not in _KIND_TO_TABLE:
                    raise ValueError(
                        f"unknown kind {k!r}; allowed: "
                        f"{sorted(set(_KIND_TO_TABLE))}"
                    )
                t = _KIND_TO_TABLE[k]
                if t not in tables:
                    tables.append(t)

        total = 0
        for table in tables:
            target = self._prefs() if table == "preferences" else self._storage
            rows = target.query(table)
            for r in rows:
                total += target.delete(table, id=r["id"])
        return total

    # ---- lifecycle ---------------------------------------------------

    def commit(self) -> None:
        """Force a SQLite commit on the underlying connection(s)."""
        if self._closed:
            raise RuntimeError("MemoryStore is closed")
        self._storage.commit()
        if self._pref_storage is not None:
            self._pref_storage.commit()

    def close(self) -> None:
        """Idempotent shutdown; delegates to the underlying ``Storage``."""
        if self._closed:
            return
        # Stop the scheduler first (guarantees a final commit before we
        # tear down the connection).
        sched = self._scheduler
        self._scheduler = None
        if sched is not None:
            try:
                sched.stop()
            except Exception:  # pragma: no cover - defensive
                pass
        self._closed = True
        self._storage.close()
        if self._pref_storage is not None:
            try:
                self._pref_storage.close()
            except Exception:  # pragma: no cover - defensive
                pass

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- friendly aliases -----------------------------------
    # Identical to the add_*/list_* methods above — same signatures,
    # same return types, same behavior. Provided so agent-facing code
    # can use the more natural remember_*/recall_* voice.
    remember_fact = add_fact
    remember_adr = add_adr
    recall_facts = list_facts
    recall_adrs = list_adrs


__all__ = ["MemoryStore", "Filter"]

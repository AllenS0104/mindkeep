"""MCP tool registry and read-tool handlers (#35).

Tool descriptors are :class:`ToolSpec` instances appended to
:data:`TOOLS` at import time. The skeleton ``register`` decorator
remains identity-preserving so existing tests (and any ad-hoc handlers
#34's write-tool work registers) keep working.

This module MUST NOT import the ``mcp`` SDK at top level. The SDK is
imported lazily inside :mod:`mindkeep.mcp.server` when the server
actually starts (DESIGN-v0.4.0 §7.1). Handlers here return plain
Python data; the server adapter wraps it in MCP envelope types.

stdio-stdout invariant (DESIGN §3.4): no handler in this module — and
nothing it calls (``MemoryStore``, ``collect_stats``,
``collect_doctor``, filters) — touches ``sys.stdout``. All diagnostics
go to stderr.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List

from ..memory_api import MemoryStore


__all__ = [
    "TOOLS",
    "ToolSpec",
    "register",
    "build_read_tools",
    # limits
    "RECALL_TOP_MAX",
    "LIST_LIMIT_MAX",
]


# Server-side hard caps (DESIGN-v0.4.0 §13). The JSON-Schema in each
# ToolSpec also advertises these as ``maximum``, but a misbehaving
# client can still submit larger values; the handlers clamp.
RECALL_TOP_MAX = 50
LIST_LIMIT_MAX = 200


@dataclass
class ToolSpec:
    """Descriptor for a single MCP tool.

    Attributes
    ----------
    name:
        Stable tool name as exposed over the wire (e.g.
        ``mindkeep_recall``).
    description:
        Model-facing intent guide. 1–3 sentences; names *when to use*
        AND *when NOT to use* (DESIGN-v0.4.0 §3.5/§3.6).
    input_schema:
        JSON-Schema dict for the tool's arguments. Even when the host
        validates the schema, handlers re-clamp numeric maxima — see
        the per-tool docstrings.
    handler:
        Callable ``(store: MemoryStore, **kwargs) -> Any`` that returns
        the structured result. Must be stdout-clean.
    mode:
        ``"read"`` or ``"write"``. ``--allow-writes`` gates registration
        of write tools; read tools are always available.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]
    mode: str = "read"

    # Conveniences mirroring mcp.types.Tool field naming so this object
    # can be inspected directly in tests without a full SDK conversion.
    @property
    def inputSchema(self) -> dict:  # noqa: N802 - matches MCP camelCase
        return self.input_schema


# List of registered tool descriptors. Populated by build_read_tools()
# (and #34's analogous build_write_tools()). The empty default lets the
# skeleton tests still exercise ``register`` with a plain callable.
TOOLS: List[Any] = []


def register(spec: Any) -> Any:
    """Append ``spec`` to :data:`TOOLS` and return it unchanged.

    Identity-preserving so callables and :class:`ToolSpec` instances can
    both flow through (the skeleton test in #33 uses a plain function).
    """
    TOOLS.append(spec)
    return spec


# ---------------------------------------------------------------- helpers

def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp ``value`` into ``[lo, hi]``."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _serialize_fact(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``facts`` row into the documented MCP shape.

    Tags are returned as a list (storage stores a comma-joined string).
    Internal-only columns (``archived_at`` etc.) are dropped to keep
    the payload compact (DESIGN §13).
    """
    from ..memory_api import _tags_from_str

    return {
        "id": int(row.get("id", 0)),
        "value": str(row.get("value", "") or ""),
        "tags": _tags_from_str(row.get("tags") or ""),
        "pin": int(row.get("pin") or 0),
        "pinned": bool(row.get("pin") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "token_estimate": (
            int(row["token_estimate"])
            if row.get("token_estimate") is not None
            else None
        ),
    }


def _serialize_adr(row: dict[str, Any]) -> dict[str, Any]:
    """Project an ``adrs`` row into the documented MCP shape."""
    from ..memory_api import _tags_from_str

    return {
        "id": int(row.get("id", 0)),
        "number": int(row.get("number") or 0),
        "title": str(row.get("title", "") or ""),
        "status": str(row.get("status", "") or ""),
        "context": str(row.get("context", "") or ""),
        "decision": str(row.get("decision", "") or ""),
        "tags": _tags_from_str(row.get("tags") or ""),
        "pin": int(row.get("pin") or 0),
        "pinned": bool(row.get("pin") or 0),
        "supersedes": row.get("supersedes"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "token_estimate": (
            int(row["token_estimate"])
            if row.get("token_estimate") is not None
            else None
        ),
    }


# ---------------------------------------------------------------- handlers

def _handle_recall(
    store: MemoryStore,
    *,
    query: str,
    top: int = 10,
    kind: str = "all",
) -> dict[str, Any]:
    """Adapter over :meth:`MemoryStore.recall` with server-side clamps."""
    top_clamped = _clamp(int(top), 1, RECALL_TOP_MAX)
    hits = store.recall(query, top=top_clamped, kind=kind)
    return {"hits": [h.to_dict() for h in hits]}


def _handle_list_facts(
    store: MemoryStore,
    *,
    tag: str | None = None,
    pinned_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Adapter over :meth:`MemoryStore.list_facts` with server-side clamp."""
    limit_clamped = _clamp(int(limit), 1, LIST_LIMIT_MAX)
    rows = store.list_facts(
        tag=tag, limit=limit_clamped, pinned_only=bool(pinned_only)
    )
    return {"facts": [_serialize_fact(r) for r in rows]}


def _handle_list_adrs(
    store: MemoryStore,
    *,
    pinned_only: bool = False,
    limit: int = 20,
    status: str | None = None,
) -> dict[str, Any]:
    """Adapter over :meth:`MemoryStore.list_adrs`.

    ``MemoryStore.list_adrs`` has no ``limit`` parameter (DESIGN
    §3.3); the server slices the returned list. The schema's
    ``maximum: 200`` is the hard cap.
    """
    limit_clamped = _clamp(int(limit), 1, LIST_LIMIT_MAX)
    rows = store.list_adrs(status=status, pinned_only=bool(pinned_only))
    sliced = rows[:limit_clamped]
    return {"adrs": [_serialize_adr(r) for r in sliced]}


def _handle_stats(store: MemoryStore) -> dict[str, Any]:
    """Adapter over :func:`mindkeep._diagnostics.collect_stats`.

    The session-budget block is intentionally absent — see DESIGN §11.
    ``collect_stats`` already omits it; we filter again defensively in
    case a future refactor adds it back.
    """
    from .._diagnostics import collect_stats

    data = collect_stats(store)
    data.pop("session_budget", None)
    return data


def _handle_doctor(
    store: MemoryStore, *, verbose: bool = False
) -> dict[str, Any]:
    """Adapter over :func:`mindkeep._diagnostics.collect_doctor`.

    ``data_dir`` is derived from the open store's DB path so the MCP
    handler doesn't need to re-resolve it.
    """
    from .._diagnostics import collect_doctor

    data_dir = store.db_path.parent
    return collect_doctor(
        data_dir, store.project_id, verbose=bool(verbose)
    )


# ------------------------------------------------------- tool descriptors

# Model-facing descriptions per DESIGN §3.5. Each one names the
# *intent* the tool serves and explicitly says when not to call it,
# carrying forward v0.3's "describe intent, not phrases" rule.

_RECALL_DESC = (
    "Search this project's mindkeep store for facts and ADRs related to "
    "a topic via full-text recall. Use this BEFORE `mindkeep_list_facts` "
    "/ `mindkeep_list_adrs` whenever the user references prior context, "
    "a past decision, or asks 'what did we decide about X'. Do NOT use "
    "for browsing recent items or to answer questions about mindkeep "
    "itself (use `mindkeep_stats` / `mindkeep_doctor` for that)."
)

_LIST_FACTS_DESC = (
    "Browse facts by tag or pinned state when you already know roughly "
    "what bucket of memory to read. Use AFTER `mindkeep_recall` came "
    "back empty, or when the user asks 'what's pinned' / 'what's "
    "tagged X'. Do NOT use for free-text questions — `mindkeep_recall` "
    "is cheaper and more relevant."
)

_LIST_ADRS_DESC = (
    "Browse architectural decision records by status or pinned state. "
    "Use when the user asks 'what ADRs do we have' or 'show pinned "
    "ADRs'. Do NOT use for free-text questions — `mindkeep_recall` is "
    "cheaper and more relevant for topical lookups."
)

_STATS_DESC = (
    "Return counts, token totals, top tags, and other metrics about "
    "this project's mindkeep store. Diagnostics only — use ONLY when "
    "the user asks about the store itself ('how many facts do I have', "
    "'what tags are most common'). Do NOT use to answer questions "
    "about project content; that's `mindkeep_recall`."
)

_DOCTOR_DESC = (
    "Run a structured health check (environment + per-project store) "
    "and return a JSON-shaped report. Diagnostics only — use ONLY when "
    "the user asks 'is mindkeep working' or after a suspected "
    "schema/storage error. Do NOT use as a general status query, and "
    "do NOT use to answer project questions."
)


def build_read_tools() -> list[ToolSpec]:
    """Return the five read-side :class:`ToolSpec` descriptors.

    Pure function; called once at server startup and once per test.
    Side-effect-free so tests can compare snapshots without mutating
    :data:`TOOLS`.
    """
    return [
        ToolSpec(
            name="mindkeep_recall",
            description=_RECALL_DESC,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "top": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": RECALL_TOP_MAX,
                        "default": 10,
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["all", "facts", "adrs"],
                        "default": "all",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=_handle_recall,
            mode="read",
        ),
        ToolSpec(
            name="mindkeep_list_facts",
            description=_LIST_FACTS_DESC,
            input_schema={
                "type": "object",
                "properties": {
                    "tag": {"type": ["string", "null"], "default": None},
                    "pinned_only": {"type": "boolean", "default": False},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": LIST_LIMIT_MAX,
                        "default": 20,
                    },
                },
                "additionalProperties": False,
            },
            handler=_handle_list_facts,
            mode="read",
        ),
        ToolSpec(
            name="mindkeep_list_adrs",
            description=_LIST_ADRS_DESC,
            input_schema={
                "type": "object",
                "properties": {
                    "pinned_only": {"type": "boolean", "default": False},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": LIST_LIMIT_MAX,
                        "default": 20,
                    },
                    "status": {"type": ["string", "null"], "default": None},
                },
                "additionalProperties": False,
            },
            handler=_handle_list_adrs,
            mode="read",
        ),
        ToolSpec(
            name="mindkeep_stats",
            description=_STATS_DESC,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=_handle_stats,
            mode="read",
        ),
        ToolSpec(
            name="mindkeep_doctor",
            description=_DOCTOR_DESC,
            input_schema={
                "type": "object",
                "properties": {
                    "verbose": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            handler=_handle_doctor,
            mode="read",
        ),
    ]


def install_default_tools() -> None:
    """Populate :data:`TOOLS` with the read tools (idempotent).

    Safe to call multiple times: prior :class:`ToolSpec` entries with
    matching ``name`` are removed before re-adding so reloading the
    module under test doesn't accumulate duplicates. Non-ToolSpec
    entries (e.g. plain callables registered by the skeleton test)
    are left in place.
    """
    desired = build_read_tools()
    desired_names = {t.name for t in desired}
    keep = [
        t for t in TOOLS
        if not (isinstance(t, ToolSpec) and t.name in desired_names)
    ]
    TOOLS.clear()
    TOOLS.extend(keep)
    TOOLS.extend(desired)


# Auto-register read tools at import time so ``mindkeep.mcp.server``
# sees them without needing an explicit init step. Keep this last in
# the module so any helper symbols are defined first.
install_default_tools()

"""MCP write-tool handlers for ``mindkeep_add_fact`` / ``mindkeep_add_adr``.

This module is the v0.4 boundary between the model-facing MCP tool
schema and the underlying :class:`mindkeep.memory_api.MemoryStore` API.
It owns:

* Field renames (tool ``value`` ↔ ``MemoryStore.add_fact(content=...)``).
* Return-shape backfill (the API returns ``int``; the tool returns
  ``{id, token_estimate, pinned, ...}``).
* Server-side dedup (DESIGN-v0.4.0 §9.3).
* Error mapping for ``WriteGuardError`` / ``ValueError`` / ``StorageError``
  / unhandled exceptions (DESIGN-v0.4.0 §10).

The ``mcp`` SDK is imported **lazily** inside the helpers so that the
module can be imported without the optional extra installed
(DESIGN-v0.4.0 §7.1). ``MemoryStore`` and ``Storage`` exceptions are safe
to import at module load — they live in core mindkeep.

DESIGN-v0.4.0 §9 explicitly *removes* ``mindkeep_set_preference`` from
the v0.4 MCP surface: preferences persist to a global, cross-project DB
that an autonomous agent could poison permanently. v0.5 may revisit a
project-scoped preference write tool. Do not add it here.
"""

from __future__ import annotations

import sys
import traceback
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Tuple

from ..memory_api import MemoryStore, _tags_to_str
from ..storage import StorageError, WriteGuardError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.types import CallToolResult, Tool


__all__ = [
    "ADD_FACT_NAME",
    "ADD_ADR_NAME",
    "build_write_tools",
    "make_tool_error",
    "make_internal_error",
]


ADD_FACT_NAME = "mindkeep_add_fact"
ADD_ADR_NAME = "mindkeep_add_adr"


# ─────────────────────────── tool descriptors ────────────────────────────
# Plain-Python data so this module stays SDK-free at import time. The
# ``mcp.types.Tool`` instances are built on demand by ``build_write_tools``.


_ADD_FACT_DESCRIPTION = (
    "Persist a durable, project-relevant fact to this project's mindkeep "
    "long-term memory. Use ONLY when the user explicitly asks to remember "
    "something, or states a non-obvious project-specific fact (preferences, "
    "naming conventions, decisions, gotchas) that future sessions will need. "
    "Do NOT call for transient task notes, secrets, credentials, or generic "
    "knowledge the model already has."
)


_ADD_FACT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value": {"type": "string", "minLength": 1},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "pin": {"type": "boolean", "default": False},
    },
    "required": ["value"],
}


_ADD_ADR_DESCRIPTION = (
    "Record an architectural decision (title + decision + rationale) to "
    "this project's mindkeep store. Use ONLY when the user has explicitly "
    "framed something as a decision worth preserving across future "
    "sessions. Do NOT use for transient notes, task lists, secrets, or "
    "speculative options the user has not endorsed."
)


_ADD_ADR_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "decision": {"type": "string", "minLength": 1},
        "rationale": {"type": "string", "minLength": 1},
        "status": {"type": "string", "default": "accepted"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "pin": {"type": "boolean", "default": False},
    },
    "required": ["title", "decision", "rationale"],
}


# ──────────────────────────── error helpers ──────────────────────────────


def make_tool_error(
    error_kind: str,
    message: str,
    fields: Dict[str, Any],
) -> "CallToolResult":
    """Build a recoverable tool-result error per DESIGN-v0.4.0 §10.1.

    The MCP type system has no ``json`` content block, so the structured
    payload is duplicated: ``content[0]`` is a ``TextContent`` carrying
    the JSON-serialised payload (visible to the model in unstructured
    form), and ``structuredContent`` carries the same payload as a dict
    (consumed programmatically by hosts that surface structured output).
    """

    import json as _json

    from mcp.types import CallToolResult, TextContent  # type: ignore[import-not-found]

    payload: Dict[str, Any] = {
        "error_kind": error_kind,
        "message": message,
        "fields": fields,
    }
    return CallToolResult(
        content=[TextContent(type="text", text=_json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


def make_internal_error(exc: BaseException) -> "Exception":
    """Log ``exc`` traceback to stderr and return a fresh ``McpError``.

    Per DESIGN-v0.4.0 §10.2, unhandled exceptions become JSON-RPC -32603
    errors. The tracebacks must NOT leak into the tool result (they may
    contain file paths, env, or unexpected data) — they go to stderr
    only. The JSON-RPC payload contains just the exception class name.
    """

    from mcp.shared.exceptions import McpError  # type: ignore[import-not-found]
    from mcp.types import INTERNAL_ERROR, ErrorData  # type: ignore[import-not-found]

    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    return McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=f"mindkeep internal error: {type(exc).__name__}",
        )
    )


# ──────────────────────────── handler helpers ────────────────────────────


def _canonical_tags(tags: List[str] | None) -> List[str]:
    """Mirror :func:`mindkeep.memory_api._tags_to_str` canonicalisation.

    Stripped, empty-dropped, original order preserved. Used both for the
    dedup boundary (compare against the same canonical form the API will
    persist) and for the tool result echo.
    """

    if not tags:
        return []
    return [t.strip() for t in tags if t and t.strip()]


def _find_duplicate_fact(
    store: MemoryStore,
    value: str,
    tags: List[str],
) -> int | None:
    """Boundary dedup (§9.3) — exact ``value`` + canonical ``tags`` match.

    Returns the existing fact id, or ``None`` if no exact duplicate
    exists in the *current* project. Comparison is on the value
    *as-passed* against the row's stored value column. If redaction
    filters mutate content, two semantically-equal-but-pre-redaction-
    different inputs will not collide here — the post-redaction store
    still naturally appends and the user can ``mindkeep dedupe`` after.
    Best-effort by design.
    """

    canonical = _canonical_tags(tags)
    canonical_str = _tags_to_str(canonical)
    # MemoryStore.list_facts has a ``limit`` param; pull a generous
    # bound so we scan all rows in practice. The store is per-project
    # and small (token cap × row count is bounded).
    rows = store.list_facts(limit=10**9)
    for row in rows:
        if row.get("value") == value and (row.get("tags") or "") == canonical_str:
            try:
                return int(row["id"])
            except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                continue
    return None


def _value_preview(value: str, max_len: int = 80) -> str:
    """Short echo of the offending value for the dedup error payload."""

    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def _fact_row_view(store: MemoryStore, row_id: int) -> Dict[str, Any]:
    """Return ``{id, token_estimate, pinned, tags}`` for a freshly-written fact.

    ``MemoryStore.add_fact`` returns only an int rowid; the tool result
    contract (§3.3 adapter responsibility) requires the richer view, so
    the handler queries the row back. ``list_facts`` is the only public
    read path; we filter in Python on the rowid.
    """

    rows = store.list_facts(limit=10**9)
    for row in rows:
        if int(row.get("id") or 0) == int(row_id):
            return {
                "id": int(row["id"]),
                "token_estimate": int(row.get("token_estimate") or 0),
                "pinned": bool(row.get("pin")),
                "tags": [t for t in (row.get("tags") or "").split(",") if t],
            }
    # Theoretically unreachable — we just inserted this row in the
    # same session. Falling back to a minimal payload keeps the handler
    # honest if the schema ever drifts.
    return {"id": int(row_id), "token_estimate": 0, "pinned": False, "tags": []}


def _adr_row_view(store: MemoryStore, row_id: int) -> Dict[str, Any]:
    """Return ``{id, token_estimate, pinned, status}`` for an inserted ADR."""

    rows = store.list_adrs()
    for row in rows:
        if int(row.get("id") or 0) == int(row_id):
            return {
                "id": int(row["id"]),
                "token_estimate": int(row.get("token_estimate") or 0),
                "pinned": bool(row.get("pin")),
                "status": str(row.get("status") or ""),
            }
    return {"id": int(row_id), "token_estimate": 0, "pinned": False, "status": ""}


def _writeguard_error_payload(exc: WriteGuardError) -> "CallToolResult":
    cap = getattr(exc, "cap", 0)
    pre = getattr(exc, "pre_tokens", 0)
    post = getattr(exc, "post_tokens", 0)
    kind = getattr(exc, "kind", "unknown")
    message = (
        f"{kind} content exceeds the {cap}-token cap "
        f"(post-redaction: {post}, pre-redaction: {pre})."
    )
    return make_tool_error(
        "write_guard",
        message,
        {
            "kind": kind,
            "cap": cap,
            "pre_tokens": pre,
            "post_tokens": post,
            "hint": (
                "Shorten the value or split into multiple facts. The CLI "
                "accepts --force; this MCP tool does not."
            ),
        },
    )


def _storage_error_payload(exc: StorageError) -> "CallToolResult":
    return make_tool_error(
        "storage",
        f"storage error: {exc}",
        {
            "reason": str(exc),
            "schema_version": getattr(exc, "schema_version", None),
            "recoverable": bool(getattr(exc, "recoverable", False)),
        },
    )


def _value_error_payload(exc: ValueError, *, field: str) -> "CallToolResult":
    return make_tool_error(
        "invalid_argument",
        str(exc),
        {"field": field, "value": None, "reason": str(exc)},
    )


# ──────────────────────────── tool handlers ──────────────────────────────


async def _handle_add_fact(
    store: MemoryStore, args: Dict[str, Any]
) -> "CallToolResult":
    value = args["value"]
    tags = list(args.get("tags") or [])
    pin = bool(args.get("pin", False))

    # Pre-flight dedup at the boundary (§9.3). Done before WriteGuard
    # because it short-circuits a future-WriteGuard-rejected duplicate
    # too — though in practice WriteGuard is content-size-only so the
    # ordering doesn't change correctness, just clarity.
    existing = _find_duplicate_fact(store, value, tags)
    if existing is not None:
        return make_tool_error(
            "duplicate",
            f"a fact with this exact value and tags already exists (id={existing})",
            {
                "existing_id": existing,
                "value_preview": _value_preview(value),
                "tags": _canonical_tags(tags),
            },
        )

    try:
        row_id = store.add_fact(value, tags=tags, pin=pin)
    except WriteGuardError as exc:
        return _writeguard_error_payload(exc)
    except ValueError as exc:
        return _value_error_payload(exc, field="value")
    except StorageError as exc:
        return _storage_error_payload(exc)

    payload = _fact_row_view(store, row_id)
    from mcp.types import CallToolResult, TextContent  # type: ignore[import-not-found]
    import json as _json

    return CallToolResult(
        content=[TextContent(type="text", text=_json.dumps(payload))],
        structuredContent=payload,
        isError=False,
    )


async def _handle_add_adr(
    store: MemoryStore, args: Dict[str, Any]
) -> "CallToolResult":
    title = args["title"]
    decision = args["decision"]
    rationale = args["rationale"]
    status = str(args.get("status") or "accepted")
    tags = list(args.get("tags") or [])
    pin = bool(args.get("pin", False))

    # ADRs are NOT dedup'd at the boundary (DESIGN-v0.4.0 §9.3): their
    # bodies are ~10× larger than facts and exact triple-string matches
    # are vanishingly rare. WriteGuard still bounds the runaway case.

    try:
        row_id = store.add_adr(
            title,
            decision,
            rationale,
            status=status,
            tags=tags,
            pin=pin,
        )
    except WriteGuardError as exc:
        return _writeguard_error_payload(exc)
    except ValueError as exc:
        return _value_error_payload(exc, field="title")
    except StorageError as exc:
        return _storage_error_payload(exc)

    payload = _adr_row_view(store, row_id)
    from mcp.types import CallToolResult, TextContent  # type: ignore[import-not-found]
    import json as _json

    return CallToolResult(
        content=[TextContent(type="text", text=_json.dumps(payload))],
        structuredContent=payload,
        isError=False,
    )


# ──────────────────────────── registry builder ───────────────────────────


def build_write_tools() -> Tuple[
    List["Tool"],
    Dict[str, Callable[[MemoryStore, Dict[str, Any]], Awaitable["CallToolResult"]]],
]:
    """Build write-tool descriptors and handler dispatch table.

    Returns ``(tools, handlers)`` where ``tools`` is the list passed to
    ``mcp.server.Server.list_tools()`` and ``handlers`` maps tool names
    to async handlers. Imports the MCP SDK lazily (caller is in the
    ``main()`` path that already required the extra).
    """

    from mcp.types import Tool  # type: ignore[import-not-found]

    tools: List[Tool] = [
        Tool(
            name=ADD_FACT_NAME,
            description=_ADD_FACT_DESCRIPTION,
            inputSchema=_ADD_FACT_SCHEMA,
        ),
        Tool(
            name=ADD_ADR_NAME,
            description=_ADD_ADR_DESCRIPTION,
            inputSchema=_ADD_ADR_SCHEMA,
        ),
    ]
    handlers: Dict[
        str, Callable[[MemoryStore, Dict[str, Any]], Awaitable["CallToolResult"]]
    ] = {
        ADD_FACT_NAME: _handle_add_fact,
        ADD_ADR_NAME: _handle_add_adr,
    }
    return tools, handlers

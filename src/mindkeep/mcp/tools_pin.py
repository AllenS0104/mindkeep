"""MCP pin/unpin write tools (#36).

``mindkeep_pin`` and ``mindkeep_unpin`` are kept as separate tools rather
than collapsed into one ``set_pin {pinned: bool}`` (DESIGN-v0.4.0 §3): the
model-facing description for each verb stays unambiguous, and the same
``--allow-writes`` registration filter hides both at once.

Both tools accept ``{kind: "fact"|"adr", id: int >= 1}``. Unknown id
returns a recoverable tool-result error with ``error_kind="not_found"``
and ``fields={kind, id}`` (DESIGN-v0.4.0 §10.1) so the model can edit
and retry.

The ``mcp`` SDK is imported lazily inside :func:`build_pin_tools`. This
module imports nothing from ``mcp`` at top level, preserving the
``mindkeep`` lazy-import contract (DESIGN-v0.4.0 §7.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Tuple

from ..memory_api import MemoryStore
from ..storage import StorageError
from .tools_write import make_tool_error

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.types import CallToolResult, Tool


__all__ = [
    "PIN_NAME",
    "UNPIN_NAME",
    "build_pin_tools",
]


PIN_NAME = "mindkeep_pin"
UNPIN_NAME = "mindkeep_unpin"


_PIN_DESCRIPTION = (
    "Promote an existing fact or ADR so it ranks first in future "
    "listings (`mindkeep_list_facts`, `mindkeep_list_adrs`) and surfaces "
    "earlier in `mindkeep_recall`. Use ONLY when the user explicitly "
    "marks something as important / critical / 'pin this'. Do NOT pin "
    "speculatively or as a default; pinning is a scarce attention slot."
)

_UNPIN_DESCRIPTION = (
    "Remove the pinned flag from a fact or ADR so it returns to ordinary "
    "ranking. Use ONLY when the user explicitly says an item is no "
    "longer high-priority or asks to 'unpin' it. Do NOT use as a "
    "cleanup pass — pin state is user-controlled."
)


_PIN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["fact", "adr"]},
        "id": {"type": "integer", "minimum": 1},
    },
    "required": ["kind", "id"],
}


def _not_found(kind: str, row_id: int) -> "CallToolResult":
    """Build the ``error_kind="not_found"`` payload per DESIGN §10.1."""

    return make_tool_error(
        "not_found",
        f"{kind} id {row_id} not found in this project",
        {"kind": kind, "id": int(row_id)},
    )


def _storage_payload(exc: StorageError) -> "CallToolResult":
    return make_tool_error(
        "storage",
        f"storage error: {exc}",
        {
            "reason": str(exc),
            "schema_version": getattr(exc, "schema_version", None),
            "recoverable": bool(getattr(exc, "recoverable", False)),
        },
    )


async def _handle_pin(
    store: MemoryStore, args: Dict[str, Any]
) -> "CallToolResult":
    return await _set_pinned(store, args, pinned=True)


async def _handle_unpin(
    store: MemoryStore, args: Dict[str, Any]
) -> "CallToolResult":
    return await _set_pinned(store, args, pinned=False)


async def _set_pinned(
    store: MemoryStore, args: Dict[str, Any], *, pinned: bool
) -> "CallToolResult":
    kind = args["kind"]
    row_id = int(args["id"])

    # Schema validates ``kind`` against the enum, but the dispatch is
    # explicit anyway: surfacing an unknown ``kind`` here as
    # ``invalid_argument`` would only happen if a misbehaving client
    # bypassed validation, and the structured error is more useful
    # than a stack trace.
    if kind not in ("fact", "adr"):
        return make_tool_error(
            "invalid_argument",
            f"unknown kind {kind!r}; expected 'fact' or 'adr'",
            {"field": "kind", "value": kind, "reason": "must be 'fact' or 'adr'"},
        )

    try:
        if kind == "fact":
            if pinned:
                store.pin_fact(row_id)
            else:
                store.unpin_fact(row_id)
        else:  # kind == "adr"
            if pinned:
                store.pin_adr(row_id)
            else:
                store.unpin_adr(row_id)
    except ValueError:
        # MemoryStore raises ``ValueError("<kind> id <n> not found")``
        # exactly when the row id doesn't exist in the current project.
        # That is the design's ``not_found`` mapping (§10.1).
        return _not_found(kind, row_id)
    except StorageError as exc:
        return _storage_payload(exc)

    payload: Dict[str, Any] = {
        "kind": kind,
        "id": row_id,
        "pinned": pinned,
    }

    import json as _json

    from mcp.types import CallToolResult, TextContent  # type: ignore[import-not-found]

    return CallToolResult(
        content=[TextContent(type="text", text=_json.dumps(payload))],
        structuredContent=payload,
        isError=False,
    )


def build_pin_tools() -> Tuple[
    List["Tool"],
    Dict[str, Callable[[MemoryStore, Dict[str, Any]], Awaitable["CallToolResult"]]],
]:
    """Build pin/unpin tool descriptors and handler dispatch table.

    Returns ``(tools, handlers)`` matching the
    :func:`mindkeep.mcp.tools_write.build_write_tools` shape so the
    server's registration loop treats both modules uniformly. The MCP
    SDK is imported lazily — caller is in ``main()`` which has already
    required the ``[mcp]`` extra.
    """

    from mcp.types import Tool  # type: ignore[import-not-found]

    tools: List[Tool] = [
        Tool(
            name=PIN_NAME,
            description=_PIN_DESCRIPTION,
            inputSchema=_PIN_SCHEMA,
        ),
        Tool(
            name=UNPIN_NAME,
            description=_UNPIN_DESCRIPTION,
            inputSchema=_PIN_SCHEMA,
        ),
    ]
    handlers: Dict[
        str, Callable[[MemoryStore, Dict[str, Any]], Awaitable["CallToolResult"]]
    ] = {
        PIN_NAME: _handle_pin,
        UNPIN_NAME: _handle_unpin,
    }
    return tools, handlers

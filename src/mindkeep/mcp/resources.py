"""MCP resource handlers for ``mindkeep://...`` URIs (#36).

Three resource shapes (DESIGN-v0.4.0 §4):

* ``mindkeep://facts/{id}``  — full record JSON for one fact in the
  currently bound project.
* ``mindkeep://adrs/{id}``   — full record JSON for one ADR.
* ``mindkeep://project``     — singleton metadata about the bound
  project (``resolved_project_dir``, ``project_id``, ``id_source``,
  ``db_path``, ``schema_version``, ``counts``, ``allow_writes``).

Resources are READ-ONLY. They are registered regardless of
``--allow-writes`` (DESIGN §4 — resources are user attachments, not
agent actions, so the write gate does not apply).

URI stability (DESIGN §4.1): ``{id}`` is the SQLite rowid in the bound
project's DB; it is valid only for the current server lifetime. Reads
of nonexistent ids return MCP resource-not-found, never a silent empty
payload.

stdio invariant (DESIGN §3.4): nothing in this module — and nothing it
calls — touches ``sys.stdout``. All diagnostics go to ``sys.stderr``.

The ``mcp`` SDK is imported lazily inside the registration helper so
``mindkeep`` keeps importing without the optional extra.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..memory_api import MemoryStore, _tags_from_str
from ..storage import SCHEMA_VERSION

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server import Server


__all__ = [
    "LIST_DEFAULT_PAGE_SIZE",
    "LIST_MAX_PAGE_SIZE",
    "register_resources",
    "build_project_meta",
]


# Bounded ``resources/list`` pagination per DESIGN §4. The default
# matches the documented "first page is small" expectation; the max
# matches ``LIST_LIMIT_MAX`` in :mod:`mindkeep.mcp.tools` so a host
# pulling pages doesn't get bigger pages from one endpoint than the
# other.
LIST_DEFAULT_PAGE_SIZE = 50
LIST_MAX_PAGE_SIZE = 200


# ─────────────────────────── serialization ───────────────────────────


def _serialize_fact_full(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project a ``facts`` row into the full resource payload."""

    return {
        "kind": "fact",
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


def _serialize_adr_full(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project an ``adrs`` row into the full resource payload."""

    return {
        "kind": "adr",
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


# ─────────────────────────── ordering helpers ───────────────────────────


def _order_key(row: Dict[str, Any]) -> Tuple[int, str, int]:
    """``(pin DESC, updated_at DESC, id DESC)`` sort key for resources/list."""

    return (
        int(row.get("pin") or 0),
        str(row.get("updated_at") or ""),
        int(row.get("id") or 0),
    )


def _ordered_facts(store: MemoryStore) -> List[Dict[str, Any]]:
    rows = store.list_facts(limit=10**9)
    rows.sort(key=_order_key, reverse=True)
    return rows


def _ordered_adrs(store: MemoryStore) -> List[Dict[str, Any]]:
    rows = store.list_adrs()
    rows.sort(key=_order_key, reverse=True)
    return rows


# ─────────────────────────── project metadata ───────────────────────────


def build_project_meta(
    store: MemoryStore,
    *,
    resolved_project_dir: Path,
    id_source: str,
    allow_writes: bool,
) -> Dict[str, Any]:
    """Return the payload exposed by ``mindkeep://project`` (DESIGN §8.3).

    Counts are computed at read time so the resource always reflects the
    live store; this is cheap because the per-project DB is bounded by
    WriteGuard caps × token-estimate × row-count.
    """

    facts = store.list_facts(limit=10**9)
    adrs = store.list_adrs()
    return {
        "resolved_project_dir": str(resolved_project_dir),
        "project_id": store.project_id.id,
        "id_source": id_source,
        "db_path": str(store.db_path),
        "schema_version": SCHEMA_VERSION,
        "allow_writes": bool(allow_writes),
        "counts": {"facts": len(facts), "adrs": len(adrs)},
    }


# ─────────────────────────── URI parsing ───────────────────────────


def _parse_mindkeep_uri(uri_str: str) -> Tuple[str, Optional[int]]:
    """Parse ``mindkeep://<bucket>[/<id>]``.

    Returns ``(bucket, id_or_none)`` for the three supported shapes:

    * ``mindkeep://project``        → ``("project", None)``
    * ``mindkeep://facts/{id}``     → ``("facts", id)``
    * ``mindkeep://adrs/{id}``      → ``("adrs", id)``

    Anything else raises :class:`ValueError` so the caller can convert
    it into an MCP ``INVALID_PARAMS`` error.
    """

    s = uri_str.strip()
    prefix = "mindkeep://"
    if not s.startswith(prefix):
        raise ValueError(f"unsupported scheme in URI {uri_str!r}")
    rest = s[len(prefix):]
    # Strip any trailing slash to make the singleton form forgiving.
    if rest.endswith("/"):
        rest = rest[:-1]
    if rest == "project":
        return ("project", None)
    if "/" not in rest:
        raise ValueError(f"missing id in URI {uri_str!r}")
    bucket, _, tail = rest.partition("/")
    if bucket not in ("facts", "adrs"):
        raise ValueError(f"unknown bucket {bucket!r} in URI {uri_str!r}")
    if "/" in tail or not tail:
        raise ValueError(f"malformed id in URI {uri_str!r}")
    try:
        row_id = int(tail)
    except ValueError as exc:
        raise ValueError(f"non-integer id in URI {uri_str!r}") from exc
    if row_id < 1:
        raise ValueError(f"id must be >= 1 in URI {uri_str!r}")
    return (bucket, row_id)


# ─────────────────────────── registration ───────────────────────────


def register_resources(
    srv: "Server",
    store: MemoryStore,
    *,
    resolved_project_dir: Path,
    id_source: str,
    allow_writes: bool,
) -> None:
    """Wire ``resources/list`` and ``resources/read`` onto ``srv``.

    Captures ``store`` and the project-resolution metadata in closures so
    handler bodies stay pure dispatch. The MCP SDK is imported lazily —
    caller has already required the extra in ``main()``.
    """

    from mcp.server.lowlevel.helper_types import ReadResourceContents  # type: ignore[import-not-found]
    from mcp.shared.exceptions import McpError  # type: ignore[import-not-found]
    from mcp.types import (  # type: ignore[import-not-found]
        ErrorData,
        INVALID_PARAMS,
        ListResourcesRequest,
        ListResourcesResult,
        ReadResourceRequest,
        Resource,
        ServerResult,
        TextResourceContents,
    )

    def _resource_descriptor_fact(row: Dict[str, Any]) -> Resource:
        rid = int(row.get("id") or 0)
        title = str(row.get("value") or "").splitlines()[0] if row.get("value") else f"fact #{rid}"
        return Resource(
            uri=f"mindkeep://facts/{rid}",  # type: ignore[arg-type]
            name=f"fact:{rid}",
            title=title[:80] if title else f"fact #{rid}",
            description=f"mindkeep fact {rid}",
            mimeType="application/json",
        )

    def _resource_descriptor_adr(row: Dict[str, Any]) -> Resource:
        rid = int(row.get("id") or 0)
        title = str(row.get("title") or f"ADR #{rid}")
        return Resource(
            uri=f"mindkeep://adrs/{rid}",  # type: ignore[arg-type]
            name=f"adr:{rid}",
            title=title[:80] if title else f"ADR #{rid}",
            description=f"mindkeep ADR {rid}",
            mimeType="application/json",
        )

    def _project_descriptor() -> Resource:
        return Resource(
            uri="mindkeep://project",  # type: ignore[arg-type]
            name="project",
            title="mindkeep project metadata",
            description=(
                "Project binding (resolved_project_dir, project_id, "
                "id_source, db_path, schema_version, counts, allow_writes)."
            ),
            mimeType="application/json",
        )

    def _all_descriptors() -> List[Resource]:
        out: List[Resource] = [_project_descriptor()]
        for r in _ordered_facts(store):
            out.append(_resource_descriptor_fact(r))
        for r in _ordered_adrs(store):
            out.append(_resource_descriptor_adr(r))
        return out

    def _decode_cursor(raw: Optional[str]) -> int:
        if not raw:
            return 0
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"invalid resources/list cursor: {raw!r}",
                )
            )
        if n < 0:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"invalid resources/list cursor: {raw!r}",
                )
            )
        return n

    async def _list_handler(req: ListResourcesRequest) -> ServerResult:
        params = getattr(req, "params", None)
        cursor_raw = getattr(params, "cursor", None) if params is not None else None
        start = _decode_cursor(cursor_raw)
        # Page size is fixed server-side. The SDK does not expose a
        # ``limit`` param on ``ListResourcesRequest`` (the spec is
        # cursor-only), so DEFAULT == MAX in practice; keeping the two
        # constants distinct documents the design's intent and lets a
        # future per-call override land cleanly.
        page_size = min(LIST_DEFAULT_PAGE_SIZE, LIST_MAX_PAGE_SIZE)
        all_resources = _all_descriptors()
        page = all_resources[start : start + page_size]
        next_cursor = (
            str(start + page_size)
            if start + page_size < len(all_resources)
            else None
        )
        return ServerResult(
            ListResourcesResult(resources=page, nextCursor=next_cursor)
        )

    srv.request_handlers[ListResourcesRequest] = _list_handler

    @srv.read_resource()  # type: ignore[misc]
    async def _read_handler(uri):  # type: ignore[no-untyped-def]
        try:
            bucket, row_id = _parse_mindkeep_uri(str(uri))
        except ValueError as exc:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"invalid mindkeep resource URI: {exc}",
                )
            )

        if bucket == "project":
            payload = build_project_meta(
                store,
                resolved_project_dir=resolved_project_dir,
                id_source=id_source,
                allow_writes=allow_writes,
            )
            return [
                ReadResourceContents(
                    content=json.dumps(payload),
                    mime_type="application/json",
                )
            ]

        # Fact / ADR lookup. ``MemoryStore`` exposes only ``list_*`` —
        # we iterate and filter by id. The store is per-project and
        # bounded so this is fine in practice.
        if bucket == "facts":
            for row in store.list_facts(limit=10**9):
                if int(row.get("id") or 0) == row_id:
                    payload = _serialize_fact_full(row)
                    return [
                        ReadResourceContents(
                            content=json.dumps(payload),
                            mime_type="application/json",
                        )
                    ]
        else:  # bucket == "adrs"
            for row in store.list_adrs():
                if int(row.get("id") or 0) == row_id:
                    payload = _serialize_adr_full(row)
                    return [
                        ReadResourceContents(
                            content=json.dumps(payload),
                            mime_type="application/json",
                        )
                    ]

        # Valid URI shape, no matching row in the bound project.
        # DESIGN §4.1: this MUST be an explicit error, not an empty
        # payload. MCP has no dedicated "resource not found" code, so
        # we use INVALID_PARAMS with a structured ``data`` payload that
        # carries the design's ``error_kind="not_found"`` discriminator.
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"mindkeep resource not found: {uri}"
                    f" (id {row_id} does not exist in this project)"
                ),
                data={
                    "error_kind": "not_found",
                    "uri": str(uri),
                    "kind": "fact" if bucket == "facts" else "adr",
                    "id": row_id,
                },
            )
        )

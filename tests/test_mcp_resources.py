"""Unit tests for MCP resources (#36): facts/adrs/project URIs.

Covers DESIGN-v0.4.0 §4 (resource shapes), §4.1 (URI stability), §8.3
(``mindkeep://project`` payload), §3.4 (stdio invariant).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.shared.exceptions import McpError  # noqa: E402
from mcp.types import (  # noqa: E402
    INVALID_PARAMS,
    ListResourcesRequest,
    PaginatedRequestParams,
    ReadResourceRequest,
    ReadResourceRequestParams,
)
from pydantic import AnyUrl  # noqa: E402

from mindkeep.memory_api import MemoryStore  # noqa: E402
from mindkeep.mcp import resources as resources_mod  # noqa: E402
from mindkeep.storage import SCHEMA_VERSION  # noqa: E402


# ──────────────────────────── helpers ────────────────────────────


def _open_store(tmp_path: Path) -> MemoryStore:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    (project / ".mindkeep").mkdir(exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return MemoryStore.open(cwd=project, data_dir=data)


def _build_server_with_resources(
    tmp_path: Path,
    *,
    allow_writes: bool = False,
    id_source: str = "cwd-discovery",
):
    """Construct a Server instance with the resource handlers registered.

    Mirrors the call shape ``mindkeep.mcp.server.main`` uses but stays
    in-process for unit testing.
    """

    from mcp.server import Server

    store = _open_store(tmp_path)
    project_dir = (tmp_path / "proj").resolve()
    srv = Server("mindkeep-test")
    resources_mod.register_resources(
        srv,
        store,
        resolved_project_dir=project_dir,
        id_source=id_source,
        allow_writes=allow_writes,
    )
    return srv, store, project_dir


def _list_resources(srv, *, cursor=None):
    handler = srv.request_handlers[ListResourcesRequest]
    params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
    req = ListResourcesRequest(method="resources/list", params=params)
    return asyncio.run(handler(req)).root


def _read_resource(srv, uri: str):
    handler = srv.request_handlers[ReadResourceRequest]
    req = ReadResourceRequest(
        method="resources/read",
        params=ReadResourceRequestParams(uri=AnyUrl(uri)),
    )
    return asyncio.run(handler(req)).root


def _read_payload(srv, uri: str) -> dict:
    res = _read_resource(srv, uri)
    contents = res.contents
    assert len(contents) == 1
    text = contents[0].text
    assert contents[0].mimeType == "application/json"
    return json.loads(text)


# ───────────────────────── resources/list ─────────────────────────


def test_list_resources_includes_project_singleton(tmp_path: Path) -> None:
    srv, _store, _pd = _build_server_with_resources(tmp_path)
    res = _list_resources(srv)
    uris = [str(r.uri) for r in res.resources]
    assert "mindkeep://project" in uris


def test_list_resources_includes_facts_and_adrs(tmp_path: Path) -> None:
    srv, store, _pd = _build_server_with_resources(tmp_path)
    f1 = store.add_fact("alpha", tags=["x"])
    f2 = store.add_fact("beta", tags=["y"], pin=True)
    a1 = store.add_adr("Title", "Decision", "Rationale")
    res = _list_resources(srv)
    uris = [str(r.uri) for r in res.resources]
    assert f"mindkeep://facts/{f1}" in uris
    assert f"mindkeep://facts/{f2}" in uris
    assert f"mindkeep://adrs/{a1}" in uris


def test_list_resources_orders_pinned_first(tmp_path: Path) -> None:
    """Order: ``pinned DESC, updated_at DESC, id DESC`` (within facts/adrs)."""

    srv, store, _pd = _build_server_with_resources(tmp_path)
    unpinned = store.add_fact("u1")
    pinned = store.add_fact("p1", pin=True)
    res = _list_resources(srv)
    uris = [str(r.uri) for r in res.resources if str(r.uri).startswith("mindkeep://facts/")]
    # Pinned must come before unpinned regardless of insertion order.
    assert uris.index(f"mindkeep://facts/{pinned}") < uris.index(
        f"mindkeep://facts/{unpinned}"
    )


def test_list_resources_pagination_clamps_page_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With many rows, the first page is bounded; ``nextCursor`` advances."""

    monkeypatch.setattr(resources_mod, "LIST_DEFAULT_PAGE_SIZE", 5)
    srv, store, _pd = _build_server_with_resources(tmp_path)
    for i in range(12):
        store.add_fact(f"fact-{i}")
    page1 = _list_resources(srv)
    assert len(page1.resources) == 5
    assert page1.nextCursor is not None

    page2 = _list_resources(srv, cursor=page1.nextCursor)
    assert len(page2.resources) == 5
    assert page2.nextCursor is not None

    page3 = _list_resources(srv, cursor=page2.nextCursor)
    # 1 project + 12 facts = 13 total, pages of 5 -> 5/5/3
    assert len(page3.resources) == 3
    assert page3.nextCursor is None


def test_list_resources_invalid_cursor(tmp_path: Path) -> None:
    srv, _store, _pd = _build_server_with_resources(tmp_path)
    with pytest.raises(McpError) as excinfo:
        _list_resources(srv, cursor="not-an-int")
    assert excinfo.value.error.code == INVALID_PARAMS


# ───────────────────────── resources/read ─────────────────────────


def test_read_fact_returns_full_record(tmp_path: Path) -> None:
    srv, store, _pd = _build_server_with_resources(tmp_path)
    fid = store.add_fact("the value", tags=["a", "b"], pin=True)
    payload = _read_payload(srv, f"mindkeep://facts/{fid}")
    assert payload["kind"] == "fact"
    assert payload["id"] == fid
    assert payload["value"] == "the value"
    assert payload["tags"] == ["a", "b"]
    assert payload["pinned"] is True
    assert isinstance(payload["token_estimate"], int)
    assert payload["created_at"]
    assert payload["updated_at"]


def test_read_adr_returns_full_record(tmp_path: Path) -> None:
    srv, store, _pd = _build_server_with_resources(tmp_path)
    aid = store.add_adr("T", "D", "R", status="accepted", tags=["arch"])
    payload = _read_payload(srv, f"mindkeep://adrs/{aid}")
    assert payload["kind"] == "adr"
    assert payload["id"] == aid
    assert payload["title"] == "T"
    assert payload["decision"] == "D"
    assert payload["status"] == "accepted"
    assert payload["tags"] == ["arch"]


def test_read_project_returns_metadata(tmp_path: Path) -> None:
    srv, store, project_dir = _build_server_with_resources(
        tmp_path, allow_writes=True, id_source="flag"
    )
    store.add_fact("one")
    store.add_fact("two")
    store.add_adr("t", "d", "r")

    payload = _read_payload(srv, "mindkeep://project")
    assert payload["resolved_project_dir"] == str(project_dir)
    assert payload["project_id"] == store.project_id.id
    assert payload["id_source"] == "flag"
    assert payload["db_path"] == str(store.db_path)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["allow_writes"] is True
    assert payload["counts"] == {"facts": 2, "adrs": 1}


def test_read_nonexistent_fact_is_resource_not_found(tmp_path: Path) -> None:
    srv, _store, _pd = _build_server_with_resources(tmp_path)
    with pytest.raises(McpError) as excinfo:
        _read_resource(srv, "mindkeep://facts/999999")
    err = excinfo.value.error
    assert err.code == INVALID_PARAMS
    assert "not found" in err.message.lower()
    assert err.data and err.data.get("error_kind") == "not_found"
    assert err.data.get("kind") == "fact"
    assert err.data.get("id") == 999999


def test_read_nonexistent_adr_is_resource_not_found(tmp_path: Path) -> None:
    srv, _store, _pd = _build_server_with_resources(tmp_path)
    with pytest.raises(McpError) as excinfo:
        _read_resource(srv, "mindkeep://adrs/12345")
    err = excinfo.value.error
    assert err.code == INVALID_PARAMS
    assert err.data and err.data.get("error_kind") == "not_found"
    assert err.data.get("kind") == "adr"


def test_read_unknown_bucket_is_invalid_uri(tmp_path: Path) -> None:
    srv, _store, _pd = _build_server_with_resources(tmp_path)
    with pytest.raises(McpError) as excinfo:
        _read_resource(srv, "mindkeep://garbage/1")
    err = excinfo.value.error
    assert err.code == INVALID_PARAMS
    assert "invalid mindkeep resource URI" in err.message


def test_read_malformed_id_is_invalid_uri(tmp_path: Path) -> None:
    srv, _store, _pd = _build_server_with_resources(tmp_path)
    with pytest.raises(McpError) as excinfo:
        _read_resource(srv, "mindkeep://facts/not-a-number")
    assert excinfo.value.error.code == INVALID_PARAMS


# ─────────────────────────── always-on (no allow_writes) ───────────────


def test_resources_work_without_allow_writes(tmp_path: Path) -> None:
    """DESIGN §4: resources are read-only, available regardless of writes."""

    srv, store, _pd = _build_server_with_resources(tmp_path, allow_writes=False)
    fid = store.add_fact("r/o read")
    res = _list_resources(srv)
    assert any(str(r.uri) == "mindkeep://project" for r in res.resources)
    payload = _read_payload(srv, f"mindkeep://facts/{fid}")
    assert payload["id"] == fid


# ─────────────────────────── stdout purity ───────────────────────────


def test_no_stdout_writes_during_resource_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §3.4: resource handlers must not emit a byte on stdout."""

    srv, store, _pd = _build_server_with_resources(tmp_path)
    fid = store.add_fact("alpha")
    aid = store.add_adr("t", "d", "r")
    capsys.readouterr()  # drain anything from setup

    _list_resources(srv)
    _read_payload(srv, "mindkeep://project")
    _read_payload(srv, f"mindkeep://facts/{fid}")
    _read_payload(srv, f"mindkeep://adrs/{aid}")
    # Error branches too.
    with pytest.raises(McpError):
        _read_resource(srv, "mindkeep://facts/999999")
    with pytest.raises(McpError):
        _read_resource(srv, "mindkeep://garbage/1")

    out, _err = capsys.readouterr()
    assert out == "", f"unexpected stdout output: {out!r}"


# ───────────────────────── unit: URI parser ─────────────────────────


def test_parse_uri_project() -> None:
    assert resources_mod._parse_mindkeep_uri("mindkeep://project") == ("project", None)


def test_parse_uri_facts() -> None:
    assert resources_mod._parse_mindkeep_uri("mindkeep://facts/42") == ("facts", 42)


def test_parse_uri_adrs() -> None:
    assert resources_mod._parse_mindkeep_uri("mindkeep://adrs/7") == ("adrs", 7)


@pytest.mark.parametrize(
    "uri",
    [
        "http://facts/1",
        "mindkeep://facts",
        "mindkeep://facts/",
        "mindkeep://facts/abc",
        "mindkeep://unknown/1",
        "mindkeep://facts/0",
        "mindkeep://facts/1/extra",
    ],
)
def test_parse_uri_rejects_bad(uri: str) -> None:
    with pytest.raises(ValueError):
        resources_mod._parse_mindkeep_uri(uri)

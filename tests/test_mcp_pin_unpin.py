"""Unit + round-trip tests for ``mindkeep_pin`` / ``mindkeep_unpin`` (#36).

Covers:

* Registration gating on ``--allow-writes``.
* ``mindkeep_pin`` happy path → ``{kind, id, pinned: true}``.
* ``mindkeep_unpin`` happy path → ``{kind, id, pinned: false}``.
* Unknown id → ``error_kind="not_found"`` with ``{kind, id}``.
* Unknown ``kind`` → schema rejection → ``error_kind="invalid_argument"``.
* Stdout-purity invariant (DESIGN §3.4).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.shared.exceptions import McpError  # noqa: E402
from mcp.types import (  # noqa: E402
    METHOD_NOT_FOUND,
    CallToolRequest,
    CallToolRequestParams,
)

from mindkeep.memory_api import MemoryStore  # noqa: E402


# ──────────────────────────── helpers ────────────────────────────


def _open_store(tmp_path: Path) -> MemoryStore:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    (project / ".mindkeep").mkdir(exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return MemoryStore.open(cwd=project, data_dir=data)


def _build_server(tmp_path: Path, *, allow_writes: bool):
    """Build an in-process server wired the same way as
    ``mindkeep.mcp.server.main``, including the pin/unpin registration.
    """

    import jsonschema

    from mcp.server import Server
    from mcp.types import ErrorData, ServerResult

    from mindkeep.mcp.tools import TOOLS
    from mindkeep.mcp.tools_pin import build_pin_tools
    from mindkeep.mcp.tools_write import (
        build_write_tools,
        make_internal_error,
        make_tool_error,
    )

    store = _open_store(tmp_path)
    srv = Server("mindkeep-test")

    all_tools = list(TOOLS)
    all_handlers: dict = {}
    if allow_writes:
        wt, wh = build_write_tools()
        all_tools.extend(wt)
        all_handlers.update(wh)
        pt, ph = build_pin_tools()
        all_tools.extend(pt)
        all_handlers.update(ph)

    @srv.list_tools()
    async def _list_tools():
        return list(all_tools)

    async def _call(req):
        name = req.params.name
        args = req.params.arguments or {}
        handler = all_handlers.get(name)
        if handler is None:
            raise McpError(
                ErrorData(code=METHOD_NOT_FOUND, message=f"Unknown tool: {name}")
            )
        tool_def = next((t for t in all_tools if t.name == name), None)
        if tool_def is not None:
            try:
                jsonschema.validate(instance=args, schema=tool_def.inputSchema)
            except jsonschema.ValidationError as exc:
                return ServerResult(
                    make_tool_error(
                        "invalid_argument",
                        exc.message,
                        {
                            "field": ".".join(str(p) for p in exc.absolute_path),
                            "value": exc.instance,
                            "reason": exc.message,
                        },
                    )
                )
        try:
            result = await handler(store, args)
        except McpError:
            raise
        except Exception as exc:
            raise make_internal_error(exc) from None
        return ServerResult(result)

    srv.request_handlers[CallToolRequest] = _call

    def dispatch(name: str, arguments: dict):
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        return asyncio.run(_call(req))

    return srv, store, all_tools, dispatch


def _payload(server_result):
    return server_result.root.structuredContent


def _is_error(server_result) -> bool:
    return bool(server_result.root.isError)


# ───────────────────────── registration gating ─────────────────────────


def test_pin_unpin_absent_without_allow_writes(tmp_path: Path) -> None:
    _, _store, all_tools, _ = _build_server(tmp_path, allow_writes=False)
    names = {t.name for t in all_tools}
    assert "mindkeep_pin" not in names
    assert "mindkeep_unpin" not in names


def test_pin_unpin_present_with_allow_writes(tmp_path: Path) -> None:
    _, _store, all_tools, _ = _build_server(tmp_path, allow_writes=True)
    names = {t.name for t in all_tools}
    assert {"mindkeep_pin", "mindkeep_unpin"}.issubset(names)


def test_pin_in_readonly_server_returns_method_not_found(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=False)
    with pytest.raises(McpError) as excinfo:
        dispatch("mindkeep_pin", {"kind": "fact", "id": 1})
    assert excinfo.value.error.code == METHOD_NOT_FOUND


# ─────────────────────────── pin / unpin happy paths ───────────────────


def test_pin_fact_happy_path(tmp_path: Path) -> None:
    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    fact_id = store.add_fact("a fact to pin", tags=["t"])

    res = dispatch("mindkeep_pin", {"kind": "fact", "id": fact_id})
    assert not _is_error(res)
    p = _payload(res)
    assert p == {"kind": "fact", "id": fact_id, "pinned": True}

    rows = store.list_facts(pinned_only=True)
    assert any(int(r["id"]) == fact_id for r in rows)


def test_pin_adr_happy_path(tmp_path: Path) -> None:
    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    adr_id = store.add_adr("title", "decision text", "rationale text")

    res = dispatch("mindkeep_pin", {"kind": "adr", "id": adr_id})
    assert not _is_error(res)
    assert _payload(res) == {"kind": "adr", "id": adr_id, "pinned": True}

    rows = store.list_adrs(pinned_only=True)
    assert any(int(r["id"]) == adr_id for r in rows)


def test_unpin_fact_happy_path(tmp_path: Path) -> None:
    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    fact_id = store.add_fact("pinned at creation", pin=True)

    res = dispatch("mindkeep_unpin", {"kind": "fact", "id": fact_id})
    assert not _is_error(res)
    assert _payload(res) == {"kind": "fact", "id": fact_id, "pinned": False}

    rows = store.list_facts(pinned_only=True)
    assert not any(int(r["id"]) == fact_id for r in rows)


def test_unpin_adr_happy_path(tmp_path: Path) -> None:
    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    adr_id = store.add_adr("t", "d", "r", pin=True)

    res = dispatch("mindkeep_unpin", {"kind": "adr", "id": adr_id})
    assert not _is_error(res)
    assert _payload(res) == {"kind": "adr", "id": adr_id, "pinned": False}


# ─────────────────────── not_found / invalid_argument ──────────────────


def test_pin_unknown_fact_id_returns_not_found(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_pin", {"kind": "fact", "id": 999_999})
    assert _is_error(res)
    p = _payload(res)
    assert p["error_kind"] == "not_found"
    assert p["fields"] == {"kind": "fact", "id": 999_999}


def test_unpin_unknown_adr_id_returns_not_found(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_unpin", {"kind": "adr", "id": 12345})
    assert _is_error(res)
    p = _payload(res)
    assert p["error_kind"] == "not_found"
    assert p["fields"] == {"kind": "adr", "id": 12345}


def test_pin_bad_kind_invalid_argument(tmp_path: Path) -> None:
    """Schema rejects ``kind`` outside the enum."""

    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_pin", {"kind": "memo", "id": 1})
    assert _is_error(res)
    assert _payload(res)["error_kind"] == "invalid_argument"


def test_pin_missing_id_invalid_argument(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_pin", {"kind": "fact"})
    assert _is_error(res)
    assert _payload(res)["error_kind"] == "invalid_argument"


def test_pin_id_zero_invalid_argument(tmp_path: Path) -> None:
    """Schema enforces ``minimum: 1`` on the id."""

    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_pin", {"kind": "fact", "id": 0})
    assert _is_error(res)
    assert _payload(res)["error_kind"] == "invalid_argument"


# ─────────────────────────── stdout purity ───────────────────────────


def test_no_stdout_writes_during_pin_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §3.4: pin/unpin handlers must not emit a single byte on stdout."""

    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    fact_id = store.add_fact("alpha")
    capsys.readouterr()  # drain anything from setup

    dispatch("mindkeep_pin", {"kind": "fact", "id": fact_id})
    dispatch("mindkeep_unpin", {"kind": "fact", "id": fact_id})
    dispatch("mindkeep_pin", {"kind": "fact", "id": 999_999})  # not_found
    dispatch("mindkeep_pin", {"kind": "memo", "id": 1})  # invalid_argument

    out, _err = capsys.readouterr()
    assert out == "", f"unexpected stdout output: {out!r}"


# ─────────────────────────── description sanity ───────────────────────────


def test_pin_descriptions_name_intent(tmp_path: Path) -> None:
    _, _store, all_tools, _ = _build_server(tmp_path, allow_writes=True)
    by_name = {t.name: t for t in all_tools}
    for name in ("mindkeep_pin", "mindkeep_unpin"):
        desc = by_name[name].description or ""
        assert len(desc) > 50
        # DESIGN §3.5 — descriptions name when-NOT-to-use as well as when-to.
        assert "Do NOT" in desc or "DO NOT" in desc

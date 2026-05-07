"""Unit + round-trip tests for the v0.4 MCP write tools (#34).

Covers DESIGN-v0.4.0 §3.3 (adapter responsibilities), §3.4 (stdio
invariant), §3.5 (descriptions), §9 (write-permission story), and the
§10 error-mapping table:

* Registration gating on ``--allow-writes``.
* ``mindkeep_add_fact`` happy path → ``{id, token_estimate, pinned, tags}``.
* Boundary dedup → ``error_kind="duplicate"`` with ``existing_id``.
* ``WriteGuardError`` → ``error_kind="write_guard"`` with structured fields.
* ``ValueError`` → ``error_kind="invalid_argument"`` (via schema validation).
* ``mindkeep_add_adr`` happy path.
* Stdout-purity invariant (no bytes on stdout during a tool call).
* Unhandled exception → JSON-RPC -32603 + traceback to stderr only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.shared.exceptions import McpError  # noqa: E402
from mcp.types import (  # noqa: E402
    METHOD_NOT_FOUND,
    CallToolRequest,
    CallToolRequestParams,
)

from mindkeep.mcp import tools_write  # noqa: E402
from mindkeep.memory_api import MemoryStore  # noqa: E402
from mindkeep.storage import StorageError, WriteGuardError  # noqa: E402


# ──────────────────────────── helpers ────────────────────────────


def _open_store(tmp_path: Path) -> MemoryStore:
    """Open a fresh per-test MemoryStore rooted at ``tmp_path``."""

    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    (project / ".mindkeep").mkdir(exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return MemoryStore.open(cwd=project, data_dir=data)


def _build_server(tmp_path: Path, *, allow_writes: bool):
    """Construct an in-process ``mcp.server.Server`` wired exactly like
    ``mindkeep.mcp.server.main`` does — but without spawning a subprocess
    or touching real stdio. Returns ``(srv, store, all_tools, dispatch)``
    where ``dispatch(name, args)`` invokes the registered call_tool
    handler and returns ``ServerResult`` (or raises ``McpError``).
    """

    import jsonschema

    from mcp.server import Server
    from mcp.types import ErrorData, ServerResult

    from mindkeep.mcp.tools import TOOLS
    from mindkeep.mcp.tools_write import build_write_tools, make_internal_error, make_tool_error

    store = _open_store(tmp_path)
    srv = Server("mindkeep-test")

    all_tools = list(TOOLS)
    all_handlers: dict = {}
    if allow_writes:
        wt, wh = build_write_tools()
        all_tools.extend(wt)
        all_handlers.update(wh)

    @srv.list_tools()
    async def _list_tools():
        return list(all_tools)

    async def _call(req):
        name = req.params.name
        args = req.params.arguments or {}
        handler = all_handlers.get(name)
        if handler is None:
            raise McpError(ErrorData(code=METHOD_NOT_FOUND, message=f"Unknown tool: {name}"))
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
    """Extract the structured payload from a ``ServerResult(CallToolResult)``."""

    return server_result.root.structuredContent


def _is_error(server_result) -> bool:
    return bool(server_result.root.isError)


# ───────────────────────── registration gating ─────────────────────────


def test_write_tools_absent_without_allow_writes(tmp_path: Path) -> None:
    """Read-only server (no ``--allow-writes``) → write tools not advertised."""

    _, _store, all_tools, _ = _build_server(tmp_path, allow_writes=False)
    names = {t.name for t in all_tools}
    assert "mindkeep_add_fact" not in names
    assert "mindkeep_add_adr" not in names


def test_write_tools_present_with_allow_writes(tmp_path: Path) -> None:
    _, _store, all_tools, _ = _build_server(tmp_path, allow_writes=True)
    names = {t.name for t in all_tools}
    assert {"mindkeep_add_fact", "mindkeep_add_adr"}.issubset(names)


def test_write_tool_in_readonly_server_returns_method_not_found(
    tmp_path: Path,
) -> None:
    """A client that calls a write tool against a read-only server must
    not see it succeed; the SDK converts ``McpError`` to JSON-RPC -32601.
    """

    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=False)
    with pytest.raises(McpError) as excinfo:
        dispatch("mindkeep_add_fact", {"value": "hello"})
    assert excinfo.value.error.code == METHOD_NOT_FOUND


# ─────────────────────────── add_fact happy path ───────────────────────


def test_add_fact_happy_path_returns_full_view(tmp_path: Path) -> None:
    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)

    res = dispatch(
        "mindkeep_add_fact",
        {"value": "always use UTC for timestamps", "tags": ["conventions"]},
    )
    assert not _is_error(res)
    payload = _payload(res)
    assert isinstance(payload["id"], int) and payload["id"] >= 1
    assert payload["pinned"] is False
    assert payload["tags"] == ["conventions"]
    assert payload["token_estimate"] > 0

    rows = store.list_facts()
    assert len(rows) == 1
    assert rows[0]["value"] == "always use UTC for timestamps"


def test_add_fact_pin_true_round_trips(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch(
        "mindkeep_add_fact", {"value": "pinned fact", "tags": [], "pin": True}
    )
    assert not _is_error(res)
    assert _payload(res)["pinned"] is True


# ───────────────────────────── add_fact dedup ──────────────────────────


def test_add_fact_dedup_returns_existing_id(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    first = dispatch(
        "mindkeep_add_fact",
        {"value": "use snake_case for python identifiers", "tags": ["style"]},
    )
    existing_id = _payload(first)["id"]

    second = dispatch(
        "mindkeep_add_fact",
        {"value": "use snake_case for python identifiers", "tags": ["style"]},
    )
    assert _is_error(second)
    p = _payload(second)
    assert p["error_kind"] == "duplicate"
    assert p["fields"]["existing_id"] == existing_id
    assert p["fields"]["tags"] == ["style"]
    assert "preview" in p["fields"]["value_preview"] or p["fields"]["value_preview"]


def test_add_fact_different_tags_not_dedup(tmp_path: Path) -> None:
    """Same ``value`` with different ``tags`` is allowed — design §9.3."""

    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    a = dispatch("mindkeep_add_fact", {"value": "v", "tags": ["a"]})
    b = dispatch("mindkeep_add_fact", {"value": "v", "tags": ["b"]})
    assert not _is_error(a)
    assert not _is_error(b)
    assert _payload(a)["id"] != _payload(b)["id"]


# ────────────────────────── add_fact WriteGuard ────────────────────────


def test_add_fact_write_guard_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Content over cap → tool-result error w/ structured WriteGuard payload.

    We tighten the per-fact cap via env var rather than synthesising a
    multi-thousand-character string so the test stays fast and readable.
    """

    monkeypatch.setenv("MINDKEEP_FACTS_TOKEN_CAP", "5")
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)

    res = dispatch(
        "mindkeep_add_fact",
        {"value": "this fact is intentionally far too long for the five-token cap"},
    )
    assert _is_error(res)
    p = _payload(res)
    assert p["error_kind"] == "write_guard"
    assert p["fields"]["kind"] == "fact"
    assert p["fields"]["cap"] == 5
    assert p["fields"]["pre_tokens"] >= p["fields"]["post_tokens"]
    assert "CLI" in p["fields"]["hint"] and "force" in p["fields"]["hint"]


# ────────────────────── add_fact invalid_argument paths ────────────────


def test_add_fact_missing_value_invalid_argument(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_add_fact", {"tags": ["x"]})
    assert _is_error(res)
    assert _payload(res)["error_kind"] == "invalid_argument"


def test_add_fact_empty_value_invalid_argument(tmp_path: Path) -> None:
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_add_fact", {"value": ""})
    assert _is_error(res)
    assert _payload(res)["error_kind"] == "invalid_argument"


def test_add_fact_wrong_type_invalid_argument(tmp_path: Path) -> None:
    """``tags`` must be ``array`` per schema; a string fails validation."""

    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch("mindkeep_add_fact", {"value": "ok", "tags": "nope"})
    assert _is_error(res)
    assert _payload(res)["error_kind"] == "invalid_argument"


# ─────────────────────────────── add_adr ──────────────────────────────


def test_add_adr_happy_path(tmp_path: Path) -> None:
    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch(
        "mindkeep_add_adr",
        {
            "title": "Adopt MCP for v0.4",
            "decision": "Ship a stdio MCP server in v0.4.",
            "rationale": "Native integration beats CLI prompts for agents.",
            "status": "accepted",
            "tags": ["arch"],
        },
    )
    assert not _is_error(res)
    p = _payload(res)
    assert p["status"] == "accepted"
    assert p["pinned"] is False
    assert p["token_estimate"] > 0
    assert isinstance(p["id"], int)

    rows = store.list_adrs()
    assert len(rows) == 1


def test_add_adr_write_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINDKEEP_ADRS_TOKEN_CAP", "5")
    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    res = dispatch(
        "mindkeep_add_adr",
        {
            "title": "long",
            "decision": "this is also pretty long for the five-token adr cap",
            "rationale": "and more rationale text to push us well over the limit",
        },
    )
    assert _is_error(res)
    p = _payload(res)
    assert p["error_kind"] == "write_guard"
    assert p["fields"]["kind"] == "adr"


# ───────────────────────── error-helper unit tests ─────────────────────


def test_make_tool_error_text_content_mirrors_structured() -> None:
    """The ``content[0]`` JSON text must round-trip to the same dict as
    ``structuredContent`` — hosts that don't surface structured output
    still see the full payload through the unstructured channel."""

    res = tools_write.make_tool_error(
        "duplicate", "msg", {"existing_id": 1, "tags": ["a"], "value_preview": "v"}
    )
    assert res.isError is True
    assert res.structuredContent["error_kind"] == "duplicate"
    parsed = json.loads(res.content[0].text)
    assert parsed == res.structuredContent


def test_writeguard_payload_shape() -> None:
    exc = WriteGuardError("over cap", kind="fact", cap=100, pre_tokens=200, post_tokens=180)
    res = tools_write._writeguard_error_payload(exc)  # noqa: SLF001
    p = res.structuredContent
    assert p["error_kind"] == "write_guard"
    assert p["fields"] == {
        "kind": "fact",
        "cap": 100,
        "pre_tokens": 200,
        "post_tokens": 180,
        "hint": p["fields"]["hint"],
    }


def test_storage_error_payload_shape() -> None:
    res = tools_write._storage_error_payload(StorageError("schema mismatch"))  # noqa: SLF001
    p = res.structuredContent
    assert p["error_kind"] == "storage"
    assert "reason" in p["fields"]
    assert p["fields"]["recoverable"] is False


# ─────────────────────────── stdio purity ─────────────────────────────


def test_no_stdout_writes_during_tool_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN-v0.4.0 §3.4: write tools must not emit a single byte on
    stdout. We exercise every error branch + happy path and assert the
    captured stdout is empty.

    ``capsys`` captures bytes Python writes to ``sys.stdout`` at the
    process level — anything emitted via ``print()``, low-level
    ``sys.stdout.write``, or a child ``traceback.print_*`` defaulting to
    stdout will be caught here.
    """

    _, _store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)
    capsys.readouterr()  # drain anything from setup

    dispatch("mindkeep_add_fact", {"value": "alpha"})
    dispatch("mindkeep_add_fact", {"value": "alpha"})  # dedup branch
    dispatch("mindkeep_add_fact", {"value": ""})  # invalid_argument branch
    dispatch(
        "mindkeep_add_adr",
        {"title": "t", "decision": "d", "rationale": "r"},
    )

    out, _err = capsys.readouterr()
    assert out == "", f"unexpected stdout output: {out!r}"


# ─────────────────────── unhandled-exception path ─────────────────────


def test_unhandled_exception_becomes_jsonrpc_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unexpected exception inside the handler must surface as an
    ``McpError(-32603)`` (so the SDK returns a JSON-RPC error response)
    and the traceback must go to stderr only — never into the tool
    result content (leak risk per DESIGN §10.2)."""

    _, store, _all_tools, dispatch = _build_server(tmp_path, allow_writes=True)

    class _Boom(RuntimeError):
        pass

    def _explode(*_a, **_kw):
        raise _Boom("unexpected database explosion: secret-token-AAA111")

    monkeypatch.setattr(store, "add_fact", _explode)
    capsys.readouterr()

    with pytest.raises(McpError) as excinfo:
        dispatch("mindkeep_add_fact", {"value": "hi"})

    from mcp.types import INTERNAL_ERROR

    assert excinfo.value.error.code == INTERNAL_ERROR
    assert "_Boom" in excinfo.value.error.message
    # The user-facing message must NOT carry the leaky exception text.
    assert "secret-token-AAA111" not in excinfo.value.error.message

    captured = capsys.readouterr()
    assert captured.out == "", "traceback leaked to stdout"
    assert "_Boom" in captured.err, "traceback should land on stderr"
    assert "secret-token-AAA111" in captured.err


# ────────────────────────── descriptions sanity ────────────────────────


def test_tool_descriptions_name_intent_not_phrases(tmp_path: Path) -> None:
    """DESIGN §3.5: descriptions describe intent, including ``do not``
    guidance. We check both tools have a non-trivial description and
    that it includes the ``Do NOT`` / ``DO NOT`` clause that survived
    the v0.3 → v0.4 carry-forward."""

    _, _store, all_tools, _ = _build_server(tmp_path, allow_writes=True)
    by_name = {t.name: t for t in all_tools}
    for name in ("mindkeep_add_fact", "mindkeep_add_adr"):
        desc = by_name[name].description or ""
        assert len(desc) >= 80, name
        assert "Do NOT" in desc or "DO NOT" in desc, name

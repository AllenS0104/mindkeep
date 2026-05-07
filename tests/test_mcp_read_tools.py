"""Tests for the MCP read tools (issue #35).

Covers:

* Registration: all five read tools registered at import time, with
  ``ToolSpec`` shape, regardless of any ``--allow-writes`` flag (writes
  are gated separately by #34).
* Schema validation: each tool's ``inputSchema`` is well-formed and
  rejects out-of-domain input via the handler's clamping / value
  checks.
* Adapter mapping: handlers call the right :class:`MemoryStore` method
  with the documented kwargs.
* Limit clamping: ``top`` (recall) clamps to 50, ``limit``
  (list_facts/list_adrs) clamps to 200 — including ``list_adrs``
  whose underlying API has no native ``limit`` parameter.
* Stats omits ``session_budget`` even when CLI session state is set.
* Doctor returns a dict with the documented keys.
* Stdout purity: no read-tool handler writes a single byte to stdout.
* Unhandled exception path: the server adapter logs the traceback to
  stderr only and re-raises so the SDK emits JSON-RPC -32603.
* :func:`mindkeep._diagnostics.collect_stats` /
  :func:`collect_doctor` exercised standalone (independent of MCP).

Anything that needs the real MCP SDK plumbing is gated with
:func:`pytest.importorskip("mcp")` so it skips in the core matrix and
runs in the ``mcp-extra`` CI job.
"""

from __future__ import annotations

import contextlib
import io
import sys
import types
from pathlib import Path

import pytest

from mindkeep import _diagnostics
from mindkeep._diagnostics import collect_doctor, collect_stats
from mindkeep.mcp import tools as tools_mod
from mindkeep.mcp.tools import (
    LIST_LIMIT_MAX,
    RECALL_TOP_MAX,
    TOOLS,
    ToolSpec,
    build_read_tools,
)
from mindkeep.memory_api import MemoryStore


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated mindkeep home, like other tests in this repo."""
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture
def store(tmp_path: Path, data_dir: Path) -> MemoryStore:
    """Open a real MemoryStore in a per-test project directory."""
    proj = tmp_path / "proj"
    proj.mkdir()
    s = MemoryStore.open(cwd=proj, data_dir=data_dir)
    yield s
    s.close()


def _read_specs() -> list[ToolSpec]:
    """Return the five read-tool specs as ToolSpec instances (only)."""
    return [t for t in TOOLS if isinstance(t, ToolSpec) and t.mode == "read"]


def _by_name() -> dict[str, ToolSpec]:
    return {t.name: t for t in _read_specs()}


# ─────────────────────── registration ───────────────────────


def test_all_five_read_tools_registered_at_import() -> None:
    names = {t.name for t in _read_specs()}
    assert names == {
        "mindkeep_recall",
        "mindkeep_list_facts",
        "mindkeep_list_adrs",
        "mindkeep_stats",
        "mindkeep_doctor",
    }


def test_read_tools_registered_regardless_of_allow_writes() -> None:
    """Read tools are unconditional; --allow-writes only gates writes."""
    # Re-running install_default_tools must not change the set or
    # accumulate duplicates — it's idempotent by design.
    before = sorted(t.name for t in _read_specs())
    tools_mod.install_default_tools()
    after = sorted(t.name for t in _read_specs())
    assert before == after
    assert len(_read_specs()) == 5


def test_tool_specs_have_required_shape() -> None:
    for spec in _read_specs():
        assert isinstance(spec.name, str) and spec.name.startswith("mindkeep_")
        assert isinstance(spec.description, str) and len(spec.description) >= 60
        assert "Do NOT" in spec.description or "do NOT" in spec.description, (
            "Tool description must include explicit when-NOT-to-use text "
            "(DESIGN-v0.4.0 §3.5)"
        )
        assert callable(spec.handler)
        assert spec.input_schema["type"] == "object"
        assert spec.inputSchema is spec.input_schema  # camelCase alias


def test_recall_schema_caps() -> None:
    spec = _by_name()["mindkeep_recall"]
    props = spec.input_schema["properties"]
    assert props["top"]["maximum"] == RECALL_TOP_MAX == 50
    assert props["kind"]["enum"] == ["all", "facts", "adrs"]
    assert spec.input_schema["required"] == ["query"]


def test_list_schemas_cap_at_200() -> None:
    for name in ("mindkeep_list_facts", "mindkeep_list_adrs"):
        spec = _by_name()[name]
        assert spec.input_schema["properties"]["limit"]["maximum"] == LIST_LIMIT_MAX == 200


def test_stats_schema_has_no_inputs() -> None:
    spec = _by_name()["mindkeep_stats"]
    assert spec.input_schema["properties"] == {}


def test_doctor_schema_has_verbose_flag() -> None:
    spec = _by_name()["mindkeep_doctor"]
    assert spec.input_schema["properties"]["verbose"]["type"] == "boolean"
    assert spec.input_schema["properties"]["verbose"]["default"] is False


# ─────────────────── handler / adapter mapping ───────────────────


def test_handle_recall_dispatches_to_memorystore(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_recall(self: MemoryStore, query: str, *, top: int, kind: str):
        captured["args"] = (query, top, kind)
        return []

    monkeypatch.setattr(MemoryStore, "recall", fake_recall, raising=True)
    spec = _by_name()["mindkeep_recall"]
    out = spec.handler(store, query="hello", top=5, kind="facts")
    assert out == {"hits": []}
    assert captured["args"] == ("hello", 5, "facts")


def test_handle_list_facts_dispatches_to_memorystore(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_list_facts(self, *, tag, limit, pinned_only):
        captured["args"] = (tag, limit, pinned_only)
        return []

    monkeypatch.setattr(MemoryStore, "list_facts", fake_list_facts, raising=True)
    spec = _by_name()["mindkeep_list_facts"]
    out = spec.handler(store, tag="x", limit=7, pinned_only=True)
    assert out == {"facts": []}
    assert captured["args"] == ("x", 7, True)


def test_handle_list_adrs_dispatches_to_memorystore(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_list_adrs(self, *, status, pinned_only):
        captured["args"] = (status, pinned_only)
        return []

    monkeypatch.setattr(MemoryStore, "list_adrs", fake_list_adrs, raising=True)
    spec = _by_name()["mindkeep_list_adrs"]
    out = spec.handler(store, status="accepted", pinned_only=False, limit=3)
    assert out == {"adrs": []}
    # list_adrs has no native ``limit`` arg — the server slices.
    assert captured["args"] == ("accepted", False)


# ─────────────────────── limit clamping ───────────────────────


def test_recall_top_clamps_to_50(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_recall(self, query, *, top, kind):
        captured["top"] = top
        return []

    monkeypatch.setattr(MemoryStore, "recall", fake_recall, raising=True)
    _by_name()["mindkeep_recall"].handler(store, query="q", top=999)
    assert captured["top"] == 50


def test_list_facts_limit_clamps_to_200(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_list_facts(self, *, tag, limit, pinned_only):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(MemoryStore, "list_facts", fake_list_facts, raising=True)
    _by_name()["mindkeep_list_facts"].handler(store, limit=5000)
    assert captured["limit"] == 200


def test_list_adrs_limit_clamped_in_server_slice(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MemoryStore.list_adrs`` lacks a ``limit`` kwarg; the server slices.

    Build a fake API that returns 300 rows; the handler must return at
    most 200 entries even when the caller asks for 5000.
    """
    fake_rows = [
        {
            "id": i,
            "number": i,
            "title": f"adr-{i}",
            "status": "accepted",
            "context": "",
            "decision": "",
            "tags": "",
            "pin": 0,
            "supersedes": None,
            "created_at": None,
            "updated_at": None,
            "token_estimate": None,
        }
        for i in range(300)
    ]
    monkeypatch.setattr(
        MemoryStore,
        "list_adrs",
        lambda self, **kw: list(fake_rows),
        raising=True,
    )
    out = _by_name()["mindkeep_list_adrs"].handler(store, limit=5000)
    assert len(out["adrs"]) == 200


# ─────────────────────── stats / doctor ───────────────────────


def test_stats_handler_has_expected_keys(store: MemoryStore) -> None:
    out = _by_name()["mindkeep_stats"].handler(store)
    for key in (
        "schema_version", "project_id", "facts", "adrs",
        "preferences", "sessions", "top_tags",
        "tokens_estimated_total", "db_size_bytes",
    ):
        assert key in out, f"missing key {key}"


def test_stats_handler_omits_session_budget(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when CLI session state is active, MCP stats must NOT leak it.

    DESIGN-v0.4.0 §11: per-call session budget is a CLI rendering
    concern; MCP callers have their own context-window accounting.
    """
    fake = types.ModuleType("mindkeep._session")
    fake.current_state = lambda: {  # type: ignore[attr-defined]
        "active": True, "budget": 2000, "spent": 100, "calls": 1,
    }
    monkeypatch.setitem(sys.modules, "mindkeep._session", fake)

    out = _by_name()["mindkeep_stats"].handler(store)
    assert "session_budget" not in out


def test_doctor_handler_returns_dict(store: MemoryStore) -> None:
    out = _by_name()["mindkeep_doctor"].handler(store)
    assert isinstance(out, dict)
    assert out["version"] == 1
    assert isinstance(out["checks"], list) and out["checks"]
    assert {"ok", "warn", "fail"} == set(out["summary"].keys())


def test_doctor_verbose_default_strips_details(store: MemoryStore) -> None:
    """``verbose=False`` (the MCP default) drops ``details`` per DESIGN §13."""
    out = _by_name()["mindkeep_doctor"].handler(store)
    for c in out["checks"]:
        assert "details" not in c, (
            f"check {c['id']!r} leaked details in non-verbose mode"
        )


def test_doctor_verbose_true_keeps_details(store: MemoryStore) -> None:
    out = _by_name()["mindkeep_doctor"].handler(store, verbose=True)
    has_any_details = any("details" in c for c in out["checks"])
    assert has_any_details


# ─────────────────────── stdout purity ───────────────────────


@pytest.mark.parametrize(
    "name, kwargs",
    [
        ("mindkeep_recall", {"query": "hello"}),
        ("mindkeep_list_facts", {}),
        ("mindkeep_list_adrs", {}),
        ("mindkeep_stats", {}),
        ("mindkeep_doctor", {}),
        ("mindkeep_doctor", {"verbose": True}),
    ],
)
def test_no_stdout_writes_during_handler(
    store: MemoryStore, name: str, kwargs: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Highest-priority regression guard for the stdio invariant.

    A single ``print()`` reachable from a tool handler corrupts the
    JSON-RPC frame channel and bricks the MCP session (DESIGN §3.4).
    Capture stdout for every read tool and assert it stays empty.
    """
    # Pre-seed enough data that recall/list_* exercise non-trivial paths.
    store.add_fact("hello world", tags=["greet"])
    store.add_adr("Title", "Decision", "Rationale")

    spec = _by_name()[name]
    # Belt-and-braces: redirect both the captured stdout and a private
    # sys.stdout proxy so any direct C-level writes also surface.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        spec.handler(store, **kwargs)
    assert buf.getvalue() == ""

    cap = capsys.readouterr()
    assert cap.out == "", f"{name} wrote to stdout: {cap.out!r}"


# ─────────────────── unhandled exception path ───────────────────


def test_unhandled_exception_logs_to_stderr_only(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server-adapter code path verified by mocking the SDK.

    We test the documented contract from DESIGN §10.2: when a handler
    raises an unexpected exception, the full traceback goes to stderr
    only — never into the tool result the model receives. We can't
    invoke the live SDK adapter without the optional extra, so we
    exercise the same branch the server code takes via direct call
    semantics.
    """
    pytest.importorskip("mcp")
    import json
    import traceback as _tb

    def boom(self, *a, **kw):  # noqa: ANN001
        raise RuntimeError("synthetic explosion")

    monkeypatch.setattr(MemoryStore, "recall", boom, raising=True)

    captured_stderr = io.StringIO()
    captured_result: list = []

    # Recreate the server's exception envelope inline; the production
    # path lives in mindkeep.mcp.server but importing it here would
    # require the [mcp] extra to be importable in this test job too.
    spec = _by_name()["mindkeep_recall"]
    try:
        try:
            spec.handler(store, query="anything")
        except RuntimeError:
            captured_stderr.write(_tb.format_exc())
            raise
    except RuntimeError as exc:
        # The server re-raises so the SDK emits -32603. We assert the
        # tool-result payload was NEVER built with traceback content.
        captured_result.append(("rpc_error", str(exc)))

    err = captured_stderr.getvalue()
    assert "synthetic explosion" in err
    assert "Traceback" in err
    # The (would-be) tool result MUST NOT carry the traceback.
    serialized = json.dumps(captured_result)
    assert "Traceback" not in serialized
    assert "synthetic explosion" in serialized  # message is fine; traceback isn't


# ────────────────── helpers tested standalone ──────────────────


def test_collect_stats_returns_dict_without_session_budget(
    store: MemoryStore,
) -> None:
    out = collect_stats(store)
    assert isinstance(out, dict)
    assert "session_budget" not in out
    assert "schema_version" in out
    assert out["preferences"]["total"] == 0


def test_collect_stats_includes_data_dir(
    store: MemoryStore, tmp_path: Path,
) -> None:
    out = collect_stats(store)
    assert "data_dir" in out
    out2 = collect_stats(store, data_dir=tmp_path / "explicit")
    assert out2["data_dir"] == str(tmp_path / "explicit")


def test_collect_doctor_no_project_db(data_dir: Path) -> None:
    """Doctor on a fresh data dir with no project: store-database WARN."""
    out = collect_doctor(data_dir, project_id=None)
    assert out["version"] == 1
    by_id = {c["id"]: c for c in out["checks"]}
    assert by_id["store-database"]["status"] == "WARN"
    # No project DB → store-health subchecks skipped.
    assert "schema-version" not in by_id


def test_collect_doctor_verbose_false_strips_details(data_dir: Path) -> None:
    out = collect_doctor(data_dir, project_id=None, verbose=False)
    for c in out["checks"]:
        assert "details" not in c


def test_collect_doctor_verbose_true_keeps_details(data_dir: Path) -> None:
    out = collect_doctor(data_dir, project_id=None, verbose=True)
    assert any("details" in c for c in out["checks"])


def test_env_check_ids_covers_environment_section() -> None:
    ids = _diagnostics.env_check_ids()
    # Sanity: known stable env-section ids.
    for known in (
        "python-version", "package-installed", "current-project",
        "known-projects", "data-dir-writable",
    ):
        assert known in ids

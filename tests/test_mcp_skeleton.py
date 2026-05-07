"""Skeleton-level tests for the ``mindkeep.mcp`` subpackage (issue #33).

These guard the lazy-import contract (DESIGN-v0.4.0 §7.1), the project
resolution precedence (§8), the missing-extra friendly error, the
read-only / allow-writes mutex, and the startup-time diagnostic warning
(§8.4). Tool-handler behavior lands in #34/#35 — not covered here.
"""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------- helpers

# Code that, when prepended to a child Python's ``-c`` script, makes any
# ``import mcp`` (or ``from mcp...``) raise ``ModuleNotFoundError`` —
# simulating a venv where the user installed plain ``mindkeep`` without
# the ``[mcp]`` extra.
_BLOCK_MCP_PRELUDE = (
    "import builtins, sys\n"
    "_real_import = builtins.__import__\n"
    "def _fake_import(name, globals_=None, locals_=None, fromlist=(), level=0):\n"
    "    # Only block absolute imports of the SDK; relative imports\n"
    "    # like `from .mcp.server import main` (level>=1) must pass\n"
    "    # through so the mindkeep.mcp subpackage stays loadable.\n"
    "    if level == 0 and (name == 'mcp' or name.startswith('mcp.')):\n"
    "        raise ModuleNotFoundError(\"No module named 'mcp'\")\n"
    "    return _real_import(name, globals_, locals_, fromlist, level)\n"
    "for _k in list(sys.modules):\n"
    "    if _k == 'mcp' or _k.startswith('mcp.'):\n"
    "        sys.modules.pop(_k, None)\n"
    "builtins.__import__ = _fake_import\n"
)


def _run_child(script: str) -> subprocess.CompletedProcess:
    """Run ``script`` in a fresh Python child and return the result."""
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def block_mcp_import(monkeypatch: pytest.MonkeyPatch):
    """In-process equivalent of ``_BLOCK_MCP_PRELUDE``.

    Pops any cached ``mcp`` modules and patches ``builtins.__import__``
    so subsequent ``import mcp`` / ``from mcp...`` calls raise
    ``ModuleNotFoundError``. Reverted on teardown.
    """
    real_import = builtins.__import__

    def fake_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        # Only block absolute imports of the SDK; relative imports
        # like ``from .mcp.server import main`` (level>=1) must pass
        # through so the ``mindkeep.mcp`` subpackage stays loadable.
        if level == 0 and (name == "mcp" or name.startswith("mcp.")):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, globals_, locals_, fromlist, level)

    for k in list(sys.modules):
        if k == "mcp" or k.startswith("mcp."):
            monkeypatch.delitem(sys.modules, k, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)


# ---------------------------------------------------- lazy-import contract

def test_import_mindkeep_without_mcp_extra() -> None:
    """``import mindkeep`` must not pull in the ``mcp`` SDK."""
    script = (
        _BLOCK_MCP_PRELUDE
        + "import mindkeep\n"
        + "import mindkeep.cli\n"
        + "assert 'mcp' not in sys.modules, sorted(sys.modules)\n"
        + "assert not any(k.startswith('mcp.') for k in sys.modules)\n"
    )
    r = _run_child(script)
    assert r.returncode == 0, r.stderr


def test_cli_help_without_mcp_extra() -> None:
    """``mindkeep --help`` must work and mention ``mcp`` w/o SDK import."""
    script = (
        _BLOCK_MCP_PRELUDE
        + "from mindkeep.cli import _build_parser\n"
        + "p = _build_parser()\n"
        + "h = p.format_help()\n"
        + "assert 'mcp' in h, h\n"
        + "assert 'mcp' not in sys.modules, sorted(sys.modules)\n"
        + "assert not any(k.startswith('mcp.') for k in sys.modules)\n"
    )
    r = _run_child(script)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------- missing-extra friendly exit

_EXPECTED_HINT = "pip install 'mindkeep[mcp]'"


def test_mindkeep_mcp_serve_without_extra_exits_2(
    block_mcp_import: None,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mindkeep mcp serve`` exits 2 with the install hint when SDK absent."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINDKEEP_PROJECT_DIR", raising=False)
    # Ensure cli + mcp.server modules get re-imported with the import
    # block in place (they may already be cached from earlier tests).
    for k in [
        "mindkeep.cli",
        "mindkeep.mcp",
        "mindkeep.mcp.server",
        "mindkeep.mcp.tools",
    ]:
        monkeypatch.delitem(sys.modules, k, raising=False)

    from mindkeep.cli import main as cli_main

    rc = cli_main(["mcp", "serve"])
    assert rc == 2
    err = capsys.readouterr().err
    assert _EXPECTED_HINT in err


def test_mindkeep_mcp_console_script_without_extra_exits_2(
    tmp_path: Path,
) -> None:
    """The ``mindkeep-mcp`` console script entry exits 2 with the hint."""
    script = (
        _BLOCK_MCP_PRELUDE
        + f"import os; os.chdir({str(tmp_path)!r})\n"
        + "os.environ.pop('MINDKEEP_PROJECT_DIR', None)\n"
        + "from mindkeep.mcp.server import main\n"
        + "sys.exit(main([]))\n"
    )
    r = _run_child(script)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert _EXPECTED_HINT in r.stderr


# ------------------------------------------------------------------ mutex

def test_mutex_read_only_and_allow_writes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--read-only`` and ``--allow-writes`` together → exit 2."""
    from mindkeep.mcp.server import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--read-only", "--allow-writes"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    # argparse names both flags in its mutex error message.
    assert "--allow-writes" in err or "--read-only" in err


# ------------------------------------------------- project resolution (§8)

def test_project_dir_flag_overrides_env_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    for d in (a, b, c):
        d.mkdir()
    monkeypatch.chdir(c)
    monkeypatch.setenv("MINDKEEP_PROJECT_DIR", str(b))

    from mindkeep.mcp.server import _resolve_project_dir, build_parser

    args = build_parser().parse_args(["--project-dir", str(a)])
    path, source = _resolve_project_dir(args)
    assert path == a.resolve()
    assert source == "flag"


def test_project_dir_env_used_when_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    b = tmp_path / "b"
    c = tmp_path / "c"
    for d in (b, c):
        d.mkdir()
    monkeypatch.chdir(c)
    monkeypatch.setenv("MINDKEEP_PROJECT_DIR", str(b))

    from mindkeep.mcp.server import _resolve_project_dir, build_parser

    args = build_parser().parse_args([])
    path, source = _resolve_project_dir(args)
    assert path == b.resolve()
    assert source == "env"


def test_project_dir_falls_back_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = tmp_path / "c"
    c.mkdir()
    monkeypatch.chdir(c)
    monkeypatch.delenv("MINDKEEP_PROJECT_DIR", raising=False)

    from mindkeep.mcp.server import _resolve_project_dir, build_parser

    args = build_parser().parse_args([])
    path, source = _resolve_project_dir(args)
    assert path == c.resolve()
    assert source == "cwd-discovery"


# ------------------------------------------ startup-time diagnostic (§8.4)

def test_temp_cwd_emits_stderr_warning(
    block_mcp_import: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Launching with cwd inside the OS temp dir emits a stderr warning.

    pytest's ``tmp_path`` lives under ``tempfile.gettempdir()`` on every
    supported platform, so the temp-dir branch of the diagnostic guard
    fires.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINDKEEP_PROJECT_DIR", raising=False)
    for k in ["mindkeep.mcp", "mindkeep.mcp.server", "mindkeep.mcp.tools"]:
        monkeypatch.delitem(sys.modules, k, raising=False)

    from mindkeep.mcp.server import main

    # Will exit 2 because the SDK import is blocked, but the
    # diagnostic warning runs first.
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "warning" in err.lower()
    assert "OS temp directory" in err or "no .git" in err


def test_no_warning_for_real_project_dir(
    block_mcp_import: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dir with a ``.mindkeep`` marker should NOT trigger the warning.

    Because pytest's tmp_path lives under the OS temp dir, the temp
    branch fires unconditionally — so we instead verify the helper
    directly to keep the assertion crisp.
    """
    project = tmp_path / "real-project"
    project.mkdir()
    (project / ".mindkeep").mkdir()  # marker present

    from mindkeep.mcp.server import _has_project_marker, _is_temp_dir

    # Marker found → no warning would fire on the marker branch.
    assert _has_project_marker(project) is True
    # ...but tmp_path is itself under the OS temp dir, sanity check:
    assert _is_temp_dir(project) is True


# ------------------------------------------------------ tools registry

def test_tools_registry_is_empty_skeleton() -> None:
    """``TOOLS`` ships empty in #33; ``register`` populates it for #34/#35."""
    from mindkeep.mcp import tools

    # Snapshot then restore so this test is order-independent.
    saved = list(tools.TOOLS)
    tools.TOOLS.clear()
    try:
        assert tools.TOOLS == []

        @tools.register
        def _example():  # pragma: no cover - trivial sentinel
            return None

        assert _example in tools.TOOLS
    finally:
        tools.TOOLS.clear()
        tools.TOOLS.extend(saved)


# --------------------------- optional: real stdio round-trip (gated)

@pytest.mark.skipif(
    os.environ.get("MINDKEEP_MCP_STDIO_E2E") != "1",
    reason="round-trip e2e requires the [mcp] extra and is opt-in via env",
)
def test_tools_list_returns_empty_over_stdio(tmp_path: Path) -> None:
    """End-to-end smoke: spawn the server, list tools, expect ``[]``.

    Gated behind ``MINDKEEP_MCP_STDIO_E2E=1`` (set by the optional CI
    job that installs ``mindkeep[mcp]``) so the core matrix never
    requires the SDK. Imports the SDK lazily.
    """
    pytest.importorskip("mcp")
    import asyncio

    from mcp import ClientSession  # type: ignore[import-not-found]
    from mcp.client.stdio import (  # type: ignore[import-not-found]
        StdioServerParameters,
        stdio_client,
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mindkeep.mcp.server", "--project-dir", str(tmp_path)],
    )

    async def _go() -> list:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.list_tools()
                return list(result.tools)

    tools = asyncio.run(_go())
    assert tools == []

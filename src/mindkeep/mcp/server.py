"""mindkeep MCP server entry point (skeleton).

================================================================
STDIO INVARIANT  (DESIGN-v0.4.0 §3.4)
----------------------------------------------------------------
In stdio MCP transport, **stdout is the JSON-RPC frame channel**.
ANY ``print()``, banner, log line, or ``traceback.print_exc()``
written to stdout will corrupt the protocol stream and brick the
session. All diagnostics in this module — and any code reachable
from a tool handler — MUST go to ``sys.stderr``.

This file deliberately uses ``sys.stderr.write(...)`` everywhere
and never touches ``sys.stdout``. Future contributors: keep it
that way.
================================================================

Lazy-import contract (DESIGN-v0.4.0 §7.1): importing this module
must not pull in the ``mcp`` SDK. The SDK is imported inside
:func:`main` so that ``mindkeep --help`` and ``import mindkeep``
keep working without the ``[mcp]`` extra installed.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from ..memory_api import MemoryStore

__all__ = ["main", "build_parser"]


# Single source of truth for the missing-extra hint. Both entry points
# (``mindkeep mcp serve`` and ``mindkeep-mcp``) end up here, so this
# message is what users see when they forgot ``pip install
# 'mindkeep[mcp]'``.
_INSTALL_HINT = (
    "mindkeep: MCP support requires the \"mcp\" extra. "
    "Install with: pip install 'mindkeep[mcp]'\n"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``mindkeep-mcp`` argparse parser.

    Kept free of any ``mcp`` SDK imports so the parser can be
    constructed (and ``--help`` rendered) in environments without the
    optional extra installed.
    """
    p = argparse.ArgumentParser(
        prog="mindkeep-mcp",
        description="Run the mindkeep MCP server over stdio.",
    )
    p.add_argument(
        "--project-dir",
        default=None,
        metavar="PATH",
        help=(
            "bind the server to PATH (overrides MINDKEEP_PROJECT_DIR "
            "and cwd discovery; see DESIGN-v0.4.0 §8.1)"
        ),
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "explicit read-only mode (default; alias / no-op for now — "
            "write tools land in #35)"
        ),
    )
    g.add_argument(
        "--allow-writes",
        action="store_true",
        help=(
            "enable write tools (off by default — see DESIGN-v0.4.0 §9). "
            "Without this flag, mindkeep_add_fact / mindkeep_add_adr are "
            "not registered and do not appear in tools/list."
        ),
    )
    return p


def _resolve_project_dir(args: argparse.Namespace) -> Tuple[Path, str]:
    """Return ``(resolved_path, id_source)`` per DESIGN §8.1.

    Precedence: ``--project-dir`` > ``MINDKEEP_PROJECT_DIR`` env >
    ``Path.cwd()``. Relative paths resolve against the server's
    startup cwd. Never raises.
    """
    raw = getattr(args, "project_dir", None)
    if raw:
        return Path(raw).expanduser().resolve(), "flag"
    env = os.environ.get("MINDKEEP_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve(), "env"
    return Path.cwd().resolve(), "cwd-discovery"


def _is_temp_dir(path: Path) -> bool:
    """True iff ``path`` is the OS temp dir or lives beneath it."""
    try:
        tmp = Path(tempfile.gettempdir()).resolve()
    except OSError:  # pragma: no cover - defensive
        return False
    try:
        path.relative_to(tmp)
        return True
    except ValueError:
        return False


def _has_project_marker(path: Path) -> bool:
    """True iff ``path`` or any ancestor has a ``.git`` or ``.mindkeep``."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() or (candidate / ".mindkeep").exists():
            return True
    return False


def _emit_startup_diagnostic(path: Path, id_source: str) -> None:
    """Per DESIGN §8.4: warn to stderr if the resolved dir looks bogus.

    The warning is informational — the server still starts. The point
    is to make "agent silently bound to my home dir" diagnosable
    instead of invisible.
    """
    home: Optional[Path]
    try:
        home = Path.home().resolve()
    except RuntimeError:  # pragma: no cover - HOME may be unset on weird systems
        home = None

    reason: Optional[str] = None
    if home is not None and path == home:
        reason = "user home directory"
    elif _is_temp_dir(path):
        reason = "OS temp directory"
    elif not _has_project_marker(path):
        reason = "no .git or .mindkeep marker found"

    if reason is not None:
        sys.stderr.write(
            f"mindkeep-mcp: warning: resolved project dir {path} looks "
            f"like {reason}; agent reads/writes may go to an "
            f"unexpected store. (id_source={id_source})\n"
        )


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``mindkeep-mcp`` and ``mindkeep mcp serve``.

    Skeleton lifecycle (#33): parse args → resolve project dir →
    optional stderr warning → lazy-import ``mcp`` SDK → open
    :class:`MemoryStore` → run an empty-tool stdio server → close
    on shutdown. Tool handlers are added in #34/#35.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    project_dir, id_source = _resolve_project_dir(args)
    _emit_startup_diagnostic(project_dir, id_source)

    # Lazy SDK import — DESIGN §7.1 hard requirement. Catching
    # ``ImportError`` (parent of ``ModuleNotFoundError``) covers the
    # case where ``mcp`` is installed but partially broken.
    try:
        from mcp.server import Server  # type: ignore[import-not-found]
        from mcp.server.stdio import stdio_server  # type: ignore[import-not-found]
        from mcp.shared.exceptions import McpError  # type: ignore[import-not-found]
        from mcp.types import (  # type: ignore[import-not-found]
            CallToolRequest,
            ErrorData,
            METHOD_NOT_FOUND,
            ServerResult,
        )
    except ImportError:
        sys.stderr.write(_INSTALL_HINT)
        return 2

    # Surface the resolution to stderr so hosts (and humans tailing
    # logs) can diagnose "wait, why is the agent reading the wrong
    # project". Mirrors the data the ``mindkeep://project`` resource
    # will expose in #34 (DESIGN §8.3).
    sys.stderr.write(
        f"mindkeep-mcp: project_dir={project_dir} id_source={id_source} "
        f"allow_writes={bool(args.allow_writes)}\n"
    )

    # Local imports to keep module-load time minimal and to keep this
    # block out of the ``--help`` path.
    import asyncio

    import jsonschema  # type: ignore[import-not-found]

    from .tools import TOOLS
    from .tools_write import build_write_tools, make_internal_error, make_tool_error

    store = MemoryStore.open(cwd=project_dir)
    try:
        srv = Server("mindkeep")

        # Build the registry. Read tools (#35) will hang off this same
        # composition point. Write tools register only when the server
        # was started with ``--allow-writes`` (DESIGN §9.1) — without
        # the flag they are not announced in ``tools/list`` at all, so
        # the model literally cannot try to call them.
        all_tools = list(TOOLS)
        all_handlers: dict = {}
        if args.allow_writes:
            wtools, whandlers = build_write_tools()
            all_tools.extend(wtools)
            all_handlers.update(whandlers)

        @srv.list_tools()  # type: ignore[misc]
        async def _list_tools():  # noqa: D401 - SDK callback shape
            return list(all_tools)

        # We install a custom ``CallToolRequest`` handler directly
        # rather than using ``@srv.call_tool()`` because the SDK's
        # decorator catches *every* ``Exception`` and converts it to a
        # tool-result error. DESIGN §10.2 requires unhandled exceptions
        # to surface as JSON-RPC -32603 with the traceback going to
        # stderr only — ``McpError`` is the SDK's escape hatch for that
        # path (re-raised by ``Server._handle_request``).
        async def _call_tool_handler(req):  # type: ignore[no-untyped-def]
            name = req.params.name
            arguments = req.params.arguments or {}
            handler = all_handlers.get(name)
            if handler is None:
                # Method-not-found at the protocol layer — the model
                # asked for a tool we did not register. Standard
                # JSON-RPC -32601.
                raise McpError(
                    ErrorData(
                        code=METHOD_NOT_FOUND,
                        message=f"Unknown tool: {name}",
                    )
                )

            tool_def = next((t for t in all_tools if t.name == name), None)
            if tool_def is not None:
                try:
                    jsonschema.validate(
                        instance=arguments, schema=tool_def.inputSchema
                    )
                except jsonschema.ValidationError as exc:
                    # Schema validation failure: §10 invalid_argument,
                    # surfaced as a tool-result error so the model can
                    # recover (edit the call and retry).
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
                result = await handler(store, arguments)
            except McpError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                # ``make_internal_error`` writes the full traceback to
                # stderr (NEVER stdout — see DESIGN §3.4) and returns a
                # fresh ``McpError(ErrorData(code=-32603, ...))`` whose
                # message contains only the exception class name.
                raise make_internal_error(exc) from None

            return ServerResult(result)

        srv.request_handlers[CallToolRequest] = _call_tool_handler

        async def _run() -> None:
            async with stdio_server() as (read_stream, write_stream):
                await srv.run(
                    read_stream,
                    write_stream,
                    srv.create_initialization_options(),
                )

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:  # pragma: no cover - signal path
            pass
    finally:
        store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

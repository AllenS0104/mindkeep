"""mindkeep MCP server package.

This subpackage is the v0.4 MCP integration for mindkeep. It is *only*
imported when the user explicitly runs the ``mindkeep mcp serve``
subcommand or the ``mindkeep-mcp`` console script. The MCP SDK itself
lives behind the ``[mcp]`` optional-dependency extra and is imported
lazily inside :func:`mindkeep.mcp.server.main` — see DESIGN-v0.4.0 §7.1.

Importing this package MUST NOT pull in the ``mcp`` SDK; that invariant
is asserted by ``tests/test_mcp_skeleton.py``.
"""

__all__: list[str] = []

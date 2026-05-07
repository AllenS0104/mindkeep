"""MCP tool registry.

Skeleton-only in this ticket (#33). The actual tool handlers
(``recall``, ``list_facts``, ``add_fact``, ...) are filled in by #34
(read tools) and #35 (write tools).

The registry is intentionally tiny: a list of registered tool
descriptors, plus a :func:`register` decorator subsequent tickets can
hang their handlers off of without further plumbing changes here.

This module MUST NOT import the ``mcp`` SDK at top level — it is
imported by :mod:`mindkeep.mcp.server`, which itself must remain
SDK-free until ``main()`` is called (see DESIGN-v0.4.0 §7.1).
"""

from __future__ import annotations

from typing import Any, Callable, List

# List of registered tool descriptors. Each entry's concrete shape is
# defined by #34/#35; for the skeleton we only need it to exist and be
# empty so ``tools/list`` returns ``[]``.
TOOLS: List[Any] = []


def register(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that appends ``handler`` to :data:`TOOLS`.

    Stored as-is; #34/#35 decide the descriptor shape. The decorator
    is identity-preserving so handlers remain importable and testable
    on their own.
    """
    TOOLS.append(handler)
    return handler


__all__ = ["TOOLS", "register"]

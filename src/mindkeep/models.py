"""Frozen data models shared by mindkeep.

Only the models needed by the project-id resolver are defined here for now;
downstream agents will extend this module with Fact / ADR / Preference /
SessionSummary per ARCHITECTURE.md §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IdSource = Literal["git_remote", "cwd_hash"]


@dataclass(frozen=True, slots=True)
class ProjectId:
    """Stable identifier for a project's on-disk memory store.

    See ARCHITECTURE.md §5 — the ``id`` field is the only value used as a
    filesystem key; ``display_name`` is purely informational.
    """

    id: str              # always 12 lowercase hex chars
    display_name: str    # human-friendly; never used as path/key
    source: IdSource
    origin: str          # raw git URL (normalized) or absolute cwd that was hashed


__all__ = ["ProjectId", "IdSource"]

"""Project identity resolution.

Implements the deterministic, offline algorithm specified in
ARCHITECTURE.md §5:

1. Walk up from ``cwd`` searching for ``.git`` (dir or file worktree pointer).
2. If found, try ``git -C <root> config --get remote.origin.url`` via
   ``subprocess.run`` (3s timeout, ``check=False``). Normalize the URL and
   hash it.
3. Otherwise fall back to hashing the absolute forward-slashed cwd.

Zero runtime dependencies; git failures are always silent.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

from .models import ProjectId

# Timeout for the single ``git config`` subprocess call. Kept small because
# the call is purely local (reads ``.git/config``); a slow git here indicates
# a broken environment and we prefer the cwd_hash fallback over blocking.
_GIT_TIMEOUT_SECONDS = 3

# Matches ``[user[:pass]@]host:owner/repo[...]`` — the scp-like ssh form.
# Host must not contain ``/`` (otherwise it's an https path). We deliberately
# do NOT match ``ssh://host/...`` here; that goes through the URL branch.
_SCP_SSH_RE = re.compile(
    r"^(?:(?P<auth>[^@]+)@)?(?P<host>[^:/@]+):(?P<path>[^:].*)$"
)


def _find_git_root(start: Path) -> Optional[Path]:
    """Return the directory containing ``.git`` at or above ``start``.

    ``.git`` may be a directory (normal repo) or a file (worktrees /
    submodules) — both count. Returns ``None`` if no repo is found.
    """
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _read_remote_origin(git_root: Path) -> Optional[str]:
    """Return the ``remote.origin.url`` value, or ``None`` on any failure.

    All errors — missing git binary, timeout, non-zero exit, empty output —
    are swallowed. The caller treats ``None`` as "no remote, use fallback".
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(git_root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    url = result.stdout.strip()
    return url or None


def _strip_git_suffix(path: str) -> str:
    """Drop a trailing ``.git`` (exactly one) from a path-like string."""
    if path.endswith(".git"):
        return path[: -len(".git")]
    return path


def _normalize_remote_url(url: str) -> str:
    """Normalize a git remote URL to ``host/owner/repo`` (lowercase host).

    Handles the three forms we care about:

    * ``https://[user:pass@]host[:port]/path`` (also ``http://``, ``ssh://``,
      ``git://``) — parsed by stripping scheme/auth, lowercasing host.
    * ``git@host:owner/repo`` — the scp-like ssh form.
    * Anything else — returned stripped of ``.git`` and any surrounding
      whitespace, as a best-effort stable key.

    Query strings and fragments are dropped. Trailing ``.git`` is removed.
    """
    u = url.strip()

    # 1. URL with explicit scheme.
    if "://" in u:
        scheme, rest = u.split("://", 1)
        # Drop query / fragment.
        for sep in ("?", "#"):
            if sep in rest:
                rest = rest.split(sep, 1)[0]
        # Split host from path.
        if "/" in rest:
            authority, path = rest.split("/", 1)
        else:
            authority, path = rest, ""
        # Strip auth.
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        # Strip port.
        host = authority.split(":", 1)[0].lower()
        path = _strip_git_suffix(path.strip("/"))
        return f"{host}/{path}" if path else host

    # 2. scp-like ssh: user@host:path
    m = _SCP_SSH_RE.match(u)
    if m:
        host = m.group("host").lower()
        path = _strip_git_suffix(m.group("path").strip("/"))
        return f"{host}/{path}" if path else host

    # 3. Fallback — still strip .git so bare paths hash stably.
    return _strip_git_suffix(u)


def _display_name_from_url(normalized: str) -> str:
    """Return the last path segment of a normalized URL as display name."""
    if "/" in normalized:
        return normalized.rsplit("/", 1)[1] or normalized
    return normalized


def _hash12(value: str) -> str:
    """Return the first 12 hex chars of ``sha256(value)``."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _cwd_origin(cwd: Path) -> str:
    """Canonical cwd origin string: absolute, forward-slashed, case-normalized.

    On Windows, filesystems are case-insensitive, so ``C:\\Foo`` and
    ``c:\\foo`` must hash to the same id. We lowercase the drive letter and
    the whole path on Windows only (POSIX paths stay case-sensitive, which
    matches their filesystem semantics).
    """
    posix = cwd.as_posix()
    # Windows paths look like ``C:/Users/...``; normalize to lowercase.
    # Heuristic: drive-letter colon at index 1.
    if len(posix) >= 2 and posix[1] == ":":
        posix = posix.lower()
    return posix


def resolve_project_id(cwd: Path | None = None) -> ProjectId:
    """Resolve the ``ProjectId`` for ``cwd`` (defaults to ``Path.cwd()``).

    Never raises. Network-free. Deterministic: two invocations with the same
    filesystem state produce identical output.
    """
    base = (cwd if cwd is not None else Path.cwd()).resolve()

    git_root = _find_git_root(base)
    if git_root is not None:
        raw_url = _read_remote_origin(git_root)
        if raw_url:
            normalized = _normalize_remote_url(raw_url)
            return ProjectId(
                id=_hash12(normalized),
                display_name=_display_name_from_url(normalized),
                source="git_remote",
                origin=normalized,
            )

    origin = _cwd_origin(base)
    return ProjectId(
        id=_hash12(origin),
        display_name=base.name or origin,
        source="cwd_hash",
        origin=origin,
    )


__all__ = ["resolve_project_id", "ProjectId"]

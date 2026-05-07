"""Integration snippet templates for `mindkeep integrate <target>`.

Each template is a ready-to-paste markdown block that instructs an AI coding
agent how to use mindkeep as project memory: silent session-start dumps,
targeted recall, and proactive capture via the Python API. Templates are kept
deliberately minimal and free of user-specific paths so they're safe to drop
into any agent-instructions file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

CLAUDE = """\
# Project memory (mindkeep)

At the start of every session, if `mindkeep` is on PATH and the current working
directory is **not** the user's home directory or a system temp dir, silently run:

- `mindkeep show --kind facts`
- `mindkeep show --kind adrs`
- `mindkeep show --kind sessions --limit 3`

Treat the output as authoritative project context for this Claude session. Do
not announce the lookup unless results are non-empty — if there are facts or
ADRs, briefly summarize them as "🧠 mindkeep recall: …" so the user knows the
context was loaded. If results are empty, stay silent.

If `mindkeep` is not installed, skip silently.

## Targeted lookup

Before answering any non-trivial question about this project, run:

- `mindkeep recall "<query>"` — full-text search across facts and ADRs.

## When to write to mindkeep

Capture durable signal proactively (don't wait for the user to ask):

- **Facts** — non-obvious project truths worth remembering across sessions:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_fact('<content>', tags=['<tag>'])"
  ```

- **ADRs** — architectural decisions with rationale:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_adr(title='<title>', decision='<what>', rationale='<why>')"
  ```

- **Preferences** — durable user/project settings:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().set_preference('<key>', '<value>')"
  ```

Tell the user concisely when you've recorded something:
"📝 Saved to mindkeep: <fact|ADR|pref> — <one-line summary>"

## Don't capture

- Transient debug output, command results, or session-specific scratch
- Anything the user explicitly says is one-off
- Secrets, credentials, tokens — mindkeep is local plaintext SQLite
"""

COPILOT = """\
# Project memory (mindkeep)

This section instructs GitHub Copilot agents to use mindkeep as durable
project memory across sessions.

At the start of every session, if `mindkeep` is on PATH and the current working
directory is **not** the user's home directory or a system temp dir, silently run:

- `mindkeep show --kind facts`
- `mindkeep show --kind adrs`
- `mindkeep show --kind sessions --limit 3`

Treat the output as authoritative project context for this session. Do not
announce the lookup unless results are non-empty — if there are facts or ADRs,
briefly summarize them as "🧠 mindkeep recall: …" so the user knows the
context was loaded. If results are empty, stay silent.

If `mindkeep` is not installed, skip silently.

## Targeted lookup

Before answering any non-trivial question about this project, run:

- `mindkeep recall "<query>"` — full-text search across facts and ADRs.

## When to write to mindkeep

Capture durable signal proactively (don't wait for the user to ask):

- **Facts** — non-obvious truths about this project worth remembering:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_fact('<content>', tags=['<tag>'])"
  ```

- **ADRs** — architectural decisions with rationale:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_adr(title='<title>', decision='<what>', rationale='<why>')"
  ```

- **Preferences** — user-scoped or project-scoped settings:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().set_preference('<key>', '<value>')"
  ```

Tell the user concisely when you've recorded something:
"📝 Saved to mindkeep: <fact|ADR|pref> — <one-line summary>"

## Don't capture

- Transient debug output, command results, or session-specific scratch
- Anything the user explicitly says is one-off
- Secrets, credentials, tokens — mindkeep is local plaintext SQLite
"""

CURSOR = """\
# Project memory (mindkeep)

You are operating inside Cursor. Use mindkeep as durable project memory across
sessions and chats.

At the start of every chat or composer session, if `mindkeep` is on PATH and
the workspace root is **not** the user's home directory or a system temp dir,
silently run in the integrated terminal:

- `mindkeep show --kind facts`
- `mindkeep show --kind adrs`
- `mindkeep show --kind sessions --limit 3`

Treat the output as authoritative project context. Do not announce the lookup
unless results are non-empty — if there are facts or ADRs, briefly summarize
them as "🧠 mindkeep recall: …". If results are empty, stay silent.

If `mindkeep` is not installed, skip silently.

## Targeted lookup

Before answering any non-trivial question about this project, run:

- `mindkeep recall "<query>"` — full-text search across facts and ADRs.

## When to write to mindkeep

Capture durable signal proactively:

- **Facts** — non-obvious project truths worth remembering:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_fact('<content>', tags=['<tag>'])"
  ```

- **ADRs** — architectural decisions with rationale:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_adr(title='<title>', decision='<what>', rationale='<why>')"
  ```

- **Preferences** — durable settings (style, tooling choices):
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().set_preference('<key>', '<value>')"
  ```

Tell the user concisely when you've recorded something:
"📝 Saved to mindkeep: <fact|ADR|pref> — <one-line summary>"

## Don't capture

- Transient debug output, command results, or scratch
- Anything explicitly one-off
- Secrets — mindkeep is local plaintext SQLite
"""

GENERIC = """\
# Project memory (mindkeep)

These instructions are vendor-neutral and apply to any AI coding agent that
can run shell commands and Python in the user's workspace.

At the start of every session, if `mindkeep` is on PATH and the current working
directory is **not** the user's home directory or a system temp dir, silently run:

- `mindkeep show --kind facts`
- `mindkeep show --kind adrs`
- `mindkeep show --kind sessions --limit 3`

Treat the output as authoritative project context. Do not announce the lookup
unless results are non-empty — if there are facts or ADRs, briefly summarize
them as "🧠 mindkeep recall: …" so the user knows the context was loaded. If
results are empty, stay silent.

If `mindkeep` is not installed, skip silently.

## Targeted lookup

Before answering any non-trivial question about this project, run:

- `mindkeep recall "<query>"` — full-text search across facts and ADRs.

## When to write to mindkeep

Capture durable signal proactively (don't wait for the user to ask):

- **Facts** — non-obvious project truths worth remembering across sessions:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_fact('<content>', tags=['<tag>'])"
  ```

- **ADRs** — architectural decisions with rationale:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().add_adr(title='<title>', decision='<what>', rationale='<why>')"
  ```

- **Preferences** — durable user/project settings:
  ```
  python -c "from mindkeep import MemoryStore; MemoryStore.open().set_preference('<key>', '<value>')"
  ```

Tell the user concisely when you've recorded something:
"📝 Saved to mindkeep: <fact|ADR|pref> — <one-line summary>"

## Don't capture

- Transient debug output, command results, or session-specific scratch
- Anything the user explicitly says is one-off
- Secrets, credentials, tokens — mindkeep is local plaintext SQLite
"""

TARGETS: dict[str, str] = {
    "claude": CLAUDE,
    "copilot": COPILOT,
    "cursor": CURSOR,
    "generic": GENERIC,
}

# MCP-aware targets emit JSON snippets (or merge JSON config files) rather
# than markdown agent-instruction blocks. They live in a separate dispatch
# path in cli._cmd_integrate; the four targets above are unaffected.
MCP_TARGETS: tuple[str, ...] = ("claude-desktop", "cursor-mcp", "continue-mcp")

MCP_TARGET_DESCRIPTIONS: dict[str, str] = {
    "claude": "Claude Code / claude.ai agent-instruction markdown block",
    "copilot": "GitHub Copilot agent-instruction markdown block",
    "cursor": "Cursor agent-instruction markdown block",
    "generic": "vendor-neutral agent-instruction markdown block",
    "claude-desktop": (
        "Claude Desktop MCP config (claude_desktop_config.json, JSON merge)"
    ),
    "cursor-mcp": (
        "Cursor MCP config (~/.cursor/mcp.json, JSON merge)"
    ),
    "continue-mcp": (
        "Continue MCP config (~/.continue/config.json, snippet only)"
    ),
}


def render(target: str) -> str:
    """Return the snippet for *target*. Raises KeyError if unknown."""
    return TARGETS[target]


def supported() -> list[str]:
    """Return the list of supported markdown target names (stable order)."""
    return ["claude", "copilot", "cursor", "generic"]


def supported_all() -> list[str]:
    """Return all supported target names (markdown + MCP), in stable order."""
    return [*supported(), *MCP_TARGETS]


# ────────────────────────── MCP target builders ──────────────────────────
#
# These are pure data-returning helpers: they never print, never write, and
# never read the filesystem (the cwd is captured by the caller and passed
# in). The CLI layer (`_cmd_integrate`) is responsible for I/O and for
# routing to merge_json / write atomically. See DESIGN-v0.4.0.md §12.


_WRITES_HINT = (
    "to enable write tools, add \"--allow-writes\" to args (read-only by "
    "default — see DESIGN-v0.4.0.md §9.1)"
)


def mcp_snippet(target: str, project_dir: str) -> dict[str, Any]:
    """Return the JSON-serialisable snippet for an MCP *target*.

    The snippet always bakes the *project_dir* (the cwd at integrate-time
    or an explicit ``--project-dir``) into the generated config — see
    DESIGN-v0.4.0.md §8.6 / §12.1. It never includes ``--allow-writes``;
    users opt into writes by hand-editing.

    For ``claude-desktop`` and ``cursor-mcp`` the returned dict is the
    full config skeleton (with ``mcpServers.mindkeep`` populated). For
    ``continue-mcp`` it's the Continue-shaped config skeleton (with the
    ``modelContextProtocolServers`` list containing one entry).
    """
    if target in ("claude-desktop", "cursor-mcp"):
        return {
            "mcpServers": {
                "mindkeep": {
                    "command": "mindkeep-mcp",
                    "args": [],
                    "env": {"MINDKEEP_PROJECT_DIR": project_dir},
                }
            }
        }
    if target == "continue-mcp":
        return {
            "experimental": {
                "modelContextProtocolServers": [
                    {
                        "transport": {
                            "type": "stdio",
                            "command": "mindkeep-mcp",
                            "args": ["--project-dir", project_dir],
                        }
                    }
                ]
            }
        }
    raise KeyError(target)


def mcp_snippet_text(target: str, project_dir: str) -> str:
    """Return the human-pasteable JSON text for an MCP *target* snippet."""
    return json.dumps(mcp_snippet(target, project_dir), indent=2) + "\n"


def default_config_path(target: str) -> Path:
    """Return the OS-appropriate default host config path for *target*.

    Used when the user passes ``--in-place`` (no explicit ``--out PATH``).
    """
    home = Path.home()
    if target == "claude-desktop":
        if sys.platform == "win32":
            base = os.environ.get("APPDATA")
            root = Path(base) if base else home / "AppData" / "Roaming"
            return root / "Claude" / "claude_desktop_config.json"
        if sys.platform == "darwin":
            return (
                home / "Library" / "Application Support"
                / "Claude" / "claude_desktop_config.json"
            )
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if target == "cursor-mcp":
        return home / ".cursor" / "mcp.json"
    if target == "continue-mcp":
        return home / ".continue" / "config.json"
    raise KeyError(target)


# ─────────────────────────── JSON merge helpers ──────────────────────────


_JSONC_LINE_COMMENT = re.compile(r'(?<!:)//')
_JSONC_BLOCK_COMMENT = re.compile(r'/\*')


class JsoncDetectedError(ValueError):
    """Raised when a config file appears to contain JSONC comments."""


class MindkeepEntryExistsError(RuntimeError):
    """Raised when ``mcpServers.mindkeep`` is already present and no --force."""


def _looks_like_jsonc(raw: str) -> bool:
    """Return True if *raw* contains line or block comments outside strings.

    Cheap heuristic — strips string literals first, then looks for ``//``
    or ``/*``. Good enough to refuse JSONC merges with a helpful error
    rather than silently corrupting the user's config.
    """
    # Strip double-quoted strings (handles escapes) and single-line
    # JSON-strings; this is conservative enough to not false-positive on
    # URLs like "https://" living inside a value.
    no_strings = re.sub(r'"(?:\\.|[^"\\])*"', '""', raw)
    return bool(
        _JSONC_LINE_COMMENT.search(no_strings)
        or _JSONC_BLOCK_COMMENT.search(no_strings)
    )


def load_json_config(path: Path) -> dict[str, Any]:
    """Load *path* as strict JSON, returning ``{}`` for missing/empty files.

    Refuses JSONC (raises :class:`JsoncDetectedError`).
    """
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    if _looks_like_jsonc(raw):
        raise JsoncDetectedError(
            f"{path} appears to contain JSONC comments (// or /*); "
            f"mindkeep refuses in-place merge for JSONC. Re-run without "
            f"--out to print the snippet to stdout, then paste it manually."
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{path} is not valid JSON: {e.msg} (line {e.lineno}, col "
            f"{e.colno}). Refusing to overwrite a file we can't parse; "
            f"re-run without --out to print the snippet to stdout."
        ) from e
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} top-level value is not a JSON object (found "
            f"{type(data).__name__}); refusing to merge."
        )
    return data


def merge_mcp_config(
    existing: dict[str, Any],
    target: str,
    project_dir: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Merge the mindkeep MCP entry into *existing* config for *target*.

    Preserves all unrelated top-level keys and unrelated ``mcpServers``
    entries. If ``mcpServers.mindkeep`` already exists and ``force`` is
    False, raises :class:`MindkeepEntryExistsError`. The returned dict is
    a new object — the input is not mutated.
    """
    if target not in ("claude-desktop", "cursor-mcp"):
        raise KeyError(
            f"merge_mcp_config: unsupported target {target!r} "
            f"(only claude-desktop and cursor-mcp support in-place merge)"
        )

    merged: dict[str, Any] = dict(existing)
    servers_in = merged.get("mcpServers")
    if servers_in is not None and not isinstance(servers_in, dict):
        raise ValueError(
            "existing 'mcpServers' is not a JSON object; refusing to merge."
        )
    servers: dict[str, Any] = dict(servers_in) if servers_in else {}

    if "mindkeep" in servers and not force:
        raise MindkeepEntryExistsError(
            "a 'mindkeep' entry already exists in mcpServers; pass --force "
            "to overwrite (other servers will be preserved)."
        )

    snippet = mcp_snippet(target, project_dir)
    servers["mindkeep"] = snippet["mcpServers"]["mindkeep"]
    merged["mcpServers"] = servers
    return merged


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write *data* to *path* atomically: tmp file in same dir, then rename.

    The caller is responsible for backing up the previous file (see
    :func:`backup_file`) before invoking this — atomic_write_json itself
    only guarantees torn-write safety on the destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def backup_file(path: Path) -> Path | None:
    """Copy *path* to ``<path>.bak`` (overwriting any prior backup).

    Returns the backup path, or ``None`` if *path* didn't exist (nothing
    to back up). Pure file copy — no JSON parsing, so a malformed file is
    preserved verbatim.
    """
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".bak")
    bak.write_bytes(path.read_bytes())
    return bak

"""Integration snippet templates for `mindkeep integrate <target>`.

Each template is a ready-to-paste markdown block that instructs an AI coding
agent how to use mindkeep as project memory: silent session-start dumps,
targeted recall, and proactive capture via the Python API. Templates are kept
deliberately minimal and free of user-specific paths so they're safe to drop
into any agent-instructions file.
"""

from __future__ import annotations

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


def render(target: str) -> str:
    """Return the snippet for *target*. Raises KeyError if unknown."""
    return TARGETS[target]


def supported() -> list[str]:
    """Return the list of supported target names in stable order."""
    return ["claude", "copilot", "cursor", "generic"]

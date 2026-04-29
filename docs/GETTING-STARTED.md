# Getting Started with mindkeep

> 🌐 **Languages**: **English** · [中文](GETTING-STARTED.zh.md)

A single-page, end-to-end guide: **Install → Use → Troubleshoot**. If you've used `agent-memory` before, mindkeep is its rebrand at v0.2.0; the API is identical except for renames.

> Looking for deeper material? See [`INSTALL.md`](INSTALL.md) (4 install methods + air-gap), [`USAGE.md`](USAGE.md) (full CLI/API reference + 8 cookbook recipes), [`FAQ.md`](FAQ.md), [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md), and [`AUTOLOAD-SETUP.md`](AUTOLOAD-SETUP.md).

---

## What is mindkeep?

A **crash-safe, per-project long-term memory store for AI coding agents**. Think of it as a tiny SQLite database — one per project — that records:

- **Facts** — short statements you want the agent to recall (e.g. _"this repo uses pnpm, not npm"_).
- **ADRs** — architectural decisions with context (_"chose PostgreSQL over MySQL because…"_).
- **Preferences** — user-level tastes that follow you across projects.
- **Sessions** — optional rolling notes per chat session.

Properties:

- **Pure-Python wheel, zero runtime deps**, Python ≥ 3.9, MIT.
- **WAL-mode SQLite** + 30 s flush scheduler + `atexit`/`SIGTERM` hooks → crash-safe.
- **Per-project isolation** via `sha256(git-remote || abs-path)[:12]` → works in **any** directory, including empty new folders.
- **Secrets redactor** with 11 built-in patterns (PEM, JWT, GitHub PATs, AWS, OpenAI, Slack, …) scrubs sensitive strings before write.

---

## 1. Install

### Recommended: pipx (one isolated venv, on PATH)

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash

# Windows PowerShell
iwr -UseBasicParsing https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

Both installers:

1. Verify Python ≥ 3.9.
2. Bootstrap `pipx` if missing.
3. `pipx install mindkeep`.
4. Add `pipx`'s bin dir to `PATH` if needed.
5. Run `mindkeep doctor` at the end to verify.

### Manual

```bash
pipx install mindkeep      # preferred
# or:
pip install --user mindkeep
```

### Verify

```bash
mindkeep --version          # → 0.2.0 (or newer)
mindkeep doctor             # green ticks across the board
mindkeep where              # prints data_dir + project_id for the current cwd
```

If `mindkeep` is not found after install, your shell hasn't picked up the new `PATH`. Open a new terminal, or run `pipx ensurepath` and restart.

### Upgrade / uninstall

```bash
pipx upgrade mindkeep
mindkeep upgrade           # auto-detects pipx vs pip and reinstalls
pipx uninstall mindkeep
```

---

## 2. Use

### CLI quickstart

```bash
# remember a fact
mindkeep fact add "this repo deploys via 'make release', not CI"

# remember an architectural decision
mindkeep adr add "Chose Pydantic v2 over attrs for runtime validation"

# recall (autoload-style)
mindkeep show --kind facts
mindkeep show --kind adrs
mindkeep show --kind sessions --limit 3

# search
mindkeep show --kind facts --grep deploy

# JSON export / import (portable, diffable backups)
mindkeep export > backup.json
mindkeep import backup.json
```

> ⚠️ The subcommand is **flag-style**: `mindkeep show --kind facts`. `mindkeep show facts` will just print help.

### Where is the data?

```bash
mindkeep where
```

…prints the platform-specific data dir:

| OS | Default `data_dir` |
|---|---|
| Windows | `%APPDATA%\mindkeep\` |
| macOS | `~/Library/Application Support/mindkeep/` |
| Linux | `$XDG_DATA_HOME/mindkeep/` (fallback `~/.local/share/mindkeep/`) |

Override with `MINDKEEP_HOME=/some/path`.

### Python API

```python
from mindkeep import MemoryStore

store = MemoryStore()                              # auto-detects current project
store.add_fact("uses pnpm, not npm", tags=["build"])
store.add_adr("Chose PostgreSQL", context="MySQL didn't fit our JSONB needs")

for f in store.recall_facts(query="pnpm"):
    print(f.text)
```

The store is safe to use from multiple processes (WAL mode) and from short-lived agent runs (commits flush at 30 s and on exit).

### Per-project vs global

- **Per-project**: facts / ADRs / sessions, scoped by `project_id`. Each project gets its own SQLite file.
- **Global**: preferences live in a separate `preferences.db` and follow you across projects.

---

## 3. Wire it into your AI agent (autoload)

The whole point of mindkeep is that recall happens **without you having to remember**. See [`AUTOLOAD-SETUP.md`](AUTOLOAD-SETUP.md) for the full guide; the short version:

Append this block to whichever global agent-instructions file your tool reads (`~/.copilot/AGENTS.md`, `~/.claude/claude.md`, `~/.cursor/rules/global.mdc`, …):

```markdown
## 🧠 mindkeep autoload

At the start of every session, before responding:

1. Run `mindkeep show --kind facts` and `mindkeep show --kind adrs`.
2. If either returns rows, print: `🧠 mindkeep recall: N facts, M ADRs loaded`.
3. Treat the results as authoritative project context.
4. If `mindkeep` is not on PATH, silently skip.

Skip autoload only when cwd is `$HOME` / `%USERPROFILE%` or a temp dir.
Do **not** require `.git` or `pyproject.toml` — mindkeep works in any directory.

When the user asks to "remember" something, use:
- `mindkeep fact add "..."`
- `mindkeep adr add "..."`
```

After pasting, open a new agent session in any directory (even empty) — it should print the recall line on its first turn.

---

## 4. Common Q & A

### Install / setup

**Q. `mindkeep` not found after install.**
The `pipx` bin dir isn't on `PATH`. Run `pipx ensurepath` and restart your terminal. On Windows, sign out/in if PATH still doesn't refresh.

**Q. I have an old `agent-memory` install. How do I migrate?**
```bash
pipx uninstall agent-memory
pipx install mindkeep
# move existing data:
#   Linux:   mv "$XDG_DATA_HOME/agent-memory" "$XDG_DATA_HOME/mindkeep"
#   macOS:   mv "~/Library/Application Support/agent-memory" "~/Library/Application Support/mindkeep"
#   Windows: Move-Item "$env:APPDATA\agent-memory" "$env:APPDATA\mindkeep"
mindkeep doctor
```
Environment variables also rename: `AGENT_MEMORY_HOME` → `MINDKEEP_HOME`, etc.

**Q. Air-gapped / enterprise install.**
Download the wheel + `SHA256SUMS` from the GitHub Releases page, verify, then `pipx install ./mindkeep-0.2.0-py3-none-any.whl`. See `INSTALL.md` for the full enterprise flow.

### Usage

**Q. Why `mindkeep show --kind facts` and not `mindkeep show facts`?**
The CLI uses flag-style subcommands so the same `show` command can target different kinds without ambiguity. The positional form was never supported.

**Q. Does mindkeep work in an empty folder with no `.git`?**
Yes. The project ID is `sha256(git-remote || abs-path)[:12]`, so a path-only ID is computed for non-git directories. Autoload should **not** require a project marker.

**Q. Are facts shared between projects?**
No — facts/ADRs/sessions are isolated per project ID. Only `preferences` are global.

**Q. How do I wipe a project's memory?**
```bash
mindkeep clear --kind facts --confirm
mindkeep clear --kind adrs --confirm
# or nuke everything:
mindkeep clear --all --confirm
```

### Security

**Q. Will mindkeep store my API keys / passwords?**
The built-in `SecretsRedactor` scrubs 11 patterns before write — PEM private keys, JWTs, GitHub PATs (classic + fine-grained), AWS access & secret keys, Google API keys, Slack tokens, OpenAI keys, Azure storage keys, plus a generic `password|token|api_key=…` sweep. It's not a substitute for being careful, but it catches the common cases.

**Q. Is the SQLite file encrypted?**
No. It lives in your user-scoped data dir with normal filesystem permissions. If you need at-rest encryption, use full-disk encryption or put `MINDKEEP_HOME` on an encrypted volume.

**Q. Verifying release artifacts.**
Every release ships a `SHA256SUMS` file alongside the wheel, sdist, and install scripts. Run `sha256sum -c SHA256SUMS` (Linux/macOS) or the PowerShell equivalent.

### Troubleshooting

**Q. `mindkeep doctor` reports "WAL not supported".**
Your filesystem (some network mounts, some sandboxed paths) doesn't support WAL. Move `MINDKEEP_HOME` to a local disk.

**Q. `database is locked` errors.**
Almost always transient: another process is mid-write. mindkeep retries automatically. If persistent, check that no stale process is holding `*.db-wal`. As a last resort, `mindkeep doctor --repair`.

**Q. Autoload runs but never prints the recall line.**
Either no data is stored yet (`mindkeep show --kind facts` returns empty — that's correct, the agent should silently skip the line) or your agent instructions file isn't being loaded. Check the file path your tool actually reads, and confirm with `cat ~/.copilot/AGENTS.md | grep mindkeep`.

**Q. I'm seeing facts from a different project.**
The autoload skip-list isn't covering your shell's home dir. Add `$HOME` (or `%USERPROFILE%` on Windows) to the skip rules in your agent instructions.

### Internals

**Q. How is `project_id` computed?**
`sha256(canonical_origin)[:12]` where `canonical_origin` is the lowercased `git config remote.origin.url` if available, else the absolute path of the directory. Stable across renames of the working copy as long as the git remote stays the same.

**Q. What's the on-disk layout?**
```
$MINDKEEP_HOME/
├── projects/<project_id>/store.db        # WAL-mode SQLite, one per project
├── projects/<project_id>/.meta.json      # atomic-rename metadata
└── preferences.db                        # global preferences
```

**Q. Can I sync mindkeep between machines?**
Yes — point `MINDKEEP_HOME` at a synced directory (Dropbox, OneDrive, syncthing). Be aware: SQLite + cloud-sync can race; prefer `mindkeep export` / `import` for explicit transfers.

---

## Where to next

- Full CLI reference & 8 cookbook recipes → [`USAGE.md`](USAGE.md)
- All 4 install methods + air-gap → [`INSTALL.md`](INSTALL.md)
- 22-question deep FAQ → [`FAQ.md`](FAQ.md)
- 19-symptom troubleshooting → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- Agent autoload patterns → [`AUTOLOAD-SETUP.md`](AUTOLOAD-SETUP.md)
- Backup / removal / compliance exit → [`UNINSTALL.md`](UNINSTALL.md)

# Autoload Setup for AI Coding Agents

This guide shows how to configure your AI coding assistant (GitHub Copilot CLI, Claude Code, or any other agent that reads a global instructions file) to **automatically recall mindkeep memory** at the start of every session — no per-project setup, no forgotten flags.

The result: in **any** working directory (including brand-new empty folders), the agent runs `mindkeep show` on its first turn, prints a one-line summary of stored facts/ADRs, and treats them as authoritative project context.

---

## Why autoload?

Without autoload, you (or the agent) must remember to run `mindkeep show --kind facts` at the start of every session. New projects, new branches, and new chat windows all become opportunities to forget. Autoload makes recall a property of the agent runtime instead of a habit.

mindkeep's project ID is computed from `sha256(git-remote || abs-path)[:12]`, so it works for **any** directory — including folders without `.git`. There is no "is this a real project?" gate to fail.

---

## Setup

### 1. Verify mindkeep is on your PATH

```bash
mindkeep --version    # should print 0.2.0 or newer
```

If not, install it: see [INSTALL.md](INSTALL.md).

### 2. Add the autoload block to your agent instructions

Append the snippet below to whichever global instructions file your agent reads:

| Agent | File |
|---|---|
| GitHub Copilot CLI | `~/.copilot/AGENTS.md` |
| Claude Code | `~/.claude/claude.md` |
| Cursor (global) | `~/.cursor/rules/global.mdc` |
| Other | whatever global / system-prompt file the agent loads |

### Snippet

```markdown
## 🧠 mindkeep autoload

At the **start of every session**, before responding to the user:

1. Run: `mindkeep show --kind facts` and `mindkeep show --kind adrs`
2. If either returns rows, print one line: `🧠 mindkeep recall: N facts, M ADRs loaded`
3. Treat returned facts/ADRs as authoritative project context for the rest of the session.
4. If `mindkeep` is not on PATH, silently skip (do not error).

Skip autoload only when:
- cwd is the user's home directory (`$HOME` / `%USERPROFILE%`)
- cwd is a temp directory (`/tmp`, `%TEMP%`)

Do **not** require `.git`, `pyproject.toml`, or any other project marker — mindkeep works in any directory.

When the user asks you to "remember" something, use:
- `mindkeep fact add "..."` for project facts
- `mindkeep adr add "..."` for architectural decisions
```

### 3. Verify

Open a new agent session in any directory. On its first turn, it should run `mindkeep show --kind facts` and print the recall line if anything is stored.

---

## Common pitfalls

### ❌ Requiring a project marker

A previous version of this guide gated autoload on the existence of `.git`, `pyproject.toml`, or `package.json`. **Don't do this.** Many real workflows start with an empty folder (`mkdir new-thing && cd new-thing`), and the gate causes silent skips exactly when you most want recall.

### ❌ Wrong CLI syntax

mindkeep uses **flag-style** subcommands:

```bash
mindkeep show --kind facts        # ✅ correct
mindkeep show facts               # ❌ wrong (will print help)
```

### ❌ Forgetting to skip in `$HOME`

Without the `$HOME` skip, opening a shell in your home directory dumps every fact you've ever stored on its parent project. Keep the skip narrow (just `$HOME` and `/tmp`-like dirs).

---

## Multi-agent consistency

If you use both Copilot CLI and Claude Code, paste the same snippet into both files. The CLI behavior is identical because both invoke `mindkeep` as a subprocess.

For team use, commit a `docs/agent-prompt-snippet.md` to your repo and tell teammates to paste it into their global agent instructions once. mindkeep itself stays per-user (the SQLite DB lives under `%APPDATA%` / `~/Library/Application Support` / `$XDG_DATA_HOME`).

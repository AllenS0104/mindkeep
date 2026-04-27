# 🧠 mindkeep

[![Release](https://img.shields.io/github/v/release/AllenS0104/mindkeep?label=release&color=brightgreen)](https://github.com/AllenS0104/mindkeep/releases/latest) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE) [![Python](https://img.shields.io/badge/python-%E2%89%A53.9-blue.svg)](https://www.python.org/) [![Tests](https://img.shields.io/badge/tests-159%20passed-brightgreen.svg)](https://github.com/AllenS0104/mindkeep/actions) [![Platform](https://img.shields.io/badge/platform-win%20%7C%20mac%20%7C%20linux-lightgrey.svg)](./docs/INSTALL.md) [![Zero deps](https://img.shields.io/badge/runtime%20deps-0-success.svg)](./pyproject.toml)

> **Crash-safe, per-project long-term memory for AI coding agents.**
> Zero runtime dependencies · SQLite + WAL · Python ≥ 3.9 · MIT

Give your agents a real memory: facts, ADRs, preferences and session recaps
that survive across runs, machines and Ctrl-C. All stored locally in plain
SQLite files you can read, back up or `git diff`.

## ✨ Features

- 🔒 **Crash-safe** — WAL + 30s flush, survives SIGKILL
- 🗂 **Per-project isolation** — one SQLite file per repo hash
- 🌐 **Global preferences** — user tastes follow you across projects
- 🛡 **Secrets redactor** — 11 credential patterns scrubbed by default
- 🪶 **stdlib-only core** — no deps at runtime, `pip` stays quiet
- 🔌 **CLI + Python API** — `mindkeep show` or `from mindkeep import MemoryStore`
- 📦 **JSON export/import** — portable, inspectable, diffable

---

## 🔐 Verify before running

One-line `curl | bash` / `iwr | iex` is convenient but executes remote code blindly.
For anything production-sensitive, **download first, review, then run**:

**Windows (PowerShell):**
```powershell
iwr https://github.com/AllenS0104/mindkeep/releases/latest/download/install.ps1 -OutFile install.ps1
# open install.ps1 in your editor and review it, then:
.\install.ps1
```

**macOS / Linux:**
```bash
curl -fsSL https://github.com/AllenS0104/mindkeep/releases/latest/download/install.sh -o install.sh
# open install.sh in your editor and review it, then:
bash install.sh
```

Every [GitHub Release](https://github.com/AllenS0104/mindkeep/releases) ships a
`SHA256SUMS` file alongside the wheel / sdist / install scripts, so you can pin
to a tagged version and verify integrity:

```bash
curl -fsSL -o SHA256SUMS https://github.com/AllenS0104/mindkeep/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

```powershell
# PowerShell equivalent
Get-FileHash -Algorithm SHA256 .\install.ps1
# compare the output against the matching line in SHA256SUMS
```

---

## 🚀 Quickstart

### Option A · One-shot install (recommended)

**Windows (PowerShell):**
```powershell
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

**macOS / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash
```

The installer checks Python ≥ 3.9, installs `pipx` if missing, adds the
scripts dir to your `PATH`, and runs `mindkeep doctor` so you know it works.

### Option B · Manual via pipx

```bash
pipx install git+https://github.com/AllenS0104/mindkeep.git
```

### Option C · Offline wheel

Grab the `.whl` from the [Releases page](https://github.com/AllenS0104/mindkeep/releases):

```bash
pip install --user ./mindkeep-0.2.0-py3-none-any.whl
```

### Verify

```bash
mindkeep --version
mindkeep doctor
```

---

## 🎬 Real-world scenario

A day in the life of an AI coding agent that doesn't lose its mind on Ctrl-C.
Reads use the CLI; writes use the Python one-liner (the CLI is intentionally
read-only — agents call the API directly).

```bash
# === Session 1 — agent opens a checkout API repo ===
$ cd ~/code/checkout-api
$ mindkeep where
data_dir: ~/.local/share/mindkeep · project=7c4e1a9f0b3d

# agent thinking: "before I touch anything, what do I already know?"
$ mindkeep show --kind facts --limit 20
(no rows yet)

# agent thinking: "user just confirmed JWT RS256 over HS256. Persist it."
$ python -c "from mindkeep import MemoryStore; \
    s = MemoryStore.open(); \
    s.add_fact('auth.method: JWT RS256 — rotated quarterly via KMS', tags=['auth','security']); \
    s.add_adr('RS256 over HS256', \
              decision='Use asymmetric JWT signing (RSA-256).', \
              rationale='Downstream services verify without sharing the secret.'); \
    s.close()"

# === ~~~ ⚡ process killed (laptop sleep / SIGKILL / OOM) ~~~ ===

# === Session 2 — agent reboots, same repo ===
$ cd ~/code/checkout-api
$ mindkeep show --kind facts
content="auth.method: JWT RS256 — rotated quarterly via KMS"  tags=auth,security  2026-04-27T09:42:11Z
# agent thinking: "good, I remember. Continue where I left off."

# === Session 3 — agent switches to a different repo ===
$ cd ~/code/admin-portal
$ mindkeep where
data_dir: ~/.local/share/mindkeep · project=2e8b6d04ac17   ← different ID
$ mindkeep show --kind facts
(no rows yet)   ← admin-portal has its own memory, untainted by checkout-api
```

Each repo gets its own SQLite file. Crash, switch context, reboot — your agent's memory follows the project.

---

## 🤖 Plug into your AI agent

Wire `mindkeep` into whichever agent you use. Two recipes below; the pattern (recall on start, store on decision) is identical for any other tool.

### GitHub Copilot CLI

Drop this into `~/.copilot/copilot-instructions.md` (or your repo's `AGENTS.md`):

```markdown
## Project memory

At session start, load prior decisions for this project:
  mindkeep show --kind facts --limit 20
  mindkeep show --kind adrs  --limit 10

When you make architectural decisions or capture user preferences, persist
them via the Python API (the CLI is intentionally read-only):
  python -c "from mindkeep import MemoryStore; s=MemoryStore.open(); \
             s.add_fact('<short claim about the project>', tags=['<topic>']); s.close()"
  python -c "from mindkeep import MemoryStore; s=MemoryStore.open(); \
             s.add_adr('<short title>', decision='<the decision>', rationale='<why>'); s.close()"

Forget secrets — the redactor handles them, but don't rely on it.
```

### Claude Code

Add the same pattern to `~/.claude/CLAUDE.md` (or wrap it as a project skill):

```markdown
# Project memory

Before answering, check project memory:
  mindkeep show --kind facts --limit 10
  mindkeep show --kind adrs  --limit 10

After confirming a decision with the user, persist it via the Python API:
  python -c "from mindkeep import MemoryStore; s=MemoryStore.open(); \
             s.add_adr('<short title>', decision='<the decision>', rationale='<one-paragraph rationale>'); s.close()"
```

Full CLI surface and Python API are in [`docs/USAGE.md`](./docs/USAGE.md).

---

## 📖 Documentation

Full docs live in [`docs/`](./docs/README.md) — start at the portal for role- and task-based navigation.

| Guide | One-liner |
|---|---|
| [INSTALL](./docs/INSTALL.md) | 4 ways to install, enterprise & air-gap, `doctor` diagnostics |
| [USAGE](./docs/USAGE.md) | CLI reference + Python API + 8 cookbook recipes |
| [UNINSTALL](./docs/UNINSTALL.md) | Backup, removal, PATH restore, compliance exit |
| [FAQ](./docs/FAQ.md) | 22 questions across 5 categories |
| [TROUBLESHOOTING](./docs/TROUBLESHOOTING.md) | 19 symptoms in symptom / cause / diagnose / fix form |

See also: [ARCHITECTURE.md](./ARCHITECTURE.md) · [CHANGELOG.md](./CHANGELOG.md) · [CONTRIBUTING.md](./CONTRIBUTING.md)

---

## 📖 Usage / 使用方式

### CLI — 命令行

Eight subcommands, all `--help`-friendly:

```text
list      list all known projects
show      show rows for a project  (--kind facts|adrs|preferences|sessions|all)
clear     delete rows from a project
export    dump a project to JSON
import    load a JSON dump into a project
where     print data_dir and current project id
doctor    run environment health checks
upgrade   pull the latest release (pip/pipx auto-detected)
```

One full example — inspect the current repo's memory:

```bash
cd ~/code/my-app
mindkeep where              # → C:\Users\you\AppData\Roaming\mindkeep · project=a1b2c3d4e5f6
mindkeep show --kind facts --limit 5
mindkeep export ./my-app-memory.json
```

### Python API — 程序化调用

```python
from mindkeep import MemoryStore

with MemoryStore.open() as store:          # resolves project from cwd
    store.add_fact("stack: Postgres 15 + FastAPI", tags=["stack"])
    store.add_adr("use RSA-256 for JWT",
                  decision="Sign JWTs with RS256.",
                  rationale="Asymmetric keys let downstream services verify without sharing the secret.")
    store.set_preference("style.quote", "single", scope="user")

    for row in store.list_facts(tag="stack"):
        print(row["value"])
```

The store auto-flushes every 30 seconds, on `close()`, and on `atexit` /
`SIGTERM`. Secrets are redacted before they hit disk.

---

## 🏗 How it works

```
┌─────────────────────────────────────────────────────────────┐
│  Your agent process                                         │
│                                                             │
│   MemoryStore ─► SecretsRedactor ─► SQLite (WAL mode)       │
│        │                                  │                 │
│        └── flush scheduler (30s) ─────────┘                 │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
        <data_dir>/
          ├── projects/
          │     ├── a1b2c3d4e5f6.db      ← repo A (hashed path)
          │     ├── a1b2c3d4e5f6.meta.json
          │     └── 9f8e7d6c5b4a.db      ← repo B
          └── preferences.db             ← cross-project user tastes
```

- **Project ID** = first 12 hex chars of `sha256(git-remote || abs-path)`
- **`data_dir`** = `%APPDATA%\mindkeep\` (Windows), `~/Library/Application Support/mindkeep/` (macOS), or `$XDG_DATA_HOME/mindkeep/` (Linux). Override with `$MINDKEEP_HOME`.
- **Crash safety** = WAL + `synchronous=NORMAL` + atomic rename for `.meta.json` + `atexit`/`SIGTERM` flush hooks

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full contract and ADRs.

---

## ❓ FAQ

> The 4 most-asked questions are inline below. For the full 22-question FAQ (getting started, usage, security, internals, troubleshooting) see **[docs/FAQ.md](./docs/FAQ.md)**.

**Q: If I Ctrl-C the CLI, do I lose data?**
A: No. WAL mode flushes on every commit; the 30-second scheduler plus
`atexit` / `SIGTERM` hooks guarantee durability. SIGKILL is safe too —
SQLite replays the WAL on next open.

**Q: Is it safe to store prompts and errors in here?**
A: Yes. `SecretsRedactor` is on by default and scrubs 11 classes of
credentials before write: PEM private keys, JWTs, GitHub PATs (classic +
fine-grained), AWS access & secret keys, Google API keys, Slack tokens,
OpenAI keys, Azure storage keys, plus a generic `password|token|api_key=…`
sweep. Still — don't store secrets on purpose.

**Q: I installed it but `mindkeep` isn't found.**
A: Run `python -m mindkeep doctor` — it prints the exact PATH it
expects. Usually it's fixed by opening a new terminal (installers edit
`PATH` for *future* shells). See [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md) for more.

**Q: How do I upgrade / uninstall?**
A: `mindkeep upgrade` (auto-detects pipx vs pip). To remove: `pipx uninstall mindkeep`.
Your data in `data_dir` is *not* deleted — see [docs/UNINSTALL.md](./docs/UNINSTALL.md) for a clean wipe.

---

## 🛠 Development

```bash
git clone https://github.com/AllenS0104/mindkeep.git
cd mindkeep
pip install -e ".[dev]"
pytest
```

PRs welcome — please run `pytest` and keep the stdlib-only rule for
`src/mindkeep/` (dev-deps are fine).

---

## 📜 License

MIT — see [LICENSE](./LICENSE).

## 👤 Author

Built by **Allen Song** ([@AllenS0104](https://github.com/AllenS0104)) with help
from a fleet of AI sub-agents (architect, coder, ops, PMO, UX inspector — see
`.github/agents/`). The stdlib-only constraint is intentional: a memory layer
shouldn't depend on a memory of dependencies.

Issues, ideas, PRs welcome at
[github.com/AllenS0104/mindkeep/issues](https://github.com/AllenS0104/mindkeep/issues).

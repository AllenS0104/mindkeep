# 📋 Share card — paste into IM

---

🧠 **mindkeep** — crash-safe, per-project long-term memory for AI coding agents.
Zero runtime deps, pure SQLite + WAL, Python 3.9+.
Give your agents a real memory across sessions: facts, ADRs, preferences, session recaps.
Secrets auto-redacted (11 credential patterns), data lives in plain `.db` files you own.
Repo → https://github.com/AllenS0104/mindkeep

**Install (pick one):**

```powershell
# Windows
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash
```

```bash
# Any OS, manual
pipx install git+https://github.com/AllenS0104/mindkeep.git
```

Then: `mindkeep doctor && mindkeep where`

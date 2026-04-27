# 📖 mindkeep Documentation

> **Crash-safe, per-project long-term memory for AI coding agents.** — Pick the doc that matches your role or task; everything is local, stdlib-only, and diffable.

## 🗺️ Documentation map

```
mindkeep/
├── README.md ................ project landing page (features, quickstart, inline FAQ)
├── ARCHITECTURE.md .......... deep design + ADR-0001 … ADR-0005
├── CONTRIBUTING.md .......... PR, test, stdlib-only rules
├── CHANGELOG.md ............. versioned change history (Keep a Changelog)
├── RELEASE-NOTES.md ......... per-release highlights
└── docs/
    ├── README.md ............ 👈 you are here — portal & nav
    ├── INSTALL.md ........... 4 ways to install, enterprise & air-gap, doctor
    ├── USAGE.md ............. CLI reference + Python API + 8 cookbook recipes
    ├── UNINSTALL.md ......... backup, remove, PATH cleanup, compliance exit
    ├── FAQ.md ............... 22 questions across 5 categories
    └── TROUBLESHOOTING.md ... 19 symptoms × (symptom / cause / diagnose / fix)
```

---

## 👥 Navigate by role

### 👤 New user — *“I just want it working in the next 5 minutes.”*

1. Start on the project [README](../README.md#-quickstart) for the one-liner installer.
2. Verify install: [INSTALL §Verify](./INSTALL.md#6-verify-your-install) → `mindkeep doctor`.
3. First recall/remember: [USAGE §CLI Quickstart](./USAGE.md#cli-quickstart).
4. If something looks odd: [FAQ §Getting started](./FAQ.md#1-getting-started).

### 🛠️ Daily user — *“I’m coding against the CLI / Python API.”*

1. CLI reference & flags: [USAGE §CLI reference](./USAGE.md#cli-reference).
2. Python API patterns: [USAGE §Python API](./USAGE.md#python-api).
3. Cookbook recipes (export/import, tags, sessions): [USAGE §Cookbook](./USAGE.md#cookbook).
4. Common error → fix: [TROUBLESHOOTING](./TROUBLESHOOTING.md).

### 🏢 Enterprise / Ops — *“I need air-gap, audit, and a clean exit.”*

1. Offline wheel / corporate proxy: [INSTALL §Enterprise & air-gap](./INSTALL.md#5-enterprise--air-gapped-environments).
2. Integrity verification (SHA256SUMS): [INSTALL §Verify checksums](./INSTALL.md#7-verify-checksums).
3. Secrets-at-rest posture: [FAQ §Security & privacy](./FAQ.md#3-security--privacy).
4. Backup → uninstall → PATH restore: [UNINSTALL.md](./UNINSTALL.md).
5. Compliance exit checklist: [UNINSTALL §Compliance exit](./UNINSTALL.md#compliance-exit-checklist).

### 🏗️ Contributor / Architect — *“Show me the internals.”*

1. Design contract + ADR-0001…0005: [ARCHITECTURE.md](../ARCHITECTURE.md).
2. How to contribute, run tests: [CONTRIBUTING.md](../CONTRIBUTING.md).
3. Change history: [CHANGELOG.md](../CHANGELOG.md).
4. Crash-safety deep dive: [FAQ §How it works](./FAQ.md#4-how-it-works) + [TROUBLESHOOTING §Crash & recovery](./TROUBLESHOOTING.md).

---

## 🎯 Navigate by task

| I want to… | Start here |
|---|---|
| Install on Windows / macOS / Linux | [INSTALL §Install methods](./INSTALL.md#2-install-methods) |
| Install behind a corporate proxy / air-gap | [INSTALL §Enterprise & air-gap](./INSTALL.md#5-enterprise--air-gapped-environments) |
| Find where my memory lives on disk | [USAGE §`where`](./USAGE.md#where) · [FAQ Q5](./FAQ.md#q5-where-is-my-data-stored) |
| Export one project to JSON | [USAGE §Cookbook — export/import](./USAGE.md#cookbook) |
| Sync memory between two machines | [FAQ Q-sync](./FAQ.md#2-usage--workflow) · [USAGE §Cookbook](./USAGE.md#cookbook) |
| Understand the redactor rules (11 patterns) | [FAQ §Security & privacy](./FAQ.md#3-security--privacy) · project [README](../README.md#-faq) |
| Upgrade to the latest release | [INSTALL §Upgrade](./INSTALL.md#8-upgrade) · [USAGE §`upgrade`](./USAGE.md#upgrade) |
| Diagnose `command not found` | [TROUBLESHOOTING §PATH / not found](./TROUBLESHOOTING.md) · [FAQ Q-path](./FAQ.md#1-getting-started) |
| Recover after a crash / SIGKILL | [TROUBLESHOOTING §Crash & recovery](./TROUBLESHOOTING.md) · [FAQ §How it works](./FAQ.md#4-how-it-works) |
| Back up my data before wiping | [UNINSTALL §Backup first](./UNINSTALL.md#1-backup-first) |
| Fully remove the tool and all data | [UNINSTALL §Full removal](./UNINSTALL.md#3-full-removal) |
| Embed `MemoryStore` in my own Python app | [USAGE §Python API](./USAGE.md#python-api) |
| Read the architecture & ADRs | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| Report a bug or request a feature | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| See what changed between versions | [CHANGELOG.md](../CHANGELOG.md) |

---

## 📦 Version matrix — v0.2.0

| Document | Size | Purpose |
|---|---:|---|
| [INSTALL.md](./INSTALL.md) | ~19 KB | 4 install methods · enterprise · air-gap · doctor |
| [USAGE.md](./USAGE.md) | ~37 KB | CLI (9 cmds) · Python API · 8 cookbook recipes |
| [UNINSTALL.md](./UNINSTALL.md) | ~15 KB | Backup · remove · PATH restore · compliance |
| [FAQ.md](./FAQ.md) | ~21 KB | 22 Qs in 5 categories |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | ~20 KB | 19 symptoms, symptom/cause/diagnose/fix |

Root-level companions:

| Document | Purpose |
|---|---|
| [../README.md](../README.md) | Landing page, quickstart, inline FAQ |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Deep design + ADR-0001 … ADR-0005 |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | PR / testing / stdlib-only rule |
| [../CHANGELOG.md](../CHANGELOG.md) | Keep-a-Changelog history |
| [../RELEASE-NOTES.md](../RELEASE-NOTES.md) | v0.2.0 highlights |

---

## 💬 Feedback & contribution

- 🐛 Found a bug or a confusing doc? Open an issue — see [CONTRIBUTING.md](../CONTRIBUTING.md).
- 🛠 Want to send a patch? PRs welcome; please keep `src/mindkeep/` stdlib-only and run `pytest` (154 tests) before opening.
- 📝 Doc typos / clarifications are always an easy first PR.

> **Stuck?** Try this order: [TROUBLESHOOTING](./TROUBLESHOOTING.md) → [FAQ](./FAQ.md) → `mindkeep doctor` → issue.

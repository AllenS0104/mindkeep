# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- _nothing yet_

---

## [0.2.0] - 2026-04-27

The Big Rename. The project's working title was `agent-memory`; its final identity is **`mindkeep`**. This release renames the PyPI package, the CLI command, the importable Python module, and the GitHub repository. There are no functional changes to the storage layer, CLI surface, or on-disk record format — only identifiers.

### Changed

- **BREAKING — Project renamed `agent-memory` → `mindkeep`.** The PyPI package (`mindkeep`), the CLI command (`mindkeep`), the importable Python module (`mindkeep`), and the GitHub repository (now [`AllenS0104/mindkeep`](https://github.com/AllenS0104/mindkeep)) have all been renamed in lockstep. The legacy `agent-memory` distribution is no longer published.
- **BREAKING — Environment variables renamed:**
  - `AGENT_MEMORY_HOME` → `MINDKEEP_HOME`
  - `AGENT_MEMORY_UPGRADE_SOURCE` → `MINDKEEP_UPGRADE_SOURCE`
  - `AGENT_MEMORY_NO_CONFIRM` → `MINDKEEP_NO_CONFIRM`
- **BREAKING — Default data directory renamed `agent-memory/` → `mindkeep/`** under the platform-specific application-data root (`%APPDATA%`, `~/Library/Application Support`, `$XDG_DATA_HOME`). Existing v0.1.x users will see an empty store on first launch unless they manually copy or move the contents of the old `agent-memory/` directory into the new `mindkeep/` directory.

### Migration

```bash
pipx uninstall agent-memory
pipx install mindkeep
mindkeep doctor
```

If you have existing v0.1.x data, copy it across before first use, e.g. on Linux:

```bash
mv "$XDG_DATA_HOME/agent-memory" "$XDG_DATA_HOME/mindkeep"
```

(or the equivalent path on macOS / Windows). If you scripted the legacy environment variables in shells, CI, or agent runners, rename them to the `MINDKEEP_*` forms above.

---

## [0.1.2] - 2026-04-24

### Fixed

- **Repository URL identity** — corrected all references from the legacy `v-songjun/mindkeep` path to the canonical `AllenS0104/mindkeep` across install scripts, README, CLI output, release notes, and test fixtures. Previous versions pointed installers at a non-existent repository.

### Changed

- **README version alignment** — bumped install/raw URLs and pinned-version examples to `v0.1.2`; restated the v0.1.1 LF-normalization fix for install scripts.

---

## [0.1.1] - 2026-04-24

### Fixed

- **Install scripts line endings** — normalized `install.ps1` and `install.sh` to LF via `.gitattributes` (`text eol=lf`). On Windows clones with `core.autocrlf=true`, v0.1.0's `install.ps1` was checked out as CRLF, but `raw.githubusercontent.com` serves the stored LF bytes. This caused `SHA256SUMS` (computed on the CRLF working copy) to fail verification against the raw URL. v0.1.1 guarantees a single canonical byte stream across local clone, raw URL, and Release asset.

### Changed

- `SHA256SUMS` is now computed on LF-normalized bytes and is itself stored with LF line endings.

---

## [0.1.0] - 2026-04-24

First public release — a crash-safe, per-project long-term memory store for AI coding agents.

### Added

- **Core `MemoryStore`** with WAL-mode SQLite backend, 30 s flush scheduler, `atexit` / `SIGTERM` hooks, and atomic `.meta.json` rename for crash safety.
- **Per-project isolation** — one SQLite DB per repo, keyed by `sha256(git-remote || abs-path)[:12]`.
- **Global preferences store** (`preferences.db`) for user-level tastes that follow you across projects.
- **Four record types**: facts, ADRs, preferences, sessions — each with tags and timestamps.
- **CLI with 9 subcommands**: `list`, `show`, `clear`, `export`, `import`, `where`, `doctor`, `upgrade`, plus `--version`.
  - `doctor` — environment health check (Python version, PATH, `data_dir`, WAL support, redactor, project detection).
  - `upgrade` — auto-detects `pipx` vs `pip` and reinstalls from the configured source; supports `--dry-run`.
- **JSON export / import** for portable, diffable backups.
- **Install scripts** — `install.sh` (macOS/Linux) and `install.ps1` (Windows) with Python ≥ 3.9 check, auto-`pipx` bootstrap, `PATH` update, and post-install `doctor` invocation.
- **pipx-first distribution** — published as a standard wheel + sdist; `pipx install` is the recommended path.
- **Cross-platform `data_dir` resolution** — `%APPDATA%` (Windows), `~/Library/Application Support` (macOS), `$XDG_DATA_HOME` (Linux), overridable via `$MINDKEEP_HOME`.
- **Python ≥ 3.9 support** with zero runtime dependencies (stdlib only).

### Security

- **SQL column-name whitelist (P0)** — all dynamic ORDER BY / column references are validated against a fixed allowlist to eliminate the injection surface.
- **`SecretsRedactor` with 11 built-in patterns** — scrubs PEM private keys, JWTs, GitHub PATs (classic + fine-grained), AWS access & secret keys, Google API keys, Slack tokens, OpenAI keys, Azure storage keys, plus a generic `password|token|api_key=…` sweep before write.
- **`SHA256SUMS` published with every release** — wheel, sdist and install scripts are hash-verifiable.
- **`install.sh` `main()` wrapper** — the curl | bash pipe only executes after the full script is received, preventing truncated-download code execution.
- **CI hardening** — explicit least-privilege `permissions:` blocks on all GitHub Actions workflows.

### Documentation

- **`docs/INSTALL.md`** — 4 install methods (one-liner, pipx, pip, offline wheel) + enterprise / air-gapped flows + `doctor` diagnostics.
- **`docs/USAGE.md`** — full CLI reference, Python API guide, and 8 cookbook recipes.
- **`docs/UNINSTALL.md`** — backup, removal, PATH restoration and enterprise compliance exit checklist.
- **`docs/FAQ.md`** — 22 questions across 5 categories (getting started, usage, security, internals, troubleshooting).
- **`docs/TROUBLESHOOTING.md`** — 19 symptoms in a 4-section format (symptom / cause / diagnose / fix).
- **`docs/README.md`** — documentation portal with role-based and task-based navigation.
- **`CHANGELOG.md`** — this file (Keep a Changelog format).

---

[Unreleased]: https://github.com/AllenS0104/mindkeep/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/AllenS0104/mindkeep/releases/tag/v0.2.0
[0.1.2]: https://github.com/AllenS0104/mindkeep/releases/tag/v0.1.2
[0.1.1]: https://github.com/AllenS0104/mindkeep/releases/tag/v0.1.1
[0.1.0]: https://github.com/AllenS0104/mindkeep/releases/tag/v0.1.0

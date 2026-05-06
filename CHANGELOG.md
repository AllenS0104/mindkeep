# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- _nothing yet_

---

## [0.3.0] - 2026-05-06

Schema v3 + retrieval, capture, and observability layer.

### Added

- **Schema v3 + FTS5** (#6): `last_accessed_at`, `access_count`, `pin`, `archived_at`, `token_estimate` columns on facts/adrs; `facts_fts` and `adrs_fts` virtual tables with `unicode61 remove_diacritics 1` tokenizer (CJK-friendly).
- **Session token budget** (#7): `MINDKEEP_SESSION_BUDGET=N` env var caps cumulative tokens emitted per parent process; `mindkeep session status|reset` CLI; per-pid state file under `$XDG_RUNTIME_DIR` / `%LOCALAPPDATA%`.
- **`show --top K` / `show --budget N`** (#8): cap rendered rows by count or by cumulative token estimate; pinned rows always come first.
- **`mindkeep recall <query>`** (#9): FTS5 + bm25 ranked search across facts and adrs. Flags: `--top K`, `--kind facts|adrs|all`, `--json`. Lower bm25 = better; uses `snippet()` for highlighting.
- **`mindkeep stats [--json]`** (#11): per-store token totals, fact/adr counts, pin counts, oldest/newest entries.
- **Dual-threshold write guard** (#12): caps facts at 100 tokens and adrs at 1500 tokens (env-overridable). Rejects oversize content unless `force=True`. Detects pre-redaction over-cap (2× heuristic) and warns when redaction shrinks input >50%.
- **`mindkeep pin` / `mindkeep unpin`** (#13): mark facts/adrs as pinned so they survive token budgets and rank first in `show`. New API: `pin_fact`, `unpin_fact`, `pin_adr`, `unpin_adr`, plus `pinned_only=` filter on list APIs.
- **`mindkeep doctor` enhancements** (#14): 8 health checks — schema-version up-to-date, WAL active, FTS5 integrity, store stats, token-cap pressure, stale entries, DB size + VACUUM hint, pin sanity. New `--json` mode.
- **`mindkeep integrate <claude|copilot|cursor|generic>`** (#10): emits ready-to-paste integration snippets for popular AI coding agents. Flags: `--out PATH`, `--force`, `--list`.
- **Eval harness** (#15): `python -m mindkeep.evals` runs 8 reproducible scenarios (recall@5, top-1 ordering, CJK recall, budget compliance, top compliance, pin priority, write-guard reject, doctor green) and emits a JSON report. Lives in `src/mindkeep/evals/`.

### Fixed

- **CI flakiness on first-open contention** (#18): `PRAGMA journal_mode=WAL` does not invoke busy_handler, causing immediate `SQLITE_BUSY` when multiple processes opened the DB concurrently. `Storage` now sets `busy_timeout=5000` before any contended pragma, skips the WAL switch when already on WAL, and wraps schema init / `migrate_to_v3` in `BEGIN IMMEDIATE` retries. CI: 6/6 green for the first time since v0.2.0.

### Notes

- 277-test suite covering all v0.3.0 features plus a regression baseline.
- v0.3.0 is fully backwards-compatible with v0.2.0 stores; first open auto-migrates schema_version 2 → 3 in a single transaction.
- Known follow-up tracked in #28: eval corpus is currently trivially separable; queries and thresholds will be tightened in v0.3.1.

---

## [0.2.0] - 2026-04-27

Initial public release of **mindkeep**.

### Added

- Crash-safe per-project long-term memory store (`MemoryStore`) backed by SQLite + WAL.
- Public API: `add_fact` / `remember_fact` / `list_facts` / `recall_facts`, `add_adr` / `remember_adr` / `list_adrs` / `recall_adrs`, plus retention scheduler.
- CLI: `mindkeep` (init, fact, adr, list, gc, doctor, version).
- Installers: `install.ps1` (Windows) and `install.sh` (macOS / Linux), pure-Python wheel, sdist.
- Pure-Python wheel, **zero runtime dependencies**, Python ≥ 3.9, MIT licensed.
- 159-test suite covering CLI, storage, scheduler, security, integration, and crash-recovery scenarios.

### Notes

mindkeep v0.2.0 is a rebrand of the legacy `agent-memory` v0.1.5 codebase. Public API surface is identical except for renames documented in `RELEASE-NOTES.md`. Legacy source, tags, and release artifacts (v0.1.0 – v0.1.5) have been archived privately at `AllenS0104/mindkeep-archive`.

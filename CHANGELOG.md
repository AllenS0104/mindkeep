# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- _nothing yet_

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

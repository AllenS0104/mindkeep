# Contributing to mindkeep

Thanks for your interest! This project is stdlib-only and aims to stay small.

## Quick start

1. **Fork** this repo and clone your fork.
2. **Create a virtualenv** and install in editable mode with dev extras:
   ```bash
   python -m venv .venv
   # Linux/macOS:
   source .venv/bin/activate
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1

   pip install -e ".[dev]"
   ```
3. **Run tests** before committing:
   ```bash
   pytest tests/ -v
   ```
4. **Open a PR** against `main`. CI must be green (Linux + Windows, Python 3.11/3.12/3.13).

## Guidelines

- Keep runtime deps at **zero** (stdlib only). Dev-only deps go under `[project.optional-dependencies]`.
- Add tests for every bug fix and feature.
- Keep commits focused; use descriptive messages.
- Crash-safety matters — prefer WAL, fsync, and atomic rename patterns.

## Reporting issues

Open a GitHub issue with:
- OS + Python version
- Minimal reproduction steps
- Expected vs. actual behavior

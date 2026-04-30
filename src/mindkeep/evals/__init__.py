"""Mindkeep eval suite — reproducible scenarios for v0.3.0 features.

Run via ``python -m mindkeep.evals``.

Exposes:
* :func:`load_corpus` — read the bundled fixture JSON.
* :class:`EvalResult` — one scenario's metric / threshold / pass-fail.
* :func:`run_all` — execute every scenario against an isolated tmp DB.

This is an internal evaluation harness, not a public benchmark. The
fixture corpus is intentionally small (KB-scale) so the wheel stays
light. See P2-10 / issue #15.
"""
from __future__ import annotations

from .scenarios import EvalResult, load_corpus, run_all

__all__ = ["EvalResult", "load_corpus", "run_all"]

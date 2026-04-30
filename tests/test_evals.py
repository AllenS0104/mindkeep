"""Tests for the mindkeep eval harness (P2-10, #15)."""
from __future__ import annotations

import io
import json
import os
import contextlib
from pathlib import Path

import pytest

from mindkeep.evals import EvalResult, load_corpus, run_all
from mindkeep.evals.runner import build_report, main as runner_main
from mindkeep.storage import fts5_available


pytestmark = pytest.mark.skipif(
    not fts5_available(),
    reason="SQLite build lacks FTS5; eval suite requires recall",
)


def test_corpus_loads_with_expected_counts() -> None:
    corpus = load_corpus()
    assert isinstance(corpus, dict)
    assert "facts" in corpus and "adrs" in corpus and "queries" in corpus
    assert len(corpus["facts"]) == 30
    assert len(corpus["adrs"]) == 10
    assert len(corpus["queries"]["recall_at_5"]) == 10
    assert len(corpus["queries"]["recall_top1"]) == 5
    assert len(corpus["queries"]["recall_cjk"]) == 3
    # All facts have unique refs
    refs = [f["ref"] for f in corpus["facts"]]
    assert len(refs) == len(set(refs))


def test_run_all_scenarios_pass() -> None:
    results = run_all()
    assert len(results) == 8
    for r in results:
        assert isinstance(r, EvalResult)
        assert r.passed, f"scenario {r.name} failed: metric={r.metric} threshold={r.threshold} details={r.details}"


def test_report_schema_and_summary() -> None:
    results = run_all()
    report = build_report(results)
    # Top-level keys
    assert set(report.keys()) == {"version", "scenarios", "summary"}
    assert report["version"] == 1
    # Summary
    summary = report["summary"]
    assert set(summary.keys()) == {"total", "passed", "failed"}
    assert summary["total"] == len(results)
    assert summary["passed"] + summary["failed"] == summary["total"]
    assert summary["failed"] == 0
    # Per-scenario shape
    expected_keys = {"name", "metric", "threshold", "passed", "details"}
    for sc in report["scenarios"]:
        assert expected_keys.issubset(sc.keys())
    # JSON-serialisable round-trip
    text = json.dumps(report, ensure_ascii=False)
    assert json.loads(text) == report


def test_runner_main_writes_report_and_exits_zero(tmp_path: Path) -> None:
    report_path = tmp_path / "out" / "report.json"
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = runner_main(["--report", str(report_path), "--quiet"])
    assert rc == 0, f"runner stderr: {err.getvalue()}"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["total"] == 8


def test_python_dash_m_entry_point(tmp_path: Path) -> None:
    """``python -m mindkeep.evals`` exits 0 when all scenarios pass."""
    import subprocess
    import sys

    report_path = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "mindkeep.evals", "--report", str(report_path), "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0, (
        f"python -m mindkeep.evals failed (rc={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["failed"] == 0


def test_markdown_summary_contains_all_scenarios() -> None:
    from mindkeep.evals.runner import render_markdown

    results = run_all()
    report = build_report(results)
    md = render_markdown(report)
    assert "mindkeep eval report" in md
    for sc in report["scenarios"]:
        assert sc["name"] in md

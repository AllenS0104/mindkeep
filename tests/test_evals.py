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
    # Use lower-bound invariants rather than exact counts so adding fixture
    # data does not require updating this test in lockstep. The eval runner
    # itself enforces actual semantic thresholds on each scenario.
    assert len(corpus["facts"]) >= 20, "corpus should have a meaningful fact set"
    assert len(corpus["adrs"]) >= 5, "corpus should have a meaningful adr set"
    assert len(corpus["queries"]["recall_at_5"]) >= 5
    assert len(corpus["queries"]["recall_top1"]) >= 3
    assert len(corpus["queries"]["recall_cjk"]) >= 1
    # All facts have unique refs
    fact_refs = [f["ref"] for f in corpus["facts"]]
    assert len(fact_refs) == len(set(fact_refs)), "fact refs must be unique"
    # All adr refs are unique
    adr_refs = [a["ref"] for a in corpus["adrs"]]
    assert len(adr_refs) == len(set(adr_refs)), "adr refs must be unique"
    # Every recall_at_5 expected ref must exist in the fact or adr corpus
    all_refs = set(fact_refs) | set(adr_refs)
    for q in corpus["queries"]["recall_at_5"]:
        for ref in q.get("expected", []):
            assert ref in all_refs, f"recall_at_5 query references unknown ref {ref!r}"


def test_run_all_scenarios_pass() -> None:
    results = run_all()
    assert len(results) == 9
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
    assert payload["summary"]["total"] == 9


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


def test_runner_main_returns_one_when_any_scenario_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner must exit non-zero when at least one scenario fails.

    We monkeypatch the runner's view of ``run_all`` to return a synthetic
    fail+pass result set, so the exit-code branch is actually exercised
    (the corpus is too easy for natural failures).
    """
    from mindkeep.evals import runner as runner_mod

    fake_results = [
        EvalResult(
            name="E_fake_pass", metric=1.0, threshold=0.5, passed=True, details={}
        ),
        EvalResult(
            name="E_fake_fail", metric=0.0, threshold=0.7, passed=False,
            details={"reason": "synthetic failure for exit-code coverage"},
        ),
    ]
    monkeypatch.setattr(runner_mod, "run_all", lambda: fake_results)

    report_path = tmp_path / "report.json"
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = runner_mod.main(["--report", str(report_path), "--quiet"])
    assert rc == 1, f"expected exit 1 on failure, got {rc}"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["failed"] == 1
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["total"] == 2


def test_markdown_summary_contains_all_scenarios() -> None:
    from mindkeep.evals.runner import render_markdown

    results = run_all()
    report = build_report(results)
    md = render_markdown(report)
    assert "mindkeep eval report" in md
    for sc in report["scenarios"]:
        assert sc["name"] in md


def test_e9_can_fail_when_term_dense_fact_absent(tmp_path: Path) -> None:
    """Guard test: E9 must actually fail if the term-dense fact is removed.

    The whole point of E9 is to catch a bm25 regression. If we strip the
    fact that *should* win on term frequency, the scenario should not be
    able to lie about success — it must report ``passed=False``.
    """
    from mindkeep.evals.scenarios import scenario_e9_bm25_term_density
    from mindkeep.memory_api import MemoryStore

    # Pre-seed the e9 paths with ONLY the sparse fact, so when the
    # scenario inserts its own dense+sparse pair, the dense fact still
    # wins and the scenario passes — that's the healthy case. Then we
    # need to also assert the *unhealthy* case: monkeypatch the
    # scenario's tmp paths to a store where dense was never inserted.
    cwd = tmp_path / "e9_proj"
    data_dir = tmp_path / "e9_data"
    cwd.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    rare_term = "snorklewhomp"
    sparse = (
        "General indexing notes: this row mentions "
        f"{rare_term} once but is mostly about unrelated metadata, "
        "schemas, migrations, and varied filler text used as a length "
        "control so bm25 length-normalisation has something to compare."
    )
    decoy_dense_other_term = (
        "alpha alpha alpha alpha alpha tokens that do not match the rare term."
    )

    store = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    try:
        sparse_id = store.add_fact(sparse, tags=["sparse"])
        store.add_fact(decoy_dense_other_term, tags=["decoy"])
        store.commit()
        # Sanity: top-1 for the rare term is the sparse fact (only match).
        hits = store.recall(rare_term, top=5)
        assert hits, "sparse fact must match the rare term"
        assert hits[0].id == sparse_id
    finally:
        store.close()

    # Now simulate "dense fact missing" by running the scenario against a
    # tmp_root where we have already created e9_proj/e9_data with ONLY
    # the sparse fact pre-loaded — but the scenario re-opens the store
    # and *adds its own* dense+sparse pair, so this path actually still
    # passes. To get a true negative we instead patch ``MemoryStore.add_fact``
    # so that the dense insert is silently dropped.
    from mindkeep.evals import scenarios as scen_mod

    real_open = scen_mod.MemoryStore.open

    class _DropDenseStore:
        def __init__(self, inner):
            self._inner = inner

        def add_fact(self, content, tags=None, pin=False, force=False):
            # Drop facts whose content starts with the rare term (the dense one).
            if isinstance(content, str) and content.startswith(rare_term + " indexes"):
                return -1
            return self._inner.add_fact(content, tags=tags or [], pin=pin, force=force)

        def commit(self):
            return self._inner.commit()

        def recall(self, *a, **kw):
            return self._inner.recall(*a, **kw)

        def close(self):
            return self._inner.close()

    def _patched_open(*a, **kw):
        return _DropDenseStore(real_open(*a, **kw))

    scen_mod.MemoryStore.open = staticmethod(_patched_open)  # type: ignore[attr-defined]
    try:
        result = scenario_e9_bm25_term_density(tmp_path / "e9_negative")
    finally:
        scen_mod.MemoryStore.open = real_open  # type: ignore[attr-defined]

    assert result.passed is False, (
        "E9 must fail when the term-dense fact is missing; otherwise the "
        "scenario cannot catch a real bm25 regression. "
        f"got metric={result.metric} details={result.details}"
    )

"""Eval scenarios for mindkeep v0.3.0 (P2-10, issue #15).

Each scenario is a small function returning :class:`EvalResult`. Scenarios
share an isolated tmp data dir + project cwd built by :func:`run_all`. They
are deterministic given the bundled fixture corpus.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .. import _tokens
from ..memory_api import MemoryStore
from ..storage import WriteGuardError

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_CORPUS_PATH = _FIXTURES_DIR / "corpus.json"


# ─────────────────────────── data types ───────────────────────────


@dataclass
class EvalResult:
    """Result of a single eval scenario."""

    name: str
    metric: float
    threshold: float
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────── corpus loader ───────────────────────────


def load_corpus(path: Path | None = None) -> dict[str, Any]:
    """Load the bundled fixture corpus (or *path* if provided)."""
    p = Path(path) if path is not None else _CORPUS_PATH
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if "facts" not in data or "adrs" not in data or "queries" not in data:
        raise ValueError(f"corpus missing required keys: {sorted(data)}")
    return data


# ─────────────────────────── helpers ───────────────────────────


def _seed_main_corpus(store: MemoryStore, corpus: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Insert the corpus into *store* and return ref -> rowid maps.

    Returns ``{"facts": {ref: rowid}, "adrs": {ref: rowid}}``.
    """
    fact_map: dict[str, int] = {}
    adr_map: dict[str, int] = {}
    for f in corpus["facts"]:
        rid = store.add_fact(f["content"], tags=list(f.get("tags") or []), pin=bool(f.get("pin")))
        fact_map[f["ref"]] = rid
    for a in corpus["adrs"]:
        rid = store.add_adr(
            title=a["title"],
            decision=a["decision"],
            rationale=a["rationale"],
            tags=list(a.get("tags") or []),
            pin=bool(a.get("pin")),
        )
        adr_map[a["ref"]] = rid
    store.commit()
    return {"facts": fact_map, "adrs": adr_map}


def _hit_ref(hit: Any, fact_map: dict[str, int], adr_map: dict[str, int]) -> str | None:
    """Map a RecallHit back to its corpus ``ref`` id."""
    inv_facts = {v: k for k, v in fact_map.items()}
    inv_adrs = {v: k for k, v in adr_map.items()}
    if hit.kind == "fact":
        return inv_facts.get(hit.id)
    if hit.kind == "adr":
        return inv_adrs.get(hit.id)
    return None


def _run_cli(data_dir: Path, cwd: Path, *args: str) -> tuple[int, str, str]:
    """Invoke the mindkeep CLI in-process with ``data_dir`` patched in."""
    from .. import cli as cli_mod

    real_default = cli_mod.default_data_dir
    cli_mod.default_data_dir = lambda: data_dir  # type: ignore[assignment]
    saved_cwd = os.getcwd()
    os.chdir(cwd)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli_mod.main(list(args))
            except SystemExit as exc:
                code = int(exc.code or 0)
    finally:
        cli_mod.default_data_dir = real_default  # type: ignore[assignment]
        os.chdir(saved_cwd)
    return code, out.getvalue(), err.getvalue()


def _data_rows(out: str, kind: str) -> list[str]:
    """Return data row lines under ``== {kind} ==`` (excluding header/sep)."""
    lines = out.splitlines()
    try:
        start = lines.index(f"== {kind} ==")
    except ValueError:
        return []
    if start + 1 < len(lines) and lines[start + 1] == "(no rows)":
        return []
    body = lines[start + 3:]
    rows: list[str] = []
    for line in body:
        if line == "" or line.startswith("== "):
            break
        rows.append(line)
    return rows


def _sum_tokens(lines: Iterable[str]) -> int:
    return sum(_tokens.estimate(line) for line in lines)


# ─────────────────────────── scenarios ───────────────────────────


def scenario_e1_recall_at_5(
    store: MemoryStore, corpus: dict[str, Any], maps: dict[str, dict[str, int]]
) -> EvalResult:
    """E1 — recall@5 precision: mean(intersection / |expected|) >= 0.85.

    Threshold tightened in #28 (was 0.7). 0.85 catches a roughly 1-in-5
    mis-ranking across the synonym-heavy corpus while still leaving room
    for a single hard query to slip without flaking the suite.
    """
    queries = corpus["queries"]["recall_at_5"]
    fact_map = maps["facts"]
    adr_map = maps["adrs"]
    per_query: list[dict[str, Any]] = []
    scores: list[float] = []
    for q in queries:
        hits = store.recall(q["q"], top=5)
        got_refs = {_hit_ref(h, fact_map, adr_map) for h in hits}
        got_refs.discard(None)
        expected = set(q["expected"])
        inter = expected & got_refs
        score = len(inter) / max(1, len(expected))
        scores.append(score)
        per_query.append({
            "query": q["q"],
            "expected": sorted(expected),
            "got": sorted(r for r in got_refs if r is not None),
            "score": score,
        })
    metric = sum(scores) / len(scores) if scores else 0.0
    threshold = 0.85
    return EvalResult(
        name="E1_recall_at_5_precision",
        metric=round(metric, 4),
        threshold=threshold,
        passed=metric >= threshold,
        details={"queries": per_query, "n": len(queries)},
    )


def scenario_e2_recall_ordering(
    store: MemoryStore, corpus: dict[str, Any], maps: dict[str, dict[str, int]]
) -> EvalResult:
    """E2 — top-1 of best-answer queries hits expected at >= 85%.

    Threshold tightened in #28 (was 0.8). With 5 best-answer queries,
    0.85 means at most 0 misses are tolerated when ``len % 0.85`` rounds
    down — i.e. all five must hit, but a single near-miss in a future
    larger set would not flake.
    """
    queries = corpus["queries"]["recall_top1"]
    fact_map = maps["facts"]
    adr_map = maps["adrs"]
    per_query: list[dict[str, Any]] = []
    correct = 0
    for q in queries:
        hits = store.recall(q["q"], top=5)
        top_ref = _hit_ref(hits[0], fact_map, adr_map) if hits else None
        ok = top_ref == q["best"]
        if ok:
            correct += 1
        per_query.append({"query": q["q"], "best": q["best"], "top1": top_ref, "ok": ok})
    metric = correct / len(queries) if queries else 0.0
    threshold = 0.85
    return EvalResult(
        name="E2_recall_ordering_top1",
        metric=round(metric, 4),
        threshold=threshold,
        passed=metric >= threshold,
        details={"queries": per_query, "n": len(queries), "correct": correct},
    )


def scenario_e3_cjk_recall(
    store: MemoryStore, corpus: dict[str, Any], maps: dict[str, dict[str, int]]
) -> EvalResult:
    """E3 — each CJK query returns >= 1 expected hit in top-3."""
    queries = corpus["queries"]["recall_cjk"]
    fact_map = maps["facts"]
    adr_map = maps["adrs"]
    per_query: list[dict[str, Any]] = []
    hits_count = 0
    for q in queries:
        hits = store.recall(q["q"], top=3)
        got_refs = {_hit_ref(h, fact_map, adr_map) for h in hits}
        got_refs.discard(None)
        expected = set(q["expected"])
        ok = bool(expected & got_refs)
        if ok:
            hits_count += 1
        per_query.append({
            "query": q["q"], "expected": sorted(expected),
            "got": sorted(r for r in got_refs if r is not None), "ok": ok,
        })
    metric = hits_count / len(queries) if queries else 0.0
    threshold = 1.0
    return EvalResult(
        name="E3_cjk_recall",
        metric=round(metric, 4),
        threshold=threshold,
        passed=metric >= threshold,
        details={"queries": per_query, "n": len(queries)},
    )


def scenario_e4_budget_compliance(data_dir: Path, cwd: Path) -> EvalResult:
    """E4 — show --budget N: rendered data-row tokens <= N for each N."""
    budgets = [50, 200, 1000]
    per_budget: list[dict[str, Any]] = []
    all_ok = True
    for n in budgets:
        rc, out, _err = _run_cli(data_dir, cwd, "show", "--budget", str(n))
        kinds_tokens: dict[str, int] = {}
        total = 0
        for kind in ("facts", "adrs", "preferences", "sessions"):
            rows = _data_rows(out, kind)
            t = _sum_tokens(rows)
            kinds_tokens[kind] = t
            total += t
        ok = rc == 0 and total <= n
        per_budget.append({"budget": n, "total_tokens": total, "by_kind": kinds_tokens, "ok": ok})
        if not ok:
            all_ok = False
    metric = sum(1 for b in per_budget if b["ok"]) / len(per_budget)
    threshold = 1.0
    return EvalResult(
        name="E4_budget_compliance",
        metric=round(metric, 4),
        threshold=threshold,
        passed=all_ok,
        details={"budgets": per_budget},
    )


def scenario_e5_top_compliance(data_dir: Path, cwd: Path) -> EvalResult:
    """E5 — show --top K: rendered row count per kind <= K."""
    tops = [1, 3, 10]
    per_top: list[dict[str, Any]] = []
    all_ok = True
    for k in tops:
        rc, out, _err = _run_cli(data_dir, cwd, "show", "--top", str(k))
        counts: dict[str, int] = {}
        kind_ok = True
        for kind in ("facts", "adrs", "preferences", "sessions"):
            rows = _data_rows(out, kind)
            counts[kind] = len(rows)
            if len(rows) > k:
                kind_ok = False
        ok = rc == 0 and kind_ok
        per_top.append({"top": k, "by_kind": counts, "ok": ok})
        if not ok:
            all_ok = False
    metric = sum(1 for t in per_top if t["ok"]) / len(per_top)
    return EvalResult(
        name="E5_top_compliance",
        metric=round(metric, 4),
        threshold=1.0,
        passed=all_ok,
        details={"tops": per_top},
    )


def scenario_e6_pin_priority(tmp_root: Path) -> EvalResult:
    """E6 — pinned facts must come first; show --kind facts --top N returns only pinned."""
    cwd = tmp_root / "e6_proj"
    data_dir = tmp_root / "e6_data"
    cwd.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    PINNED_COUNT = 3
    UNPINNED_COUNT = 5

    store = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    try:
        for i in range(UNPINNED_COUNT):
            store.add_fact(f"unpinned-fact-{i:02d}-payload")
        pinned_ids: list[int] = []
        for i in range(PINNED_COUNT):
            pinned_ids.append(store.add_fact(f"PINNED-fact-{i:02d}-payload", pin=True))
        store.commit()
    finally:
        store.close()

    rc, out, _err = _run_cli(
        data_dir, cwd, "show", "--kind", "facts", "--top", str(PINNED_COUNT)
    )
    rows = _data_rows(out, "facts")
    # Each row begins with "id | pin | key | value ..." — pin column is "*" when pinned.
    # We assert all returned rows have a "*" in the pin column.
    pinned_marks = 0
    parsed_rows: list[list[str]] = []
    for line in rows:
        cells = [c.strip() for c in line.split("|")]
        parsed_rows.append(cells)
        if len(cells) >= 2 and cells[1] == "*":
            pinned_marks += 1
    ok = rc == 0 and len(rows) == PINNED_COUNT and pinned_marks == PINNED_COUNT
    return EvalResult(
        name="E6_pin_priority",
        metric=float(pinned_marks),
        threshold=float(PINNED_COUNT),
        passed=ok,
        details={
            "rendered_rows": rows,
            "pinned_marks": pinned_marks,
            "row_count": len(rows),
            "expected_pinned": PINNED_COUNT,
            "rc": rc,
        },
    )


def scenario_e7_write_guard_reject(tmp_root: Path) -> EvalResult:
    """E7 — write-guard rejects oversized facts/ADRs; force=True succeeds."""
    cwd = tmp_root / "e7_proj"
    data_dir = tmp_root / "e7_data"
    cwd.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    checks: dict[str, bool] = {}
    store = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    try:
        # facts cap default = 100 tokens. _chars(200) -> 200 tokens > 100.
        oversized_fact = "x" * (200 * 4 + 1)
        try:
            store.add_fact(oversized_fact)
            checks["fact_reject"] = False
        except WriteGuardError:
            checks["fact_reject"] = True

        try:
            rid = store.add_fact(oversized_fact, force=True)
            checks["fact_force"] = isinstance(rid, int) and rid > 0
        except Exception:
            checks["fact_force"] = False

        # ADRs cap default = 1500 tokens. Combined title+decision+rationale -> ~1800.
        big = "y" * (600 * 4 + 1)  # ~600 tokens each
        try:
            store.add_adr(title=big, decision=big, rationale=big)
            checks["adr_reject"] = False
        except WriteGuardError:
            checks["adr_reject"] = True

        try:
            rid2 = store.add_adr(title=big, decision=big, rationale=big, force=True)
            checks["adr_force"] = isinstance(rid2, int) and rid2 > 0
        except Exception:
            checks["adr_force"] = False
    finally:
        store.close()

    passed = all(checks.values())
    metric = sum(1 for v in checks.values() if v) / len(checks)
    return EvalResult(
        name="E7_write_guard_reject",
        metric=round(metric, 4),
        threshold=1.0,
        passed=passed,
        details={"checks": checks},
    )


def scenario_e8_doctor_green(data_dir: Path, cwd: Path) -> EvalResult:
    """E8 — doctor --json against populated fixture DB has summary.fail == 0."""
    rc, out, _err = _run_cli(data_dir, cwd, "doctor", "--json")
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = {}
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    fail = int(summary.get("fail", 1)) if summary else 1
    ok = (rc == 0) and (fail == 0)
    return EvalResult(
        name="E8_doctor_green",
        metric=float(fail),
        threshold=0.0,
        passed=ok,
        details={"rc": rc, "summary": summary, "ok_count": int(summary.get("ok", 0))},
    )


def scenario_e9_bm25_term_density(tmp_root: Path) -> EvalResult:
    """E9 — bm25 term-frequency sanity: a term-dense fact must outrank a
    semantically-related but term-sparse fact for that term.

    Added in #28 as a guard against a regression where ``recall_facts``
    drops the ``ORDER BY bm25(...)`` clause (or otherwise stops weighting
    term frequency). Uses an isolated tmp store so it does not depend on
    fixture corpus content.
    """
    cwd = tmp_root / "e9_proj"
    data_dir = tmp_root / "e9_data"
    cwd.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Use a deliberately rare token so no fixture noise can leak in.
    rare_term = "snorklewhomp"
    dense = (
        f"{rare_term} indexes {rare_term} tables for {rare_term} search "
        f"and {rare_term} ranking heuristics."
    )
    sparse = (
        "General indexing notes: this row mentions "
        f"{rare_term} once but is mostly about unrelated metadata, "
        "schemas, migrations, and varied filler text used as a length "
        "control so bm25 length-normalisation has something to compare."
    )

    store = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    try:
        # Insert sparse FIRST so its rowid is lower than the dense one.
        # That way a regression that drops bm25 ordering and falls back
        # to insertion order / id tiebreak still lets sparse "win",
        # surfacing the regression. The healthy bm25 path overrides id
        # order and ranks dense first on term frequency.
        sparse_id = store.add_fact(sparse, tags=["sparse"])
        dense_id = store.add_fact(dense, tags=["dense"])
        store.commit()
        hits = store.recall(rare_term, top=5)
    finally:
        store.close()

    top_id = hits[0].id if hits else None
    ok = top_id == dense_id
    return EvalResult(
        name="E9_bm25_term_density",
        metric=1.0 if ok else 0.0,
        threshold=1.0,
        passed=ok,
        details={
            "rare_term": rare_term,
            "dense_id": dense_id,
            "sparse_id": sparse_id,
            "top_id": top_id,
            "hit_count": len(hits),
            "ranking": [
                {"id": h.id, "score": h.score, "value": h.value[:80]} for h in hits
            ],
        },
    )


# ─────────────────────────── orchestration ───────────────────────────


def run_all(corpus_path: Path | None = None) -> list[EvalResult]:
    """Run every scenario against an isolated tmp DB; return ordered results."""
    corpus = load_corpus(corpus_path)

    with tempfile.TemporaryDirectory(prefix="mindkeep-evals-") as tmp:
        tmp_root = Path(tmp)
        main_data = tmp_root / "main_data"
        main_cwd = tmp_root / "main_proj"
        main_data.mkdir(parents=True, exist_ok=True)
        main_cwd.mkdir(parents=True, exist_ok=True)

        store = MemoryStore.open(cwd=main_cwd, data_dir=main_data)
        try:
            maps = _seed_main_corpus(store, corpus)
            results: list[EvalResult] = [
                scenario_e1_recall_at_5(store, corpus, maps),
                scenario_e2_recall_ordering(store, corpus, maps),
                scenario_e3_cjk_recall(store, corpus, maps),
            ]
        finally:
            store.close()

        # CLI-driven scenarios run after the store is closed so SQLite
        # doesn't see two simultaneous writers on the same DB.
        results.append(scenario_e4_budget_compliance(main_data, main_cwd))
        results.append(scenario_e5_top_compliance(main_data, main_cwd))
        results.append(scenario_e6_pin_priority(tmp_root))
        results.append(scenario_e7_write_guard_reject(tmp_root))
        results.append(scenario_e8_doctor_green(main_data, main_cwd))
        results.append(scenario_e9_bm25_term_density(tmp_root))

    return results


__all__ = [
    "EvalResult",
    "load_corpus",
    "run_all",
    "scenario_e1_recall_at_5",
    "scenario_e2_recall_ordering",
    "scenario_e3_cjk_recall",
    "scenario_e4_budget_compliance",
    "scenario_e5_top_compliance",
    "scenario_e6_pin_priority",
    "scenario_e7_write_guard_reject",
    "scenario_e8_doctor_green",
    "scenario_e9_bm25_term_density",
]

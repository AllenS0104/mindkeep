"""Tests for the v0.3.0 P0-4 ``mindkeep recall`` command and API (#9).

Covers both the in-process API (``MemoryStore.recall``) and the CLI
subcommand. Designed to skip cleanly on SQLite builds without FTS5 — the
P0-4 design treats recall as best-effort on environments lacking FTS5.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindkeep import cli
from mindkeep.memory_api import MemoryStore, RecallHit, _prepare_fts_query
from mindkeep.storage import fts5_available


pytestmark = pytest.mark.skipif(
    not fts5_available(),
    reason="SQLite build lacks FTS5; recall is unavailable",
)


# ───────────────────────── fixtures ─────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture
def populated_store(data_dir: Path):
    """Open a MemoryStore prefilled with 5 facts + 3 ADRs covering the
    English/CJK terms used by the assertions below."""
    store = MemoryStore.open(data_dir=data_dir)
    # Facts — distinguishable terms.
    store.add_fact("alpha bravo charlie", tags=["greek", "letters"])
    store.add_fact("alpha shows up only in this fact", tags=["unique"])
    store.add_fact("delta echo foxtrot", tags=["alpha"])  # tag-only "alpha"
    store.add_fact("中文 内容 测试 一", tags=["cjk"])
    store.add_fact("plain english fact about pnpm install", tags=["build"])
    # ADRs — separate term universes from the facts so kind filters bite.
    store.add_adr(
        title="Adopt zigzag protocol",
        decision="we will use zigzag for transport",
        rationale="evaluated quokka and yodel; zigzag wins on latency",
        tags=["transport"],
    )
    store.add_adr(
        title="Yodel for telemetry",
        decision="standardise on yodel for metrics",
        rationale="alpha is rejected as an alternative",
        tags=["telemetry"],
    )
    store.add_adr(
        title="日志格式 决议",
        decision="使用 JSON 行格式",
        rationale="便于 grep 与机器解析",
        tags=["logging"],
    )
    yield store
    store.close()


def _run(argv, capsys):
    rc = cli.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ───────────────────────── _prepare_fts_query ─────────────────────────


def test_prepare_wraps_plain_text():
    assert _prepare_fts_query("alpha bravo") == '"alpha bravo"'


def test_prepare_wraps_punctuation_so_fts_does_not_choke():
    # bare hyphens / dots / colons are FTS5-significant — must be quoted.
    assert _prepare_fts_query("foo-bar.baz") == '"foo-bar.baz"'


def test_prepare_passes_through_explicit_operator():
    assert _prepare_fts_query("alpha OR bravo") == "alpha OR bravo"


def test_prepare_passes_through_explicit_phrase():
    assert _prepare_fts_query('"alpha bravo"') == '"alpha bravo"'


def test_prepare_passes_through_prefix_wildcard():
    assert _prepare_fts_query("alph*") == "alph*"


def test_prepare_passes_through_when_quote_present():
    # A bare `"` is itself an FTS5 syntax token (phrase delimiter), so
    # any input containing one is trusted and forwarded unchanged.
    assert _prepare_fts_query('He said "hi"') == 'He said "hi"'


# ───────────────────────── MemoryStore.recall ─────────────────────────


def test_recall_finds_matching_fact(populated_store):
    hits = populated_store.recall("alpha")
    assert hits, "expected at least one hit for 'alpha'"
    assert all(isinstance(h, RecallHit) for h in hits)
    # The two facts containing the literal word "alpha" must be present.
    fact_values = {h.value for h in hits if h.kind == "fact"}
    assert any("alpha bravo charlie" in v for v in fact_values)
    assert any("alpha shows up only in this fact" in v for v in fact_values)


def test_recall_cjk_query(populated_store):
    hits = populated_store.recall("中文")
    assert hits, "expected at least one hit for the CJK query"
    assert any("中文" in h.value or "中文" in h.snippet for h in hits)


def test_recall_no_matches_returns_empty(populated_store):
    assert populated_store.recall("nonexistent_zzzzzz_token") == []


def test_recall_empty_query_returns_empty(populated_store):
    assert populated_store.recall("") == []
    assert populated_store.recall("   ") == []


def test_recall_kind_facts_excludes_adrs(populated_store):
    hits = populated_store.recall("alpha", kind="facts")
    assert hits
    assert all(h.kind == "fact" for h in hits)


def test_recall_kind_adrs_excludes_facts(populated_store):
    # "yodel" appears only in ADRs.
    hits = populated_store.recall("yodel", kind="adrs")
    assert hits
    assert all(h.kind == "adr" for h in hits)


def test_recall_kind_all_includes_both(populated_store):
    # "alpha" appears in facts AND in one ADR's rationale.
    hits = populated_store.recall("alpha", kind="all", top=20)
    kinds = {h.kind for h in hits}
    assert kinds == {"fact", "adr"}


def test_recall_top_one(populated_store):
    hits = populated_store.recall("alpha", top=1)
    assert len(hits) == 1


def test_recall_results_sorted_by_score_ascending(populated_store):
    hits = populated_store.recall("alpha", top=20)
    assert hits == sorted(hits, key=lambda h: (h.score, h.kind, h.id))


def test_recall_unknown_kind_raises(populated_store):
    with pytest.raises(ValueError):
        populated_store.recall("alpha", kind="bogus")


def test_recall_snippet_highlights_match(populated_store):
    hits = populated_store.recall("alpha", kind="facts", top=5)
    # FTS5 snippet() wraps matches with the `[ ]` markers we requested.
    assert any("[" in h.snippet and "]" in h.snippet for h in hits)


def test_recall_ranking_documented(populated_store):
    """Document the actual bm25 ordering with default column weights.

    bm25 rewards short documents matching rare terms. The tag-only fact
    ``"delta echo foxtrot"`` (tags=``alpha``) is a shorter "document"
    overall than ``"alpha bravo charlie"`` (tags=``greek,letters``) once
    you sum value+tags lengths, so it can legitimately rank first. We
    don't assert a specific ordering — only that all three "alpha"-
    bearing facts appear, are deterministically sorted, and that scores
    are non-decreasing (lower bm25 = better, ascending).
    """
    hits = populated_store.recall("alpha", kind="facts", top=10)
    values = [h.value for h in hits]
    assert any("alpha bravo" in v for v in values)
    assert any(v.startswith("alpha shows up") for v in values)
    assert any(v.startswith("delta") for v in values)  # tag-only match
    scores = [h.score for h in hits]
    assert scores == sorted(scores)


# ───────────────────────── CLI surface ─────────────────────────


def _seed(data_dir: Path) -> None:
    """Same fixture content as ``populated_store``, but as a freshly-closed
    store so the CLI subcommand can re-open it."""
    store = MemoryStore.open(data_dir=data_dir)
    store.add_fact("alpha bravo charlie", tags=["greek"])
    store.add_fact("alpha shows up only in this fact")
    store.add_fact("delta echo foxtrot", tags=["alpha"])
    store.add_fact("中文 内容 测试 一", tags=["cjk"])
    store.add_fact("pnpm install fact", tags=["build"])
    store.add_adr(
        title="Adopt zigzag protocol",
        decision="we will use zigzag",
        rationale="evaluated alternatives",
    )
    store.add_adr(
        title="Yodel for telemetry",
        decision="standardise on yodel",
        rationale="alpha is rejected as an alternative",
    )
    store.close()


def test_cli_recall_human_format(data_dir, capsys):
    _seed(data_dir)
    rc, out, err = _run(["recall", "alpha"], capsys)
    assert rc == 0, err
    assert "recall:" in out
    assert "lower bm25 = better match" in out
    assert "kind" in out and "score" in out


def test_cli_recall_no_matches(data_dir, capsys):
    _seed(data_dir)
    rc, out, _ = _run(["recall", "nonexistent_xyz_token"], capsys)
    assert rc == 0
    assert "No results." in out


def test_cli_recall_no_matches_json(data_dir, capsys):
    _seed(data_dir)
    rc, out, _ = _run(["recall", "nonexistent_xyz_token", "--json"], capsys)
    assert rc == 0
    assert json.loads(out) == []


def test_cli_recall_empty_query_exits_2(data_dir, capsys):
    _seed(data_dir)
    rc, _, err = _run(["recall", "   "], capsys)
    assert rc == 2
    assert "non-empty" in err


def test_cli_recall_json_shape(data_dir, capsys):
    _seed(data_dir)
    rc, out, err = _run(["recall", "alpha", "--json"], capsys)
    assert rc == 0, err
    data = json.loads(out)
    assert isinstance(data, list)
    assert data, "expected at least one hit"
    required = {"kind", "id", "score", "snippet", "tags", "value"}
    for entry in data:
        assert required.issubset(entry.keys())
        assert entry["kind"] in {"fact", "adr"}
        assert isinstance(entry["id"], int)
        assert isinstance(entry["score"], (int, float))
        assert isinstance(entry["tags"], list)


def test_cli_recall_kind_facts(data_dir, capsys):
    _seed(data_dir)
    rc, out, _ = _run(["recall", "alpha", "--kind", "facts", "--json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data and all(d["kind"] == "fact" for d in data)


def test_cli_recall_kind_adrs(data_dir, capsys):
    _seed(data_dir)
    rc, out, _ = _run(["recall", "yodel", "--kind", "adrs", "--json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data and all(d["kind"] == "adr" for d in data)


def test_cli_recall_top_limits(data_dir, capsys):
    _seed(data_dir)
    rc, out, _ = _run(["recall", "alpha", "--top", "1", "--json"], capsys)
    assert rc == 0
    assert len(json.loads(out)) == 1


def test_cli_recall_cjk(data_dir, capsys):
    _seed(data_dir)
    rc, out, _ = _run(["recall", "中文", "--json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data
    assert any("中文" in d["value"] or "中文" in d["snippet"] for d in data)

"""Tests for ``mindkeep stats`` (P1-6, issue #11)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from mindkeep import cli
from mindkeep.memory_api import MemoryStore
from mindkeep.storage import SCHEMA_VERSION, Storage, _estimate_tokens


# ───────────────────────── fixtures ─────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    rc = cli.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ───────────────────────── token estimator ─────────────────────────


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_ascii_quartered():
    assert _estimate_tokens("abcdefghijkl") == 3  # 12 // 4


def test_estimate_tokens_cjk_one_each():
    # When sibling `_tokens.estimate` (P0-2) is available it returns chars/2
    # for CJK (per design doc §9). Without it, the local fallback returns
    # 1 token per CJK char. Both implementations produce a deterministic,
    # non-zero count for pure CJK input.
    assert _estimate_tokens("中文测试") in (2, 4)


# ───────────────────────── Storage.stats() ─────────────────────────


def test_stats_empty_store(data_dir: Path):
    s = Storage("0123456789ab", data_dir=data_dir)
    try:
        d = s.stats()
    finally:
        s.close()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["facts"] == {"total": 0, "pinned": 0, "archived": 0}
    assert d["adrs"] == {"total": 0, "pinned": 0, "archived": 0}
    assert d["sessions"] == {"total": 0}
    assert d["top_tags"] == []
    assert d["tokens_estimated_total"] == 0
    assert d["oldest_fact_at"] is None
    assert d["newest_fact_at"] is None
    assert d["db_size_bytes"] > 0


def test_stats_counts_with_pin_and_archive(data_dir: Path, tmp_path: Path):
    sub = tmp_path / "proj1"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        for i in range(5):
            store.add_fact(f"fact-{i}", tags=["auth" if i < 3 else "ops", "core"])
        for i in range(2):
            store.add_adr(f"ADR {i}", "do X", "because Y", tags=["auth"])
        ph = store.project_id.id
    finally:
        store.close()

    s = Storage(ph, data_dir=data_dir)
    try:
        s._conn.execute("UPDATE facts SET pin = 1 WHERE id IN (1, 2)")
        s._conn.execute(
            "UPDATE facts SET archived_at = '2024-01-01T00:00:00Z' WHERE id = 3"
        )
        s._conn.commit()
        d = s.stats()
    finally:
        s.close()

    assert d["facts"]["total"] == 5
    assert d["facts"]["pinned"] == 2
    assert d["facts"]["archived"] == 1
    assert d["adrs"]["total"] == 2
    tags = d["top_tags"]
    assert tags[0] == {"tag": "auth", "count": 5}  # 3 facts + 2 adrs
    assert {t["tag"] for t in tags} >= {"auth", "core", "ops"}
    assert d["tokens_estimated_total"] > 0
    assert d["oldest_fact_at"] is not None
    assert d["newest_fact_at"] is not None
    assert d["oldest_fact_at"] <= d["newest_fact_at"]


# ───────────────────────── CLI dispatch ─────────────────────────


def _seed(data_dir: Path, tmp_path: Path) -> str:
    sub = tmp_path / "cli_proj"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact("hello world", tags=["x", "y"])
        store.add_fact("another", tags=["x"])
        store.add_adr("ADR 1", "decide", "rationale", tags=["y"])
        store.set_preference("editor", "vim")
        ph = store.project_id.id
    finally:
        store.close()
    return ph


def test_stats_human_format(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    ph = _seed(data_dir, tmp_path)
    monkeypatch.chdir(tmp_path / "cli_proj")
    rc, out, err = _run(["stats"], capsys)
    assert rc == 0, err
    assert "mindkeep store" in out
    assert f"project={ph}" in out
    assert "Schema version:" in out
    assert "Facts:" in out
    assert "ADRs:" in out
    assert "Preferences:" in out
    assert "Top tags:" in out
    assert "DB file size:" in out
    assert "Session budget:" in out
    assert "not active" in out


def test_stats_json_schema(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    ph = _seed(data_dir, tmp_path)
    monkeypatch.chdir(tmp_path / "cli_proj")
    rc, out, err = _run(["stats", "--json"], capsys)
    assert rc == 0, err
    payload = json.loads(out)
    expected = {
        "schema_version", "project_id", "data_dir", "db_size_bytes",
        "facts", "adrs", "preferences", "sessions", "top_tags",
        "tokens_estimated_total", "oldest_fact_at", "newest_fact_at",
        "session_budget",
    }
    assert expected.issubset(payload.keys())
    assert payload["project_id"] == ph
    assert payload["facts"]["total"] == 2
    assert payload["adrs"]["total"] == 1
    assert payload["preferences"]["total"] == 1
    assert payload["db_size_bytes"] > 0
    assert payload["session_budget"] is None
    counts = [t["count"] for t in payload["top_tags"]]
    assert counts == sorted(counts, reverse=True)


def test_stats_top_tags_ranking(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    sub = tmp_path / "rank_proj"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact("a", tags=["alpha", "beta"])
        store.add_fact("b", tags=["alpha", "beta"])
        store.add_fact("c", tags=["alpha"])
        store.add_fact("d", tags=["gamma"])
    finally:
        store.close()
    monkeypatch.chdir(sub)
    rc, out, _ = _run(["stats", "--json"], capsys)
    assert rc == 0
    tags = json.loads(out)["top_tags"]
    assert [t["tag"] for t in tags] == ["alpha", "beta", "gamma"]
    assert [t["count"] for t in tags] == [3, 2, 1]


def test_stats_session_budget_mocked(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _seed(data_dir, tmp_path)
    monkeypatch.chdir(tmp_path / "cli_proj")

    fake = types.ModuleType("mindkeep._session")
    fake.current_state = lambda: {  # type: ignore[attr-defined]
        "active": True, "budget": 2000, "spent": 450, "calls": 3,
    }
    monkeypatch.setitem(sys.modules, "mindkeep._session", fake)

    rc, out, _ = _run(["stats", "--json"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["session_budget"] == {
        "active": True, "budget": 2000, "spent": 450, "calls": 3,
    }


def test_stats_oldest_before_newest(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    sub = tmp_path / "ts_proj"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact("first")
        ph = store.project_id.id
    finally:
        store.close()
    s = Storage(ph, data_dir=data_dir)
    try:
        s._conn.execute(
            "UPDATE facts SET created_at = '2020-01-01T00:00:00Z' WHERE id = 1"
        )
        s._conn.commit()
    finally:
        s.close()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact("second")
    finally:
        store.close()

    monkeypatch.chdir(sub)
    rc, out, _ = _run(["stats", "--json"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["oldest_fact_at"] == "2020-01-01T00:00:00Z"
    assert payload["newest_fact_at"] > payload["oldest_fact_at"]

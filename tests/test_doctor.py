"""Tests for the enhanced ``mindkeep doctor`` (P1-9, issue #14)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindkeep import cli
from mindkeep.memory_api import MemoryStore
from mindkeep.storage import SCHEMA_VERSION, Storage


# ───────────────────────── fixtures ─────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def _seed(data_dir: Path, cwd: Path, *, facts: int = 0, adrs: int = 0) -> str:
    cwd.mkdir(exist_ok=True)
    store = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    try:
        for i in range(facts):
            store.add_fact(f"fact-{i}", tags=["t"])
        for i in range(adrs):
            store.add_adr(f"ADR {i}", "do X", "because Y")
        return store.project_id.id
    finally:
        store.close()


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    rc = cli.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ───────────────────────── tests ─────────────────────────


def test_doctor_missing_db_warns_exit_zero(data_dir: Path, capsys):
    rc, out, _ = _run(["doctor"], capsys)
    assert rc == 0
    assert "no database initialised" in out


def test_doctor_json_schema_missing_db(data_dir: Path, capsys):
    rc, out, _ = _run(["doctor", "--json"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["version"] == 1
    assert isinstance(payload["checks"], list)
    assert {"ok", "warn", "fail"} == set(payload["summary"].keys())
    assert payload["summary"]["fail"] == 0
    for c in payload["checks"]:
        assert {"id", "status", "message"} <= set(c.keys())
        assert c["status"] in {"OK", "WARN", "FAIL"}
    ids = {c["id"] for c in payload["checks"]}
    assert "store-database" in ids
    # No project DB → store-health subchecks skipped.
    assert "schema-version" not in ids


def test_doctor_fresh_db_all_checks_pass(
    data_dir: Path, tmp_path: Path, monkeypatch, capsys,
):
    sub = tmp_path / "fresh"
    _seed(data_dir, sub)
    monkeypatch.chdir(sub)
    rc, out, _ = _run(["doctor", "--json"], capsys)
    assert rc == 0
    payload = json.loads(out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["schema-version"]["status"] == "OK"
    assert by_id["schema-version"]["details"]["db"] == SCHEMA_VERSION
    assert by_id["wal-mode-active"]["status"] == "OK"
    assert by_id["fts5-integrity"]["status"] == "OK"
    assert by_id["store-stats"]["status"] == "OK"
    assert by_id["store-stats"]["details"]["facts_total"] == 0
    assert by_id["store-stats"]["details"]["adrs_total"] == 0
    assert by_id["token-cap-pressure"]["status"] == "OK"
    assert by_id["stale-entries"]["status"] == "OK"
    assert by_id["db-size-vacuum"]["status"] == "OK"
    assert by_id["pin-sanity"]["status"] == "OK"
    assert payload["summary"]["fail"] == 0


def test_doctor_counts_after_inserts(
    data_dir: Path, tmp_path: Path, monkeypatch, capsys,
):
    sub = tmp_path / "seeded"
    _seed(data_dir, sub, facts=3, adrs=2)
    monkeypatch.chdir(sub)
    rc, out, _ = _run(["doctor", "--json"], capsys)
    assert rc == 0
    payload = json.loads(out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["store-stats"]["details"]["facts_total"] == 3
    assert by_id["store-stats"]["details"]["adrs_total"] == 2
    assert by_id["store-stats"]["details"]["tokens_estimated_total"] > 0
    # New rows have last_accessed_at NULL → counted as "stale" by design.
    assert by_id["stale-entries"]["details"]["stale_facts"] == 3


def test_doctor_cap_pressure_warns(
    data_dir: Path, tmp_path: Path, monkeypatch, capsys,
):
    sub = tmp_path / "near_cap"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        # Long value: ~ (chars/4) tokens ≫ 80% of default 100 fact cap.
        store.add_fact("a" * 600, force=True)
    finally:
        store.close()
    monkeypatch.chdir(sub)
    rc, out, _ = _run(["doctor", "--json"], capsys)
    assert rc == 0
    payload = json.loads(out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["token-cap-pressure"]["status"] == "WARN"
    assert by_id["token-cap-pressure"]["details"]["facts_near_cap"] >= 1
    assert payload["summary"]["fail"] == 0


def test_doctor_human_format_renders_sections(
    data_dir: Path, tmp_path: Path, monkeypatch, capsys,
):
    sub = tmp_path / "human"
    _seed(data_dir, sub, facts=1)
    monkeypatch.chdir(sub)
    rc, out, _ = _run(["doctor"], capsys)
    assert rc == 0
    assert "mindkeep doctor" in out
    assert "Environment" in out
    assert "Store health" in out
    assert "Schema version up-to-date" in out
    assert "FTS5 integrity check passed" in out
    assert "Store stats:" in out


def test_doctor_warn_only_exits_zero(
    data_dir: Path, tmp_path: Path, monkeypatch, capsys,
):
    # Default fixture leaves no DB; cli-on-path may already be OK. Force
    # at least one WARN by leaving DB missing → store-database WARN.
    rc, _, _ = _run(["doctor"], capsys)
    assert rc == 0


def test_doctor_schema_version_newer_fails(
    data_dir: Path, tmp_path: Path, monkeypatch, capsys,
):
    sub = tmp_path / "newer"
    _seed(data_dir, sub)
    monkeypatch.chdir(sub)
    # Pretend this binary is older than the on-disk schema. Patch the
    # constant the doctor compares against (Storage open re-stamps the DB
    # to its own SCHEMA_VERSION via migrate_to_v3, so we can't simply bump
    # meta.schema_version directly).
    monkeypatch.setattr(cli, "SCHEMA_VERSION", SCHEMA_VERSION - 1)
    rc, out, _ = _run(["doctor", "--json"], capsys)
    assert rc == 1
    payload = json.loads(out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["schema-version"]["status"] == "FAIL"
    assert payload["summary"]["fail"] >= 1

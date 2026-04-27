"""Tests for mindkeep.integration — the agent-facing convenience API."""
from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mindkeep.integration import (
    load_project_memory,
    recall,
    save_decision,
)
from mindkeep.memory_api import MemoryStore


# ───────────────────── helpers ─────────────────────


def _open(tmp_path: Path, *, auto_flush: bool = False) -> MemoryStore:
    # auto_flush defaults False in tests to keep threads deterministic.
    return load_project_memory(
        cwd=tmp_path, auto_flush=auto_flush, data_dir=tmp_path
    )


# ───────────────────── load_project_memory ─────────────────────


def test_load_project_memory_returns_open_store(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        assert isinstance(store, MemoryStore)
        assert not store.closed
        assert store.db_path.exists()
        assert store.db_path.parent == tmp_path
    finally:
        store.close()
    assert store.closed


def test_load_project_memory_context_manager(tmp_path: Path) -> None:
    with _open(tmp_path) as store:
        store.add_fact("hello", tags=["smoke"])
    assert store.closed


def test_load_project_memory_auto_flush_does_not_crash(tmp_path: Path) -> None:
    # auto_flush=True spawns a daemon Timer; we just verify it starts and the
    # wrapped close() tears it down cleanly (no lingering exceptions).
    store = load_project_memory(
        cwd=tmp_path, auto_flush=True, data_dir=tmp_path
    )
    try:
        store.add_fact("flushed", tags=["smoke"])
    finally:
        store.close()
    assert store.closed


# ───────────────────── save_decision ─────────────────────


def test_save_decision_roundtrip(tmp_path: Path) -> None:
    with _open(tmp_path) as store:
        rid = save_decision(
            store,
            title="Adopt SQLite per project",
            decision="One DB file per project hash.",
            rationale="Isolation + simpler backup.",
            tags=["architecture"],
        )
        assert isinstance(rid, int) and rid > 0

        adrs = store.list_adrs()
        assert len(adrs) == 1
        row = adrs[0]
        assert row["title"] == "Adopt SQLite per project"
        assert row["status"] == "accepted"
        assert row["decision"] == "One DB file per project hash."
        assert "architecture" in row["tags"]
        # Auto-numbered starting at 1.
        assert int(row["number"]) == 1


def test_save_decision_rejects_empty_inputs(tmp_path: Path) -> None:
    with _open(tmp_path) as store:
        with pytest.raises(ValueError):
            save_decision(store, title="  ", decision="x")
        with pytest.raises(ValueError):
            save_decision(store, title="ok", decision="")


# ───────────────────── recall ─────────────────────


def test_recall_returns_all_four_keys_even_when_empty(tmp_path: Path) -> None:
    with _open(tmp_path) as store:
        snap = recall(store)
    assert set(snap.keys()) == {
        "facts",
        "adrs",
        "preferences",
        "recent_sessions",
    }
    assert snap["facts"] == []
    assert snap["adrs"] == []
    assert snap["preferences"] == {}
    assert snap["recent_sessions"] == []


def test_recall_aggregates_written_data(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _open(tmp_path) as store:
        store.add_fact("pg15 is target", tags=["architecture", "data"])
        store.add_fact("flaky test in checkout", tags=["bug", "testing"])
        save_decision(
            store,
            title="Use RS256 for JWT",
            decision="Sign with RS256.",
            rationale="Avoid shared symmetric secret.",
            tags=["security", "architecture"],
        )
        store.set_preference("style.quote", "single", scope="user")
        store.add_session_summary(
            summary="set up auth", started_at=now, ended_at=now, turn_count=3
        )

        snap = recall(store)

    assert len(snap["facts"]) == 2
    assert len(snap["adrs"]) == 1
    assert snap["preferences"]["style.quote"] == "single"
    assert len(snap["recent_sessions"]) == 1


def test_recall_topic_filters_facts_and_adrs(tmp_path: Path) -> None:
    with _open(tmp_path) as store:
        store.add_fact("db fact", tags=["data"])
        store.add_fact("sec fact", tags=["security"])
        save_decision(
            store,
            title="Sec decision",
            decision="decide",
            rationale="",
            tags=["security"],
        )
        save_decision(
            store,
            title="Unrelated decision",
            decision="decide",
            rationale="",
            tags=["architecture"],
        )
        store.set_preference("repo.owner", "acme")

        snap = recall(store, topic="security")

    assert len(snap["facts"]) == 1
    assert snap["facts"][0]["value"] == "sec fact"
    assert len(snap["adrs"]) == 1
    assert snap["adrs"][0]["title"] == "Sec decision"
    # Preferences are NOT tag-filtered — they're still present.
    assert snap["preferences"]["repo.owner"] == "acme"


# ───────────────────── redaction (soft dep) ─────────────────────


def _security_available() -> bool:
    try:
        importlib.import_module("mindkeep.security")
        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _security_available(),
    reason="mindkeep.security module not yet available (parallel agent)",
)
def test_load_project_memory_redacts_secrets_when_available(
    tmp_path: Path,
) -> None:
    # A plausible GitHub PAT pattern per ARCHITECTURE.md §8 defaults.
    fake_secret = "ghp_" + "A" * 36
    with _open(tmp_path) as store:
        store.add_fact(f"token is {fake_secret}", tags=["bug"])
        facts = store.list_facts()

    assert facts, "fact should have been persisted"
    stored = facts[0]["value"]
    assert fake_secret not in stored, (
        f"SecretsRedactor did not redact the fake PAT; stored value: {stored!r}"
    )

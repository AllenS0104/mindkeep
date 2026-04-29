"""Tests for mindkeep.memory_api.MemoryStore (minimal-mode surface)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from mindkeep.memory_api import MemoryStore, Filter
from mindkeep.storage import Storage


# ───────────────────── fixtures ─────────────────────


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        yield s
    finally:
        if not s.closed:
            s.close()


# ───────────────────── 1. open creates DB ─────────────────────


def test_open_creates_db_file(tmp_path: Path) -> None:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        assert s.db_path.exists()
        assert s.db_path.parent == tmp_path
        assert s.db_path.name.endswith(".db")
        # File stem is the 12-hex project hash.
        stem = s.db_path.stem
        assert len(stem) == 12
        assert all(c in "0123456789abcdef" for c in stem)
    finally:
        s.close()


def test_project_id_property_is_readable(tmp_path: Path) -> None:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        pid = s.project_id
        assert pid.id == s.db_path.stem
        assert pid.source in {"git_remote", "cwd_hash"}
        assert isinstance(pid.display_name, str)
    finally:
        s.close()


# ───────────────────── 2. facts CRUD ─────────────────────


def test_add_fact_and_list_facts_basic(store: MemoryStore) -> None:
    rid1 = store.add_fact("prefer uv over pip")
    rid2 = store.add_fact("use pytest", tags=["tool", "test"], source="human")
    assert rid1 > 0 and rid2 > 0 and rid1 != rid2

    facts = store.list_facts()
    assert len(facts) == 2
    values = {f["value"] for f in facts}
    assert values == {"prefer uv over pip", "use pytest"}


def test_list_facts_filter_by_tag(store: MemoryStore) -> None:
    store.add_fact("a", tags=["x"])
    store.add_fact("b", tags=["y"])
    store.add_fact("c", tags=["x", "y"])

    only_x = store.list_facts(tag="x")
    assert {f["value"] for f in only_x} == {"a", "c"}

    only_y = store.list_facts(tag="y")
    assert {f["value"] for f in only_y} == {"b", "c"}

    none = store.list_facts(tag="zzz")
    assert none == []


def test_list_facts_orders_newest_first(store: MemoryStore) -> None:
    store.add_fact("first")
    time.sleep(1.01)  # ISO precision is seconds
    store.add_fact("second")
    facts = store.list_facts()
    assert [f["value"] for f in facts[:2]] == ["second", "first"]


def test_list_facts_respects_limit(store: MemoryStore) -> None:
    for i in range(5):
        store.add_fact(f"f{i}")
    assert len(store.list_facts(limit=3)) == 3


# ───────────────────── 3. ADRs ─────────────────────


def test_add_adr_auto_numbers_and_supersedes(store: MemoryStore) -> None:
    a1 = store.add_adr(title="choose sqlite", decision="use sqlite", rationale="simple")
    a2 = store.add_adr(
        title="choose wal",
        decision="enable WAL",
        rationale="perf",
        supersedes=a1,
    )
    assert a2 > a1

    adrs = store.list_adrs()
    # Default ordering is `pin DESC, created_at DESC` (P1-8); both ADRs
    # are unpinned and a2 is newer, so it surfaces first.
    assert [a["number"] for a in adrs] == [2, 1]
    assert adrs[1]["title"] == "choose sqlite"
    assert adrs[0]["supersedes"] == a1
    # rationale is persisted as context.
    assert adrs[0]["context"] == "perf"
    assert adrs[0]["decision"] == "enable WAL"


def test_list_adrs_filter_by_status(store: MemoryStore) -> None:
    store.add_adr("a", "d1", "r1", status="accepted")
    store.add_adr("b", "d2", "r2", status="proposed")
    store.add_adr("c", "d3", "r3", status="accepted")

    accepted = store.list_adrs(status="accepted")
    assert {a["title"] for a in accepted} == {"a", "c"}
    proposed = store.list_adrs(status="proposed")
    assert {a["title"] for a in proposed} == {"b"}


# ───────────────────── 4. preferences upsert ─────────────────────


def test_set_preference_upsert_keeps_latest(store: MemoryStore) -> None:
    store.set_preference("ui.theme", "dark")
    first = store.get_preference("ui.theme")
    assert first == "dark"

    # Overwrite.
    time.sleep(1.01)
    store.set_preference("ui.theme", "light")
    assert store.get_preference("ui.theme") == "light"

    # Only one row persisted.
    rows = store._pref_storage.query("preferences", key="ui.theme")
    assert len(rows) == 1
    # created_at preserved, updated_at advanced.
    assert rows[0]["created_at"] <= rows[0]["updated_at"]
    assert rows[0]["value"] == "light"


def test_get_preference_default(store: MemoryStore) -> None:
    assert store.get_preference("missing") is None
    assert store.get_preference("missing", default="fallback") == "fallback"
    store.set_preference("exists", "v")
    assert store.get_preference("exists", default="fallback") == "v"


# ───────────────────── 5. session summaries ─────────────────────


def test_add_session_summary_and_recent_order(store: MemoryStore) -> None:
    store.add_session_summary(
        "older",
        started_at="2024-01-01T00:00:00+00:00",
        ended_at="2024-01-01T01:00:00+00:00",
        turn_count=5,
    )
    store.add_session_summary(
        "newer",
        started_at="2024-02-01T00:00:00+00:00",
        ended_at="2024-02-01T01:00:00+00:00",
        turn_count=10,
    )
    store.add_session_summary(
        "middle",
        started_at="2024-01-15T00:00:00+00:00",
        ended_at="2024-01-15T01:00:00+00:00",
    )

    recent = store.recent_sessions()
    assert [r["summary"] for r in recent] == ["newer", "middle", "older"]
    assert len(recent) == 3

    # Limit works.
    assert len(store.recent_sessions(limit=1)) == 1


def test_session_turn_count_persisted_in_refs(store: MemoryStore) -> None:
    import json as _json
    store.add_session_summary(
        "s",
        started_at="2024-01-01T00:00:00+00:00",
        ended_at="2024-01-01T00:10:00+00:00",
        turn_count=42,
    )
    rows = store._storage.query("session_summaries")
    assert len(rows) == 1
    parsed = _json.loads(rows[0]["refs"])
    assert parsed == {"turn_count": 42}


# ───────────────────── 6. clear semantics ─────────────────────


def test_clear_all_wipes_business_tables_preserves_meta(store: MemoryStore) -> None:
    store.add_fact("f")
    store.add_adr("t", "d", "r")
    store.set_preference("k", "v")
    store.add_session_summary(
        "s", started_at="2024-01-01T00:00:00+00:00",
        ended_at="2024-01-01T00:01:00+00:00",
    )

    removed = store.clear(None)
    assert removed == 4

    assert store.list_facts() == []
    assert store.list_adrs() == []
    assert store.recent_sessions() == []
    assert store.get_preference("k") is None

    # Meta still intact.
    meta = store._storage.query("meta")
    assert len(meta) == 1


def test_clear_single_kind(store: MemoryStore) -> None:
    store.add_fact("f1")
    store.add_fact("f2")
    store.add_adr("t", "d", "r")
    store.set_preference("k", "v")

    removed = store.clear(["facts"])
    assert removed == 2
    assert store.list_facts() == []
    # Others untouched.
    assert len(store.list_adrs()) == 1
    assert store.get_preference("k") == "v"


def test_clear_unknown_kind_raises(store: MemoryStore) -> None:
    with pytest.raises(ValueError):
        store.clear(["bogus"])


# ───────────────────── 7. filter hook ─────────────────────


class _UppercaseFilter:
    """Deterministic filter: records every invocation + uppercases value."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def apply(self, kind: str, field: str, value: str) -> str:
        self.calls.append((kind, field, value))
        return value.upper()


def test_filter_runs_on_every_text_write(tmp_path: Path) -> None:
    filt = _UppercaseFilter()
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path, filters=[filt])
    try:
        s.add_fact("hello")
        s.add_adr("Title", decision="do it", rationale="because")
        s.set_preference("k", "val")
        s.add_session_summary(
            "summary-text",
            started_at="2024-01-01T00:00:00+00:00",
            ended_at="2024-01-01T00:01:00+00:00",
        )

        kinds = [c[0] for c in filt.calls]
        assert "fact" in kinds
        assert kinds.count("adr") == 2  # decision + rationale
        assert "preference" in kinds
        assert "session" in kinds

        # Stored values are uppercased.
        assert s.list_facts()[0]["value"] == "HELLO"
        adr = s.list_adrs()[0]
        assert adr["decision"] == "DO IT"
        assert adr["context"] == "BECAUSE"
        assert s.get_preference("k") == "VAL"
        assert s.recent_sessions()[0]["summary"] == "SUMMARY-TEXT"
    finally:
        s.close()


def test_empty_filter_list_is_noop(tmp_path: Path) -> None:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path, filters=[])
    try:
        s.add_fact("untouched")
        assert s.list_facts()[0]["value"] == "untouched"
    finally:
        s.close()


def test_filter_pipeline_chains_in_order(tmp_path: Path) -> None:
    class Append:
        def __init__(self, suffix: str) -> None:
            self.suffix = suffix

        def apply(self, kind: str, field: str, value: str) -> str:
            return value + self.suffix

    s = MemoryStore.open(
        cwd=tmp_path,
        data_dir=tmp_path,
        filters=[Append("-a"), Append("-b")],
    )
    try:
        s.add_fact("x")
        assert s.list_facts()[0]["value"] == "x-a-b"
    finally:
        s.close()


# ───────────────────── 8. lifecycle ─────────────────────


def test_context_manager_closes_storage(tmp_path: Path) -> None:
    with MemoryStore.open(cwd=tmp_path, data_dir=tmp_path) as s:
        s.add_fact("f")
        inner = s._storage
        assert inner._closed is False  # type: ignore[attr-defined]
    # After the with-block, storage must be closed.
    assert inner._closed is True  # type: ignore[attr-defined]
    assert s.closed is True


def test_close_is_idempotent(tmp_path: Path) -> None:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    s.close()
    s.close()  # must not raise


def test_commit_can_be_called_explicitly(store: MemoryStore) -> None:
    store.add_fact("persist me")
    # Should not raise — Storage.commit is safe to call repeatedly.
    store.commit()
    store.commit()


def test_commit_after_close_raises(tmp_path: Path) -> None:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    s.close()
    with pytest.raises(RuntimeError):
        s.commit()


# ───────────────────── 9. Filter protocol runtime check ─────────────────────


def test_filter_protocol_structural_match() -> None:
    class Good:
        def apply(self, kind: str, field: str, value: str) -> str:
            return value

    assert isinstance(Good(), Filter)


# ───────────────────── P0/P1 regression tests ─────────────────────


def test_preferences_are_cross_project(tmp_path: Path) -> None:
    """P0-3: preferences set in project A are visible from project B."""
    data_dir = tmp_path / "shared"
    proj_a = tmp_path / "A"; proj_a.mkdir()
    proj_b = tmp_path / "B"; proj_b.mkdir()

    store_a = MemoryStore.open(cwd=proj_a, data_dir=data_dir)
    try:
        store_a.set_preference("style.quote", "single", scope="user")
        # project ids must differ — we are truly talking about two projects.
        pid_a = store_a.project_id.id
    finally:
        store_a.close()

    store_b = MemoryStore.open(cwd=proj_b, data_dir=data_dir)
    try:
        pid_b = store_b.project_id.id
        assert pid_a != pid_b
        assert store_b.get_preference("style.quote") == "single"
        rows = store_b.list_preferences()
        assert any(r["key"] == "style.quote" and r["value"] == "single"
                   for r in rows)
    finally:
        store_b.close()


def test_concurrent_add_adr_no_duplicate_numbers(tmp_path: Path) -> None:
    """P1-1: 10 threads × 10 add_adr calls produce 100 unique numbers."""
    import threading

    store = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        errors: list[BaseException] = []
        barrier = threading.Barrier(10)

        def worker(tid: int) -> None:
            try:
                barrier.wait(timeout=5)
                for i in range(10):
                    store.add_adr(
                        title=f"t-{tid}-{i}",
                        decision="d",
                        rationale="r",
                    )
            except BaseException as exc:  # pragma: no cover - on failure
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors

        adrs = store.list_adrs()
        numbers = [int(a["number"]) for a in adrs]
        assert len(numbers) == 100
        assert len(set(numbers)) == 100, "duplicate ADR numbers observed"
        assert sorted(numbers) == list(range(1, 101))
    finally:
        store.close()


def test_concurrent_set_preference_no_exception(tmp_path: Path) -> None:
    """P1-2: many writers hammering the same key converge to one row."""
    import threading

    store = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        errors: list[BaseException] = []
        values_written: list[str] = []
        barrier = threading.Barrier(8)

        def worker(tid: int) -> None:
            try:
                barrier.wait(timeout=5)
                for i in range(25):
                    v = f"t{tid}-i{i}"
                    store.set_preference("hot.key", v)
                    values_written.append(v)
            except BaseException as exc:  # pragma: no cover - on failure
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, errors
        # One row only (UNIQUE(key)), with some written value.
        rows = store._pref_storage.query("preferences", key="hot.key")
        assert len(rows) == 1
        assert rows[0]["value"] in set(values_written)
    finally:
        store.close()


def test_list_preferences_returns_rows(tmp_path: Path) -> None:
    """P0-3 / P1-8: list_preferences is a public method on MemoryStore."""
    store = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        store.set_preference("a", "1")
        store.set_preference("b", "2")
        rows = store.list_preferences()
        keys = {r["key"] for r in rows}
        assert keys == {"a", "b"}
        # prefix filter
        assert [r["key"] for r in store.list_preferences(prefix="a")] == ["a"]
    finally:
        store.close()

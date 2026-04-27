"""verify remember_*/recall_* aliases match add_*/list_*."""
from __future__ import annotations

from pathlib import Path

from mindkeep import MemoryStore


def test_remember_fact_is_add_fact() -> None:
    assert MemoryStore.remember_fact is MemoryStore.add_fact


def test_remember_adr_is_add_adr() -> None:
    assert MemoryStore.remember_adr is MemoryStore.add_adr


def test_recall_facts_is_list_facts() -> None:
    assert MemoryStore.recall_facts is MemoryStore.list_facts


def test_recall_adrs_is_list_adrs() -> None:
    assert MemoryStore.recall_adrs is MemoryStore.list_adrs


def test_alias_call_equivalence(tmp_path: Path) -> None:
    """Calling via alias must produce equivalent rows to calling via real name."""
    with MemoryStore.open(cwd=tmp_path, data_dir=tmp_path) as store:
        id1 = store.add_fact("via add_fact", tags=["a"])
        id2 = store.remember_fact("via remember_fact", tags=["b"])
        assert id1 != id2

        rows = store.recall_facts(limit=10)
        contents = {r.get("content") or r.get("value") for r in rows}
        assert "via add_fact" in contents
        assert "via remember_fact" in contents

        # ADRs alias parity
        adr_id = store.remember_adr(
            title="alias test",
            decision="use aliases",
            rationale="agent-facing voice",
        )
        assert isinstance(adr_id, int)
        adrs = store.recall_adrs()
        assert any(a.get("title") == "alias test" for a in adrs)

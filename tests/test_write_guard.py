"""Tests for the dual-threshold write guard (P1-7, issue #12)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mindkeep.memory_api import MemoryStore
from mindkeep.security import SecretsRedactor
from mindkeep.storage import StorageError, WriteGuardError


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    try:
        yield s
    finally:
        if not s.closed:
            s.close()


@pytest.fixture
def redacting_store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore.open(
        cwd=tmp_path, data_dir=tmp_path, filters=[SecretsRedactor()]
    )
    try:
        yield s
    finally:
        if not s.closed:
            s.close()


def _chars(tokens: int) -> str:
    """Build a payload that estimates to roughly ``tokens`` tokens."""
    return "x" * (tokens * 4 + 1)


def test_small_fact_passes_and_persists_token_estimate(store: MemoryStore) -> None:
    rid = store.add_fact(_chars(50))
    rows = store.list_facts()
    [row] = [r for r in rows if r["id"] == rid]
    assert row["token_estimate"] is not None
    assert 40 <= int(row["token_estimate"]) <= 60


def test_oversize_fact_raises(store: MemoryStore) -> None:
    with pytest.raises(WriteGuardError) as exc:
        store.add_fact(_chars(200))
    msg = str(exc.value).lower()
    assert "facts" in msg
    assert "cap" in msg
    assert exc.value.kind == "fact"
    assert isinstance(exc.value, StorageError)


def test_oversize_fact_with_force_passes(store: MemoryStore) -> None:
    rid = store.add_fact(_chars(200), force=True)
    assert rid > 0


def test_adr_within_cap_passes(store: MemoryStore) -> None:
    rid = store.add_adr(
        title=_chars(100),
        decision=_chars(700),
        rationale=_chars(600),
    )
    adrs = store.list_adrs()
    [row] = [r for r in adrs if r["id"] == rid]
    assert row["token_estimate"] is not None
    assert 1300 <= int(row["token_estimate"]) <= 1500


def test_oversize_adr_raises(store: MemoryStore) -> None:
    with pytest.raises(WriteGuardError) as exc:
        store.add_adr(
            title=_chars(100),
            decision=_chars(900),
            rationale=_chars(700),
        )
    assert exc.value.kind == "adr"
    assert "adrs" in str(exc.value).lower()


def test_secrets_only_pre_redaction_passes_quietly(
    redacting_store: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_pat = "ghp_" + "A" * 36
    body = " ".join([fake_pat] * 25)
    redacting_store.add_adr(title="seed", decision=body, rationale="-")
    err = capsys.readouterr().err
    assert "redaction trimmed" not in err, err


def test_mostly_redacted_large_payload_warns(
    redacting_store: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_pat = "github_pat_" + "A" * 82
    body = " ".join([fake_pat] * 50)
    redacting_store.add_adr(title="seed", decision=body, rationale="-")
    err = capsys.readouterr().err
    assert "redaction trimmed" in err, err


def test_huge_pre_redaction_raises_even_when_redaction_could_fix_it(
    redacting_store: MemoryStore,
) -> None:
    huge = _chars(5000)
    with pytest.raises(WriteGuardError) as exc:
        redacting_store.add_fact(huge)
    msg = str(exc.value).lower()
    assert "2×" in msg or "2x" in msg or "unstructured" in msg
    assert exc.value.pre_tokens >= 2 * exc.value.cap


def test_env_var_overrides_facts_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINDKEEP_FACTS_TOKEN_CAP", "200")
    with MemoryStore.open(cwd=tmp_path, data_dir=tmp_path) as s:
        rid = s.add_fact(_chars(150))
        assert rid > 0


def test_env_var_overrides_adrs_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINDKEEP_ADRS_TOKEN_CAP", "300")
    with MemoryStore.open(cwd=tmp_path, data_dir=tmp_path) as s:
        with pytest.raises(WriteGuardError):
            s.add_adr(title=_chars(50), decision=_chars(200), rationale=_chars(100))


def test_force_kwarg_is_keyword_only(store: MemoryStore) -> None:
    with pytest.raises(TypeError):
        store.add_fact(_chars(50), None, None, True)  # type: ignore[misc]

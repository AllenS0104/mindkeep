"""Tests for session-level token budget (P0-2, issue #7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mindkeep import _session, _tokens
from mindkeep.cli import main as cli_main


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Redirect session state into tmp_path and clear budget env."""
    state_dir = tmp_path / "mindkeep-session"
    monkeypatch.setattr(_session, "_state_dir", lambda: state_dir)
    # Pin the PID so tests are deterministic regardless of pytest's PID.
    monkeypatch.setattr(_session, "_shell_pid", lambda: 4242)
    monkeypatch.delenv("MINDKEEP_SESSION_BUDGET", raising=False)
    yield


# ─────────────────── token estimator ───────────────────


def test_estimate_empty():
    assert _tokens.estimate("") == 0


def test_estimate_ascii_quarter():
    # 8 ASCII chars → 2 tokens
    assert _tokens.estimate("abcdefgh") == 2


def test_estimate_cjk_half():
    # 4 CJK chars → 2 tokens
    assert _tokens.estimate("你好世界") == 2


def test_estimate_min_one():
    assert _tokens.estimate("a") == 1


# ─────────────────── state file lifecycle ───────────────────


def test_status_no_file_returns_zeros():
    st = _session.status()
    assert st["spent"] == 0
    assert st["calls"] == 0
    assert st["budget"] == 0
    assert not Path(st["path"]).exists()


def test_check_and_record_creates_file():
    allowed, st = _session.check_and_record("hello world")
    assert allowed is True
    assert st["spent"] >= 1
    assert st["calls"] == 1
    assert _session.state_path().exists()


def test_unset_budget_does_not_block_but_tracks():
    st = None
    for _ in range(3):
        allowed, st = _session.check_and_record("x" * 400)
        assert allowed is True
    assert st is not None
    assert st["calls"] == 3
    assert st["spent"] >= 300  # 400/4 * 3


def test_budget_blocks_when_exceeded(monkeypatch):
    monkeypatch.setenv("MINDKEEP_SESSION_BUDGET", "100")
    allowed1, st1 = _session.check_and_record("x" * 240)  # 60 tokens
    assert allowed1 is True
    assert st1["spent"] == 60
    allowed2, st2 = _session.check_and_record("x" * 240)  # would be 120
    assert allowed2 is False
    # spent NOT bumped on rejection; calls IS.
    assert st2["spent"] == 60
    assert st2["calls"] == 2


def test_reset_removes_file():
    _session.check_and_record("seed")
    assert _session.state_path().exists()
    assert _session.reset() is True
    assert not _session.state_path().exists()
    # Idempotent.
    assert _session.reset() is False


# ─────────────────── CLI integration ───────────────────


def _run_cli(argv, capsys):
    rc = cli_main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def test_cli_session_status_no_file(capsys):
    rc, out, err = _run_cli(["session", "status"], capsys)
    assert rc == 0
    assert "spent:      0" in out
    assert "budget:     0" in out
    assert "calls:      0" in out


def test_cli_session_reset(capsys):
    _session.check_and_record("seed")
    assert _session.state_path().exists()
    rc, out, _ = _run_cli(["session", "reset"], capsys)
    assert rc == 0
    assert "reset session state" in out
    assert not _session.state_path().exists()
    rc, out, _ = _run_cli(["session", "reset"], capsys)
    assert rc == 0
    assert "no session state" in out


def test_cli_show_records_spend(tmp_path, monkeypatch, capsys):
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    from mindkeep.memory_api import MemoryStore
    store = MemoryStore.open(cwd=tmp_path, data_dir=home)
    try:
        store.add_fact("hello world")
    finally:
        store.close()

    rc, out, err = _run_cli(["show", "--kind", "facts", "--limit", "1"], capsys)
    assert rc == 0
    assert "project:" in out
    st = _session.load_state()
    assert st["spent"] > 0
    assert st["calls"] == 1


def test_cli_show_suppressed_when_budget_exhausted(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("MINDKEEP_SESSION_BUDGET", "1")  # tiny
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    from mindkeep.memory_api import MemoryStore
    store = MemoryStore.open(cwd=tmp_path, data_dir=home)
    try:
        store.add_fact("hello world")
    finally:
        store.close()

    rc, out, err = _run_cli(["show", "--kind", "facts", "--limit", "1"], capsys)
    assert rc == 0
    assert out == ""  # output suppressed
    assert "session budget reached" in err
    st = _session.load_state()
    # Rejected calls still bump the call counter.
    assert st["calls"] == 1

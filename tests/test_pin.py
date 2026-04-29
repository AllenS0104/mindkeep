"""Tests for pin/unpin (issue #13, P1-8).

Covers the Python API surface (``add_fact(pin=True)`` /
``pin_fact`` / ``unpin_fact`` / ``pinned_only`` / default ordering) and
the corresponding CLI subcommands (``mindkeep pin <kind> <id>``,
``mindkeep show --pinned``).
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

import pytest

from mindkeep.memory_api import MemoryStore


# ──────────────────────────── fixtures ────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore.open(cwd=tmp_path, data_dir=tmp_path)
    yield s
    s.close()


# ──────────────────────────── facts: pin via add_fact ────────────────────────────


def test_add_fact_pin_true_persists(store: MemoryStore) -> None:
    fid = store.add_fact("pinned fact", pin=True)
    rows = store.list_facts()
    [row] = [r for r in rows if r["id"] == fid]
    assert int(row["pin"]) == 1


def test_add_fact_pin_false_default(store: MemoryStore) -> None:
    fid = store.add_fact("unpinned")
    [row] = [r for r in store.list_facts() if r["id"] == fid]
    assert int(row["pin"]) == 0


# ──────────────────────────── facts: pin/unpin round-trip ────────────────────────────


def test_pin_unpin_fact_round_trip(store: MemoryStore) -> None:
    fid = store.add_fact("subject")
    assert int(store.list_facts()[0]["pin"]) == 0

    store.pin_fact(fid)
    [row] = [r for r in store.list_facts() if r["id"] == fid]
    assert int(row["pin"]) == 1

    store.unpin_fact(fid)
    [row] = [r for r in store.list_facts() if r["id"] == fid]
    assert int(row["pin"]) == 0


def test_pin_fact_unknown_id_raises(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="fact id 999 not found"):
        store.pin_fact(999)
    with pytest.raises(ValueError, match="fact id 999 not found"):
        store.unpin_fact(999)


# ──────────────────────────── facts: ordering & pinned_only ────────────────────────────


def test_list_facts_pinned_first_default_ordering(store: MemoryStore) -> None:
    # Insert 5 facts; pin two of them (the 2nd and 4th inserted).
    ids = [store.add_fact(f"f{i}") for i in range(5)]
    store.pin_fact(ids[1])
    store.pin_fact(ids[3])

    rows = store.list_facts()
    # The two pinned rows must appear before any unpinned row.
    pinned_positions = [i for i, r in enumerate(rows) if int(r["pin"]) == 1]
    assert pinned_positions == [0, 1]
    # And both pinned fact ids are present at the front.
    assert {rows[0]["id"], rows[1]["id"]} == {ids[1], ids[3]}


def test_list_facts_pinned_only_filters(store: MemoryStore) -> None:
    a = store.add_fact("a")
    store.add_fact("b")
    c = store.add_fact("c", pin=True)
    store.pin_fact(a)

    pinned = store.list_facts(pinned_only=True)
    assert {r["id"] for r in pinned} == {a, c}
    assert all(int(r["pin"]) == 1 for r in pinned)


# ──────────────────────────── ADRs: parity with facts ────────────────────────────


def test_add_adr_pin_true_and_pin_adr(store: MemoryStore) -> None:
    a1 = store.add_adr("t1", decision="d1", rationale="r1")
    a2 = store.add_adr("t2", decision="d2", rationale="r2", pin=True)

    rows = store.list_adrs()
    by_id = {r["id"]: r for r in rows}
    assert int(by_id[a1]["pin"]) == 0
    assert int(by_id[a2]["pin"]) == 1
    # Pinned a2 must appear before unpinned a1.
    assert rows[0]["id"] == a2

    store.unpin_adr(a2)
    store.pin_adr(a1)
    rows = store.list_adrs()
    assert rows[0]["id"] == a1
    assert int(rows[0]["pin"]) == 1


def test_list_adrs_pinned_only(store: MemoryStore) -> None:
    store.add_adr("t1", decision="d1", rationale="r1")
    a2 = store.add_adr("t2", decision="d2", rationale="r2", pin=True)
    pinned = store.list_adrs(pinned_only=True)
    assert [r["id"] for r in pinned] == [a2]


def test_pin_adr_unknown_id_raises(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="adr id 4242 not found"):
        store.pin_adr(4242)


# ──────────────────────────── CLI ────────────────────────────


def _run_main(data_dir: Path, cwd: Path, *args: str) -> tuple[int, str, str]:
    """Invoke mindkeep CLI in-process, with data_dir patched in."""
    from mindkeep import cli as cli_mod

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


def test_cli_pin_unpin_fact(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cwd = tmp_path / "proj"
    cwd.mkdir()

    # Seed a fact through the API at the same data_dir + cwd so the CLI
    # resolves to the same project.
    s = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    fid = s.add_fact("subject")
    s.close()

    code, out, err = _run_main(data_dir, cwd, "pin", "fact", str(fid))
    assert code == 0, (out, err)
    assert "pinned fact" in out

    # show --pinned should now include the fact.
    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "facts", "--pinned",
    )
    assert code == 0, (out, err)
    assert "subject" in out

    # unpin → show --pinned should be empty (no rows).
    code, out, err = _run_main(data_dir, cwd, "unpin", "fact", str(fid))
    assert code == 0, (out, err)
    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "facts", "--pinned",
    )
    assert code == 0
    assert "(no rows)" in out


def test_cli_pin_unknown_id_exits_2(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cwd = tmp_path / "proj"
    cwd.mkdir()

    # Materialise a project DB so resolution succeeds, then ask to pin
    # a non-existent id.
    MemoryStore.open(cwd=cwd, data_dir=data_dir).close()

    code, out, err = _run_main(data_dir, cwd, "pin", "fact", "999")
    assert code == 2, (out, err)
    assert "not found" in err


def test_cli_show_pinned_only_filters(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cwd = tmp_path / "proj"
    cwd.mkdir()

    s = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    s.add_fact("alpha")
    pinned_id = s.add_fact("bravo", pin=True)
    s.close()

    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "facts", "--pinned",
    )
    assert code == 0, (out, err)
    assert "bravo" in out
    assert "alpha" not in out
    _ = pinned_id  # silence unused

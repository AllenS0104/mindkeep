"""Tests for ``mindkeep show --top K`` and ``--budget N`` (issue #8, P0-3)."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

from mindkeep._tokens import estimate as _estimate_tokens
from mindkeep.memory_api import MemoryStore


# ──────────────────────────── helpers ────────────────────────────


def _run_main(data_dir: Path, cwd: Path, *args: str) -> tuple[int, str, str]:
    """Invoke the mindkeep CLI in-process, with ``data_dir`` patched in."""
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


def _seed(cwd: Path, data_dir: Path,
          n_facts: int = 5, n_adrs: int = 5,
          pinned_fact: bool = False) -> None:
    s = MemoryStore.open(cwd=cwd, data_dir=data_dir)
    try:
        for i in range(n_facts):
            s.add_fact(f"fact-{i:02d}-value-content")
        if pinned_fact:
            s.add_fact("PINNED-fact-value", pin=True)
        for i in range(n_adrs):
            s.add_adr(
                title=f"adr-{i:02d}-title",
                decision=f"adr-{i:02d}-decision",
                rationale=f"adr-{i:02d}-rationale",
            )
    finally:
        s.close()


def _data_rows(out: str, kind: str) -> list[str]:
    """Return the data row lines under ``== {kind} ==`` (excluding header/sep)."""
    lines = out.splitlines()
    try:
        start = lines.index(f"== {kind} ==")
    except ValueError:
        return []
    rows: list[str] = []
    # Skip header and separator (two lines after the section header), then collect
    # until the next blank line.
    if start + 1 < len(lines) and lines[start + 1] == "(no rows)":
        return []
    body = lines[start + 3:]
    for line in body:
        if line == "" or line.startswith("== "):
            break
        rows.append(line)
    return rows


# ──────────────────────────── tests ────────────────────────────


def test_top_caps_rows_per_kind(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    _seed(cwd, data_dir, n_facts=5, n_adrs=5)

    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "all", "--top", "2",
    )
    assert code == 0, (out, err)
    assert len(_data_rows(out, "facts")) <= 2
    assert len(_data_rows(out, "adrs")) <= 2


def test_top_zero_yields_no_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    _seed(cwd, data_dir, n_facts=3, n_adrs=3)

    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "all", "--top", "0",
    )
    assert code == 0, (out, err)
    assert _data_rows(out, "facts") == []
    assert _data_rows(out, "adrs") == []
    # Section header still printed with "(no rows)" placeholder.
    assert "== facts ==" in out
    assert "(no rows)" in out


def test_budget_stops_after_rendered_tokens_exceed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    _seed(cwd, data_dir, n_facts=10, n_adrs=10)

    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "all", "--budget", "50",
    )
    assert code == 0, (out, err)
    fact_rows = _data_rows(out, "facts")
    adr_rows = _data_rows(out, "adrs")
    pref_rows = _data_rows(out, "preferences")
    sess_rows = _data_rows(out, "sessions")
    total_tokens = sum(
        _estimate_tokens(line)
        for line in fact_rows + adr_rows + pref_rows + sess_rows
    )
    assert total_tokens <= 50, (total_tokens, out)


def test_budget_too_small_yields_zero_rows_no_crash(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    _seed(cwd, data_dir, n_facts=3, n_adrs=3)

    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "facts", "--budget", "1",
    )
    assert code == 0, (out, err)
    assert _data_rows(out, "facts") == []
    assert "(no rows)" in out


def test_top_and_budget_combined_respect_tighter(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    _seed(cwd, data_dir, n_facts=10, n_adrs=10)

    # Top would allow 5 per kind, but a tight budget should clip earlier.
    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "all",
        "--top", "5", "--budget", "30",
    )
    assert code == 0, (out, err)
    assert len(_data_rows(out, "facts")) <= 5
    assert len(_data_rows(out, "adrs")) <= 5
    total_tokens = sum(
        _estimate_tokens(line)
        for kind in ("facts", "adrs", "preferences", "sessions")
        for line in _data_rows(out, kind)
    )
    assert total_tokens <= 30


def test_pinned_rows_appear_first_under_top(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    # 5 unpinned facts plus one pinned: pinned row must surface within --top 1.
    _seed(cwd, data_dir, n_facts=5, n_adrs=0, pinned_fact=True)

    code, out, err = _run_main(
        data_dir, cwd, "show", "--kind", "facts", "--top", "1",
    )
    assert code == 0, (out, err)
    rows = _data_rows(out, "facts")
    assert len(rows) == 1
    assert "PINNED-fact-value" in rows[0]
    # The asterisk pin marker should be present in the surviving row.
    assert " * " in rows[0] or rows[0].startswith("* ") or "| *" in rows[0]


def test_default_unchanged_no_flags(tmp_path: Path) -> None:
    """Without --top / --budget, output for a small project matches a stable shape."""
    data_dir = tmp_path / "data"; data_dir.mkdir()
    cwd = tmp_path / "proj"; cwd.mkdir()
    _seed(cwd, data_dir, n_facts=3, n_adrs=2)

    code, out, err = _run_main(data_dir, cwd, "show", "--kind", "all")
    assert code == 0, (out, err)
    # All 3 facts and 2 adrs are present (well under the default --limit=20).
    assert len(_data_rows(out, "facts")) == 3
    assert len(_data_rows(out, "adrs")) == 2
    # Section headers exist.
    for kind in ("facts", "adrs", "preferences", "sessions"):
        assert f"== {kind} ==" in out

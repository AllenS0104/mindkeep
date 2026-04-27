"""Tests for ``mindkeep.cli``.

All tests isolate the on-disk store by pointing ``MINDKEEP_HOME`` at a
``tmp_path`` via ``monkeypatch.setenv``. No real user data is touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindkeep import cli
from mindkeep.memory_api import MemoryStore


# ───────────────────────── fixtures ─────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated mindkeep home + predictable cwd project id."""
    home = tmp_path / "mem"
    home.mkdir()
    monkeypatch.setenv("MINDKEEP_HOME", str(home))
    # cwd → tmp_path so resolve_project_id() is deterministic (cwd_hash).
    monkeypatch.chdir(tmp_path)
    return home


def _make_project(
    data_dir: Path, tmp_path: Path, name: str, *,
    facts: int = 0, adrs: int = 0, prefs: int = 0, sessions: int = 0,
    fact_tags: list[str] | None = None,
) -> str:
    """Create a project DB under ``data_dir`` seeded with fixture rows.

    Returns the 12-char project hash.
    """
    sub = tmp_path / name
    sub.mkdir(exist_ok=True)
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        for i in range(facts):
            store.add_fact(f"fact-{name}-{i}", tags=fact_tags)
        for i in range(adrs):
            store.add_adr(f"ADR {name} {i}", "do X", "because Y")
        for i in range(prefs):
            store.set_preference(f"pref-{name}-{i}", f"val-{i}")
        for i in range(sessions):
            store.add_session_summary(
                f"session summary {name} {i}",
                started_at="2024-01-01T00:00:00Z",
                ended_at=f"2024-01-0{i+1}T00:00:00Z",
            )
        ph = store.project_id.id
    finally:
        store.close()
    return ph


# ───────────────────────── tests ─────────────────────────


def test_list_empty_friendly_message(data_dir, capsys):
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No projects yet" in out


def test_list_two_projects_shows_rows(data_dir, tmp_path, capsys):
    ph1 = _make_project(data_dir, tmp_path, "proj1", facts=2)
    ph2 = _make_project(data_dir, tmp_path, "proj2", adrs=1, prefs=3)
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert ph1 in out and ph2 in out
    # Header present
    assert "project_hash" in out and "facts_count" in out
    # Separator line of dashes
    assert "---" in out


def test_show_kind_facts(data_dir, tmp_path, capsys, monkeypatch):
    # current cwd == tmp_path, so default project resolves to it.
    ph = _make_project(data_dir, tmp_path, ".", facts=3)
    # make project resolution match cwd (tmp_path IS the project)
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["show", "--kind", "facts"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "== facts ==" in out
    assert "fact-.-0" in out


def test_show_tag_limit_filter(data_dir, tmp_path, capsys):
    ph = _make_project(data_dir, tmp_path, "tagged",
                       facts=6, fact_tags=["foo", "bar"])
    # And a project without matching tag
    _make_project(data_dir, tmp_path, "other", facts=2, fact_tags=["baz"])
    rc = cli.main(["show", "--project", ph, "--kind", "facts",
                   "--tag", "foo", "--limit", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "== facts ==" in out
    # Max 5 rows rendered — count data rows (after header + separator).
    lines = [l for l in out.splitlines() if "fact-tagged-" in l]
    assert 1 <= len(lines) <= 5


def test_clear_yes_wipes_facts(data_dir, tmp_path, capsys):
    ph = _make_project(data_dir, tmp_path, "wipe", facts=3)
    rc = cli.main(["clear", "--project", ph, "--kind", "facts", "--yes"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cleared 3" in out
    # Now show should have no facts.
    capsys.readouterr()
    rc2 = cli.main(["show", "--project", ph, "--kind", "facts"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "(no rows)" in out2


def test_clear_without_yes_is_refused(data_dir, tmp_path, capsys, monkeypatch):
    ph = _make_project(data_dir, tmp_path, "refuse", facts=2)
    # Simulate user typing "n".
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    rc = cli.main(["clear", "--project", ph, "--kind", "facts"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "aborted" in out
    # Data still present.
    capsys.readouterr()
    cli.main(["show", "--project", ph, "--kind", "facts"])
    out2 = capsys.readouterr().out
    assert "fact-refuse-0" in out2


def test_export_produces_loadable_json(data_dir, tmp_path, capsys):
    ph = _make_project(data_dir, tmp_path, "exp",
                       facts=2, adrs=1, prefs=1, sessions=1)
    out_file = tmp_path / "dump.json"
    rc = cli.main(["export", "--project", ph, str(out_file)])
    assert rc == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    for kind in ("facts", "adrs", "preferences", "sessions"):
        assert kind in payload
    assert len(payload["facts"]) == 2
    assert len(payload["adrs"]) == 1
    assert len(payload["preferences"]) == 1
    assert len(payload["sessions"]) == 1


def test_import_replace_restores_data(data_dir, tmp_path, capsys):
    ph = _make_project(data_dir, tmp_path, "roundtrip",
                       facts=2, adrs=1, prefs=1)
    out_file = tmp_path / "dump.json"
    cli.main(["export", "--project", ph, str(out_file)])
    capsys.readouterr()

    # Wipe the project.
    cli.main(["clear", "--project", ph, "--yes"])
    capsys.readouterr()

    # Restore via --replace.
    rc = cli.main(["import", "--project", ph, "--replace", str(out_file)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "imported" in out
    # Check facts came back.
    cli.main(["show", "--project", ph, "--kind", "facts"])
    out2 = capsys.readouterr().out
    assert "fact-roundtrip-0" in out2


def test_where_prints_data_dir_and_project(data_dir, capsys):
    rc = cli.main(["where"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "data_dir" in out
    assert str(data_dir) in out
    assert "project_id" in out


def test_unknown_subcommand_exit_code_2(data_dir, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus-cmd"])
    assert excinfo.value.code == 2


def test_show_nonexistent_project_errors(data_dir, tmp_path, capsys):
    rc = cli.main(["show", "--project", "does-not-exist"])
    captured = capsys.readouterr()
    assert rc != 0
    assert "project not found" in captured.err


def test_version_flag(data_dir, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out + capsys.readouterr().err
    # argparse writes version to stdout on py>=3.4
    assert "mindkeep" in out or "0.2.0" in out


# ───────────────────────── P0/P1 regression tests ─────────────────────────


def test_import_unknown_columns_warn_and_skip(data_dir, tmp_path, capsys):
    """P0-1 / CLI: unknown columns in a JSON row emit a stderr warning
    and are dropped; legal rows still import."""
    ph = _make_project(data_dir, tmp_path, "imp-unknown", facts=0)
    in_path = tmp_path / "dump.json"
    payload = {
        "facts": [
            {
                "key": "fact-a",
                "value": "hello",
                "tags": "",
                "source": "agent",
                "confidence": 1.0,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                # Trailing unknown column — should be dropped with a warning.
                "bogus_extra": "nope",
            },
        ],
        "adrs": [],
        "preferences": [],
        "sessions": [],
    }
    in_path.write_text(json.dumps(payload), encoding="utf-8")

    rc = cli.main(["import", "--project", ph, str(in_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "warning" in captured.err and "bogus_extra" in captured.err
    assert "imported 1" in captured.out

    # The valid fact is visible.
    cli.main(["show", "--project", ph, "--kind", "facts"])
    shown = capsys.readouterr().out
    assert "fact-a" in shown


def test_exit_code_project_not_found_is_1(data_dir, tmp_path, capsys):
    """P1-6: unknown --project maps to user-error exit code 1."""
    rc = cli.main(["show", "--project", "does-not-exist"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "project not found" in captured.err


def test_exit_code_storage_error_is_2(data_dir, tmp_path, capsys):
    """P1-6: a corrupt DB file bubbles up as exit code 2."""
    # Seed a real project then clobber its DB with garbage bytes.
    ph = _make_project(data_dir, tmp_path, "brk", facts=1)
    db = data_dir / f"{ph}.db"
    # Nuke WAL/SHM then overwrite DB with a header that isn't SQLite.
    for sfx in ("-wal", "-shm"):
        p = db.with_name(db.name + sfx)
        if p.exists():
            p.unlink()
    db.write_bytes(b"not a sqlite file at all" * 10)

    rc = cli.main(["show", "--project", ph, "--kind", "facts"])
    captured = capsys.readouterr()
    assert rc == 2, (rc, captured.err)
    assert "storage failure" in captured.err or "error" in captured.err


# ───────────────────────── UX polish (todo: ux-polish) ─────────────────────────


def test_list_shows_display_name_from_meta(data_dir, tmp_path, capsys):
    """``mindkeep list`` must surface the project's display_name —
    not leave the column blank — after MemoryStore has been opened on it.

    Regression guard for ux-polish: MemoryStore.__init__ now stamps the
    resolved ProjectId.display_name into the per-project meta row, which
    the CLI then reads back (preferring the DB value over the sidecar).
    """
    ph = _make_project(data_dir, tmp_path, "demo-project", facts=1)
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert ph in out
    # display_name comes from Path.name of the project directory in the
    # cwd_hash branch of resolve_project_id — i.e. "demo-project".
    assert "demo-project" in out, out


def test_show_full_prints_untruncated_value(data_dir, tmp_path, capsys):
    """``show --full`` must print the entire ``value`` column, even when
    longer than the default ~52-char truncation threshold."""
    long_val = (
        "this is a long fact that will definitely exceed fifty two "
        "characters for truncation testing 1234567890 ABCDEFG"
    )
    assert len(long_val) > 100
    sub = tmp_path / "full-proj"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact(long_val)
        ph = store.project_id.id
    finally:
        store.close()

    rc = cli.main(["show", "--project", ph, "--kind", "facts", "--full"])
    out = capsys.readouterr().out
    assert rc == 0
    assert long_val in out, out


def test_show_no_truncate_alias_matches_full(data_dir, tmp_path, capsys):
    """``--no-truncate`` is documented as an equivalent alias for ``--full``."""
    long_val = "x" * 200
    sub = tmp_path / "alias-proj"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact(long_val)
        ph = store.project_id.id
    finally:
        store.close()

    rc = cli.main(
        ["show", "--project", ph, "--kind", "facts", "--no-truncate"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert long_val in out, out


def test_show_default_still_truncates(data_dir, tmp_path, capsys):
    """Regression: without ``--full``, long values must still be cut so
    the table keeps its traditional alignment."""
    long_val = "y" * 200
    sub = tmp_path / "trunc-proj"
    sub.mkdir()
    store = MemoryStore.open(cwd=sub, data_dir=data_dir)
    try:
        store.add_fact(long_val)
        ph = store.project_id.id
    finally:
        store.close()

    rc = cli.main(["show", "--project", ph, "--kind", "facts"])
    out = capsys.readouterr().out
    assert rc == 0
    # Full value must NOT be present, ellipsis marker MUST be.
    assert long_val not in out
    assert "..." in out


# ───────────────────────── upgrade subcommand ─────────────────────────


def test_upgrade_dry_run_prints_pip_command_without_executing(
    data_dir, capsys, monkeypatch
):
    """--dry-run must print the install command but never call subprocess.run."""
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs):  # pragma: no cover - should not fire
        called["ran"] = True
        raise AssertionError("subprocess.run must not be called on --dry-run")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    # Force pip (not pipx) branch with a clean executable path.
    monkeypatch.setattr(cli.sys, "executable", "/usr/bin/python3")
    monkeypatch.delenv(cli._UPGRADE_SOURCE_ENV, raising=False)

    rc = cli.main(["upgrade", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ran" not in called
    assert "pip install" in out
    assert "--upgrade" in out
    # Default source must appear.
    assert "git+https://github.com/AllenS0104/mindkeep.git" in out
    assert "dry-run" in out


def test_upgrade_source_flag_overrides_default(data_dir, capsys, monkeypatch):
    """--source must be reflected verbatim in the printed command."""
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no run on dry-run")))
    monkeypatch.setattr(cli.sys, "executable", "/usr/bin/python3")
    monkeypatch.delenv(cli._UPGRADE_SOURCE_ENV, raising=False)

    custom = "git+https://example.invalid/fork.git@main"
    rc = cli.main(["upgrade", "--dry-run", "--source", custom])
    out = capsys.readouterr().out
    assert rc == 0
    assert custom in out
    # Default source must NOT leak in.
    assert "github.com/v-songjun" not in out


def test_upgrade_env_var_overrides_default(data_dir, capsys, monkeypatch):
    """mindkeep_UPGRADE_SOURCE env var takes effect when no --source."""
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no run on dry-run")))
    monkeypatch.setattr(cli.sys, "executable", "/usr/bin/python3")
    monkeypatch.setenv(cli._UPGRADE_SOURCE_ENV,
                       "git+https://env.example/repo.git")

    rc = cli.main(["upgrade", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "env.example/repo.git" in out
    assert "github.com/v-songjun" not in out


def test_upgrade_detects_pipx_install_from_venv_path(
    data_dir, capsys, monkeypatch
):
    """sys.executable inside a pipx venvs directory → pipx command path."""
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no run on dry-run")))
    # Emulate a pipx layout on either OS.
    pipx_exe = "/home/u/.local/pipx/venvs/mindkeep/bin/python"
    monkeypatch.setattr(cli.sys, "executable", pipx_exe)
    monkeypatch.delenv(cli._UPGRADE_SOURCE_ENV, raising=False)

    # With the default (git) source pipx must use ``install --force``.
    rc = cli.main(["upgrade", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "install mode detected: pipx" in out
    assert "pipx install --force" in out
    assert "git+https://github.com/AllenS0104/mindkeep.git" in out

    # And with a PyPI source pipx should use ``upgrade <pkg>``.
    capsys.readouterr()
    rc2 = cli.main(["upgrade", "--dry-run", "--source", "pypi"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "pipx upgrade mindkeep" in out2



# ───────────────────────── doctor ─────────────────────────


def test_doctor_all_ok_in_normal_env(data_dir, capsys):
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "All checks" in out or "Some warnings" in out
    assert "Python version" in out
    assert "SQLite WAL" in out


def test_doctor_reports_failure_when_data_dir_not_writable(
    data_dir, capsys, monkeypatch
):
    from mindkeep import cli as cli_mod

    real_mkdir = Path.mkdir

    def boom(self, *a, **kw):
        if str(self).startswith(str(data_dir)):
            raise PermissionError("read-only")
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", boom)
    rc = cli_mod.main(["doctor"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "❌" in out
    assert "Data dir not writable" in out


def test_doctor_shows_current_project_id_and_display_name(
    data_dir, tmp_path, capsys
):
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    from mindkeep.project_id import resolve_project_id
    pid = resolve_project_id()
    assert pid.id in out
    assert pid.display_name in out



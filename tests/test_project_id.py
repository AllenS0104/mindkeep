"""Tests for ``mindkeep.project_id.resolve_project_id``.

Covers ARCHITECTURE.md §5 guarantees:
* https and ssh remotes collapse to the same id
* missing remote / non-git dir fall back to cwd_hash
* Windows path case is normalized
* missing ``git`` binary degrades gracefully
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mindkeep.project_id import (
    ProjectId,
    _hash12,
    _normalize_remote_url,
    resolve_project_id,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _git(*args: str, cwd: Path) -> None:
    """Run a git command, failing the test if git itself errors out."""
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path, remote: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    # Avoid global-config dependency for commits (not needed here, but cheap).
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "test", cwd=path)
    if remote is not None:
        _git("remote", "add", "origin", remote, cwd=path)


# --------------------------------------------------------------------------- #
# 1. git repo with https remote
# --------------------------------------------------------------------------- #

def test_https_remote_produces_git_remote_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo_https"
    _init_repo(repo, remote="https://github.com/Foo/Bar.git")

    pid = resolve_project_id(repo)

    assert pid.source == "git_remote"
    assert pid.display_name == "Bar"
    assert pid.origin == "github.com/Foo/Bar"
    assert len(pid.id) == 12
    assert all(c in "0123456789abcdef" for c in pid.id)


# --------------------------------------------------------------------------- #
# 2. ssh and https remotes collapse to the same id
# --------------------------------------------------------------------------- #

def test_ssh_and_https_remotes_match(tmp_path: Path) -> None:
    https_repo = tmp_path / "h"
    ssh_repo = tmp_path / "s"
    _init_repo(https_repo, remote="https://github.com/Foo/Bar.git")
    _init_repo(ssh_repo, remote="git@github.com:Foo/Bar.git")

    a = resolve_project_id(https_repo)
    b = resolve_project_id(ssh_repo)

    assert a.id == b.id
    assert a.origin == b.origin == "github.com/Foo/Bar"
    assert a.source == b.source == "git_remote"


def test_ssh_url_scheme_form_also_matches(tmp_path: Path) -> None:
    """The explicit ``ssh://git@host/owner/repo.git`` form should also collapse."""
    repo = tmp_path / "r"
    _init_repo(repo, remote="ssh://git@github.com/Foo/Bar.git")

    pid = resolve_project_id(repo)

    assert pid.origin == "github.com/Foo/Bar"
    assert pid.id == _hash12("github.com/Foo/Bar")


# --------------------------------------------------------------------------- #
# 3. git repo without remote falls back to cwd_hash
# --------------------------------------------------------------------------- #

def test_git_repo_without_remote_uses_cwd_hash(tmp_path: Path) -> None:
    repo = tmp_path / "noremote"
    _init_repo(repo, remote=None)

    pid = resolve_project_id(repo)

    assert pid.source == "cwd_hash"
    assert pid.display_name == "noremote"


# --------------------------------------------------------------------------- #
# 4. non-git directory → cwd_hash
# --------------------------------------------------------------------------- #

def test_non_git_directory_uses_cwd_hash(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    pid = resolve_project_id(plain)

    assert pid.source == "cwd_hash"
    assert pid.display_name == "plain"
    # id must be stable for the same path.
    assert resolve_project_id(plain).id == pid.id


# --------------------------------------------------------------------------- #
# 5. Windows path case normalization
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path casing")
def test_windows_path_case_insensitive(tmp_path: Path) -> None:
    plain = tmp_path / "CaseTest"
    plain.mkdir()

    lower = Path(str(plain).lower())
    upper = Path(str(plain).upper())

    # The directory itself must exist; we only vary the *string* given to
    # resolve_project_id. ``.resolve()`` will canonicalize both to the same
    # on-disk path on Windows, and our origin normalization lowercases it.
    a = resolve_project_id(lower)
    b = resolve_project_id(upper)

    assert a.id == b.id
    assert a.source == "cwd_hash"


def test_cwd_origin_is_forward_slashed(tmp_path: Path) -> None:
    plain = tmp_path / "fwd"
    plain.mkdir()
    pid = resolve_project_id(plain)
    assert "\\" not in pid.origin


# --------------------------------------------------------------------------- #
# 6. git binary missing → silent fallback
# --------------------------------------------------------------------------- #

def test_missing_git_binary_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, remote="https://github.com/Foo/Bar.git")

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise FileNotFoundError("git")

    # Patch subprocess.run as seen by project_id module.
    import mindkeep.project_id as mod

    monkeypatch.setattr(mod.subprocess, "run", _boom)

    pid = resolve_project_id(repo)

    assert pid.source == "cwd_hash"
    assert pid.display_name == "repo"


def test_git_timeout_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo_t"
    _init_repo(repo, remote="https://github.com/Foo/Bar.git")

    def _slow(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise subprocess.TimeoutExpired(cmd="git", timeout=3)

    import mindkeep.project_id as mod

    monkeypatch.setattr(mod.subprocess, "run", _slow)

    pid = resolve_project_id(repo)
    assert pid.source == "cwd_hash"


# --------------------------------------------------------------------------- #
# Direct unit tests for the URL normalizer
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/Foo/Bar.git", "github.com/Foo/Bar"),
        ("https://github.com/Foo/Bar", "github.com/Foo/Bar"),
        ("http://GitHub.com/Foo/Bar.git", "github.com/Foo/Bar"),
        ("git@github.com:Foo/Bar.git", "github.com/Foo/Bar"),
        ("ssh://git@github.com/Foo/Bar.git", "github.com/Foo/Bar"),
        ("https://user:pass@github.com/Foo/Bar.git", "github.com/Foo/Bar"),
        ("https://github.com:443/Foo/Bar.git", "github.com/Foo/Bar"),
        ("https://github.com/Foo/Bar.git?x=1#frag", "github.com/Foo/Bar"),
        ("git://github.com/Foo/Bar.git", "github.com/Foo/Bar"),
    ],
)
def test_normalize_remote_url(url: str, expected: str) -> None:
    assert _normalize_remote_url(url) == expected


def test_projectid_is_frozen() -> None:
    pid = ProjectId(id="a" * 12, display_name="x", source="cwd_hash", origin="x")
    with pytest.raises(Exception):
        pid.id = "b" * 12  # type: ignore[misc]

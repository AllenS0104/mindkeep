"""Tests for `mindkeep integrate <target>` (P1-5, #10)."""

from __future__ import annotations

import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from mindkeep import _integrations
from mindkeep.cli import main as cli_main


TARGETS = ["claude", "copilot", "cursor", "generic"]
TARGET_KEYWORDS = {
    "claude": "Claude",
    "copilot": "Copilot",
    "cursor": "Cursor",
    "generic": "vendor-neutral",
}


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli_main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def test_list_lists_four_targets() -> None:
    rc, out, _ = _run("integrate", "--list")
    assert rc == 0
    listed = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    assert listed == TARGETS


@pytest.mark.parametrize("target", TARGETS)
def test_target_snippet_has_required_content(target: str) -> None:
    rc, out, _ = _run("integrate", target)
    assert rc == 0
    assert TARGET_KEYWORDS[target].lower() in out.lower() or target.lower() in out.lower()
    assert "mindkeep recall" in out
    assert "mindkeep show" in out
    assert ("add_fact" in out) or ("add_adr" in out)


@pytest.mark.parametrize("target", TARGETS)
def test_snippet_no_placeholders(target: str) -> None:
    snippet = _integrations.render(target)
    for bad in ("TODO", "PLACEHOLDER", "XXX", "FIXME"):
        assert bad not in snippet, f"{target} snippet contains {bad}"


@pytest.mark.parametrize("target", TARGETS)
def test_snippet_valid_utf8(target: str) -> None:
    snippet = _integrations.render(target)
    encoded = snippet.encode("utf-8")
    assert encoded.decode("utf-8") == snippet
    # No surrogate code points.
    assert not re.search(r"[\ud800-\udfff]", snippet)


def test_unknown_target_exits_2() -> None:
    rc, _, err = _run("integrate", "nonsuch")
    assert rc == 2
    assert "supported targets: claude, copilot, cursor, generic" in err


def test_no_target_and_no_list_exits_2() -> None:
    rc, _, err = _run("integrate")
    assert rc == 2
    assert "supported targets" in err


def test_out_writes_file_with_same_content(tmp_path: Path) -> None:
    target = "claude"
    rc_stdout, stdout, _ = _run("integrate", target)
    assert rc_stdout == 0

    dest = tmp_path / "nested" / "claude.md"
    rc, out, _ = _run("integrate", target, "--out", str(dest))
    assert rc == 0
    assert out == ""
    assert dest.read_text(encoding="utf-8") == stdout


def test_out_refuses_overwrite_without_force(tmp_path: Path) -> None:
    dest = tmp_path / "exists.md"
    dest.write_text("OLD CONTENT", encoding="utf-8")

    rc, _, err = _run("integrate", "generic", "--out", str(dest))
    assert rc == 1
    assert "refusing to overwrite" in err
    assert dest.read_text(encoding="utf-8") == "OLD CONTENT"


def test_out_force_overwrites(tmp_path: Path) -> None:
    dest = tmp_path / "exists.md"
    dest.write_text("OLD", encoding="utf-8")

    rc, _, _ = _run("integrate", "generic", "--out", str(dest), "--force")
    assert rc == 0
    assert "OLD" not in dest.read_text(encoding="utf-8")
    assert "mindkeep recall" in dest.read_text(encoding="utf-8")

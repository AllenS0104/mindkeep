"""Tests for `mindkeep integrate <target>` (P1-5, #10; #37 MCP targets)."""

from __future__ import annotations

import io
import json
import os
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from mindkeep import _integrations
from mindkeep.cli import main as cli_main


TARGETS = ["claude", "copilot", "cursor", "generic"]
MCP_TARGETS = ["claude-desktop", "cursor-mcp", "continue-mcp"]
ALL_TARGETS = TARGETS + MCP_TARGETS
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


def test_list_lists_all_targets() -> None:
    rc, out, _ = _run("integrate", "--list")
    assert rc == 0
    listed = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    assert listed == ALL_TARGETS


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
    # Error message lists every supported target (markdown + MCP).
    for name in ALL_TARGETS:
        assert name in err


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


# ───────────────────────── MCP target tests (#37) ─────────────────────────


@pytest.fixture
def project_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp dir that looks like a mindkeep project (has .mindkeep/), set as cwd.

    Avoids the soft "no .mindkeep/ directory" stderr warning so tests can
    assert on otherwise-empty stderr without false positives.
    """
    (tmp_path / ".mindkeep").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path.resolve()


def test_claude_desktop_stdout_default(project_cwd: Path) -> None:
    rc, out, err = _run("integrate", "claude-desktop")
    assert rc == 0
    payload = json.loads(out)
    server = payload["mcpServers"]["mindkeep"]
    assert server["command"] == "mindkeep-mcp"
    assert server["args"] == []
    assert server["env"]["MINDKEEP_PROJECT_DIR"] == str(project_cwd)
    # Read-only by default — no --allow-writes anywhere in the snippet.
    assert "--allow-writes" not in out
    # The post-print stderr hint mentions the opt-in path.
    assert "--allow-writes" in err


def test_cursor_mcp_stdout_default(project_cwd: Path) -> None:
    rc, out, _ = _run("integrate", "cursor-mcp")
    assert rc == 0
    payload = json.loads(out)
    assert payload["mcpServers"]["mindkeep"]["command"] == "mindkeep-mcp"
    assert (
        payload["mcpServers"]["mindkeep"]["env"]["MINDKEEP_PROJECT_DIR"]
        == str(project_cwd)
    )


def test_continue_mcp_stdout_default(project_cwd: Path) -> None:
    rc, out, _ = _run("integrate", "continue-mcp")
    assert rc == 0
    payload = json.loads(out)
    server = payload["experimental"]["modelContextProtocolServers"][0]
    assert server["transport"]["command"] == "mindkeep-mcp"
    assert server["transport"]["args"] == ["--project-dir", str(project_cwd)]
    assert "--allow-writes" not in out


def test_continue_mcp_ignores_out(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    dest.write_text("{}", encoding="utf-8")
    rc, out, err = _run("integrate", "continue-mcp", "--out", str(dest))
    assert rc == 0
    # Snippet still printed to stdout.
    assert json.loads(out)["experimental"]
    # File untouched.
    assert dest.read_text(encoding="utf-8") == "{}"
    assert "does not support in-place merge" in err


def test_project_dir_flag_overrides_cwd(
    project_cwd: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    rc, out, _ = _run(
        "integrate", "claude-desktop", "--project-dir", str(other)
    )
    assert rc == 0
    payload = json.loads(out)
    assert (
        payload["mcpServers"]["mindkeep"]["env"]["MINDKEEP_PROJECT_DIR"]
        == str(other.resolve())
    )


def test_non_project_cwd_warns_but_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rc, out, err = _run("integrate", "claude-desktop")
    assert rc == 0
    payload = json.loads(out)
    assert (
        payload["mcpServers"]["mindkeep"]["env"]["MINDKEEP_PROJECT_DIR"]
        == str(tmp_path.resolve())
    )
    assert "no .mindkeep/" in err


def test_mcp_out_writes_empty_file(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "claude_desktop_config.json"
    rc, out, err = _run("integrate", "claude-desktop", "--out", str(dest))
    assert rc == 0
    assert out == ""
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert list(payload.keys()) == ["mcpServers"]
    assert payload["mcpServers"]["mindkeep"]["command"] == "mindkeep-mcp"
    # No backup expected — the file didn't exist before.
    assert not (tmp_path / "claude_desktop_config.json.bak").exists()
    assert "wrote" in err


def test_mcp_out_preserves_unrelated_keys(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "claude_desktop_config.json"
    existing = {
        "someOtherSetting": 1,
        "theme": "dark",
        "mcpServers": {
            "other-server": {
                "command": "other-mcp",
                "args": ["--flag"],
            }
        },
    }
    dest.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    rc, _, _ = _run("integrate", "claude-desktop", "--out", str(dest))
    assert rc == 0

    merged = json.loads(dest.read_text(encoding="utf-8"))
    assert merged["someOtherSetting"] == 1
    assert merged["theme"] == "dark"
    assert merged["mcpServers"]["other-server"]["command"] == "other-mcp"
    assert (
        merged["mcpServers"]["mindkeep"]["env"]["MINDKEEP_PROJECT_DIR"]
        == str(project_cwd)
    )

    # Backup should contain the original content.
    bak = dest.with_name(dest.name + ".bak")
    assert bak.exists()
    assert json.loads(bak.read_text(encoding="utf-8")) == existing


def test_mcp_out_refuses_existing_mindkeep_entry(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    existing = {
        "mcpServers": {
            "mindkeep": {"command": "old-mindkeep", "args": []},
            "other": {"command": "other"},
        }
    }
    dest.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    original_text = dest.read_text(encoding="utf-8")

    rc, _, err = _run("integrate", "claude-desktop", "--out", str(dest))
    assert rc == 2
    assert "already exists" in err
    # File untouched.
    assert dest.read_text(encoding="utf-8") == original_text
    # No backup written on the refusal path.
    assert not dest.with_name(dest.name + ".bak").exists()


def test_mcp_out_force_overwrites_mindkeep_entry(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    existing = {
        "mcpServers": {
            "mindkeep": {"command": "old-mindkeep", "args": ["--legacy"]},
            "other": {"command": "other"},
        }
    }
    dest.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    rc, _, _ = _run(
        "integrate", "claude-desktop", "--out", str(dest), "--force"
    )
    assert rc == 0

    merged = json.loads(dest.read_text(encoding="utf-8"))
    # Mindkeep entry was overwritten (no more "--legacy").
    assert merged["mcpServers"]["mindkeep"]["command"] == "mindkeep-mcp"
    assert merged["mcpServers"]["mindkeep"]["args"] == []
    # Other server preserved.
    assert merged["mcpServers"]["other"]["command"] == "other"
    # Backup contains the previous mindkeep entry.
    bak = dest.with_name(dest.name + ".bak")
    assert bak.exists()
    bak_data = json.loads(bak.read_text(encoding="utf-8"))
    assert bak_data["mcpServers"]["mindkeep"]["command"] == "old-mindkeep"


def test_mcp_out_dry_run_does_not_write(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    existing = {"mcpServers": {"other": {"command": "other"}}}
    dest.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    original = dest.read_text(encoding="utf-8")

    rc, out, err = _run(
        "integrate", "claude-desktop", "--out", str(dest), "--dry-run"
    )
    assert rc == 0
    # Disk unchanged.
    assert dest.read_text(encoding="utf-8") == original
    assert not dest.with_name(dest.name + ".bak").exists()
    # Stdout shows the would-be merged content.
    payload = json.loads(out)
    assert payload["mcpServers"]["mindkeep"]["command"] == "mindkeep-mcp"
    assert payload["mcpServers"]["other"]["command"] == "other"
    assert "dry-run" in err


def test_mcp_out_jsonc_refused(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    dest.write_text(
        '{\n  "x": 1, // a JSONC comment\n  "mcpServers": {}\n}\n',
        encoding="utf-8",
    )
    original = dest.read_text(encoding="utf-8")

    rc, _, err = _run("integrate", "claude-desktop", "--out", str(dest))
    assert rc == 2
    assert "JSONC" in err
    # File untouched.
    assert dest.read_text(encoding="utf-8") == original


def test_mcp_out_block_comment_refused(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    dest.write_text(
        '/* header */\n{\n  "mcpServers": {}\n}\n', encoding="utf-8"
    )
    rc, _, err = _run("integrate", "claude-desktop", "--out", str(dest))
    assert rc == 2
    assert "JSONC" in err


def test_mcp_out_invalid_json_refused(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "config.json"
    dest.write_text("{ this is not json", encoding="utf-8")
    rc, _, err = _run("integrate", "claude-desktop", "--out", str(dest))
    assert rc == 2
    assert "not valid JSON" in err


def test_cursor_mcp_out_happy_path(
    project_cwd: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "mcp.json"
    rc, _, _ = _run("integrate", "cursor-mcp", "--out", str(dest))
    assert rc == 0
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["mindkeep"]["command"] == "mindkeep-mcp"


def test_cursor_mcp_out_force(project_cwd: Path, tmp_path: Path) -> None:
    dest = tmp_path / "mcp.json"
    dest.write_text(
        json.dumps({"mcpServers": {"mindkeep": {"command": "old"}}}),
        encoding="utf-8",
    )
    # No --force → refuse.
    rc, _, _ = _run("integrate", "cursor-mcp", "--out", str(dest))
    assert rc == 2
    # With --force → overwrite.
    rc, _, _ = _run("integrate", "cursor-mcp", "--out", str(dest), "--force")
    assert rc == 0
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["mindkeep"]["command"] == "mindkeep-mcp"


def test_force_without_out_warns(project_cwd: Path) -> None:
    rc, _, err = _run("integrate", "claude-desktop", "--force")
    assert rc == 0
    assert "--force has no effect" in err


def test_existing_markdown_targets_unchanged_regression() -> None:
    """Adding MCP targets must not regress markdown snippets."""
    for t in TARGETS:
        rc, out, _ = _run("integrate", t)
        assert rc == 0
        assert "mindkeep recall" in out
        assert "mindkeep show" in out
        # No JSON contamination.
        assert '"mcpServers"' not in out


# ──────────────────────── unit tests for helpers ────────────────────────


def test_mcp_snippet_shape_claude_desktop() -> None:
    snippet = _integrations.mcp_snippet("claude-desktop", "/p")
    assert snippet == {
        "mcpServers": {
            "mindkeep": {
                "command": "mindkeep-mcp",
                "args": [],
                "env": {"MINDKEEP_PROJECT_DIR": "/p"},
            }
        }
    }


def test_mcp_snippet_shape_continue() -> None:
    snippet = _integrations.mcp_snippet("continue-mcp", "/p")
    server = snippet["experimental"]["modelContextProtocolServers"][0]
    assert server["transport"]["command"] == "mindkeep-mcp"
    assert server["transport"]["args"] == ["--project-dir", "/p"]


def test_jsonc_detector_ignores_strings_with_double_slash() -> None:
    raw = '{"url": "https://example.com"}'
    assert not _integrations._looks_like_jsonc(raw)


def test_jsonc_detector_catches_line_comment() -> None:
    raw = '{ // hi\n}'
    assert _integrations._looks_like_jsonc(raw)


def test_default_config_path_known_targets() -> None:
    for t in MCP_TARGETS:
        p = _integrations.default_config_path(t)
        assert isinstance(p, Path)
        assert p.is_absolute()

"""CLI runner for the mindkeep eval suite (P2-10, #15).

Usage:
    python -m mindkeep.evals [--report PATH] [--quiet]

Builds an isolated tmp DB, runs all scenarios, writes ``evals/report.json``
(by default in CWD) and prints a markdown summary. Exits non-zero if any
scenario fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .scenarios import EvalResult, run_all

REPORT_VERSION = 1


def build_report(results: list[EvalResult]) -> dict[str, Any]:
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    return {
        "version": REPORT_VERSION,
        "scenarios": [r.to_dict() for r in results],
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines: list[str] = []
    lines.append("# mindkeep eval report")
    lines.append("")
    lines.append(
        f"**Scenarios:** {summary['total']}  "
        f"**Passed:** {summary['passed']}  "
        f"**Failed:** {summary['failed']}"
    )
    lines.append("")
    lines.append("| Scenario | Metric | Threshold | Result |")
    lines.append("|---|---|---|---|")
    for sc in report["scenarios"]:
        glyph = "✅" if sc["passed"] else "❌"
        lines.append(
            f"| {sc['name']} | {sc['metric']} | {sc['threshold']} | {glyph} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mindkeep.evals",
        description="Run mindkeep evaluation scenarios (P2-10, #15).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("evals") / "report.json",
        help="Output path for the JSON report (default: evals/report.json).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress markdown summary output.",
    )
    args = parser.parse_args(argv)

    results = run_all()
    report = build_report(results)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = render_markdown(report)
    if not args.quiet:
        print(md)
        print(f"Report written to: {args.report}")

    return 0 if report["summary"]["failed"] == 0 else 1


__all__ = ["main", "build_report", "render_markdown", "REPORT_VERSION"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

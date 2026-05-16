# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI entry point -- wires all forge modules into a pipeline.

Usage:
    forge [diff_spec]           # default: HEAD
    forge --staged              # compare index to HEAD
    forge --registry PATH       # custom tools.yaml
    forge --state-dir PATH      # custom state directory
    forge --quiet               # suppress skipped/version info
    forge --version             # print version

Addresses:
- Consensus #1: uses run_git_diff from forge.git (single owner)
- Consensus #6: uses EXIT_PASS/EXIT_FAIL from forge.__init__
- Round 3 B-1: no list mutation while iterating
- Round 3 C-1: format_report with tools_failed parameter
- Round 5 R5-M3: stderr propagation to ToolError
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from forge import EXIT_PASS, EXIT_FAIL, __version__
from forge.delta import filter_delta
from forge.diff import extract_changed_lines, get_changed_files
from forge.git import run_git_diff
from forge.parsers import parse_output
from forge.parsers.base import ToolError
from forge.registry import load_registry
from forge.reporter import format_report
from forge.runner import run_tools
from forge.state import write_state
from forge.verdict import determine_verdict


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="forge",
        description="3-state quality gate for code review",
    )
    parser.add_argument(
        "diff_spec",
        nargs="?",
        default="HEAD",
        help="git diff spec (default: HEAD)",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="check staged changes (shortcut for diff_spec=--staged)",
    )
    parser.add_argument(
        "--registry",
        default=".forge/tools.yaml",
        help="path to tools.yaml (default: .forge/tools.yaml)",
    )
    parser.add_argument(
        "--state-dir",
        default=".forge",
        help="directory for state.json (default: .forge)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress tool-skipped and version messages",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="forge %s" % __version__,
    )
    return parser


def main() -> None:
    """Entry point for the forge CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    # Resolve diff_spec
    diff_spec = "--staged" if args.staged else args.diff_spec

    # a. Load registry
    try:
        registry = load_registry(args.registry)
    except FileNotFoundError:
        print(
            "forge: error: registry not found: %s" % args.registry,
            file=sys.stderr,
        )
        sys.exit(EXIT_FAIL)
    except ValueError as exc:
        print("forge: error: %s" % exc, file=sys.stderr)
        sys.exit(EXIT_FAIL)

    # b. Get diff text via run_git_diff (Consensus #1)
    try:
        diff_text = run_git_diff(diff_spec)
    except RuntimeError as exc:
        print("forge: error: %s" % exc, file=sys.stderr)
        sys.exit(EXIT_FAIL)
    except ValueError as exc:
        print("forge: error: %s" % exc, file=sys.stderr)
        sys.exit(EXIT_FAIL)

    # c. Parse diff
    changed_lines = extract_changed_lines(diff_text)
    changed_files = get_changed_files(diff_text)

    state_path = os.path.join(args.state_dir, "state.json")

    # d. No changes -> PASS
    if not changed_files:
        print("forge: PASS -- no changes detected")
        write_state(state_path, {
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diff_spec": diff_spec,
            "verdict": "PASS",
            "exit_code": EXIT_PASS,
            "tools_run": [],
            "tools_skipped": [],
            "tools_failed": [],
            "tool_versions": {},
            "delta_findings": [],
            "all_findings_count": 0,
            "delta_findings_count": 0,
            "summary": "PASS: no changes detected",
        })
        sys.exit(EXIT_PASS)

    # e. Run tools
    tool_results, tool_versions, tools_skipped = run_tools(
        registry, changed_files
    )

    # f. Parse tool outputs
    all_findings = []
    for tool_name, (stdout, returncode, stderr) in tool_results.items():
        output_format = registry[tool_name].output_format
        try:
            parsed = parse_output(
                stdout, output_format, tool_name, exit_code=returncode
            )
        except KeyError:
            parsed = [
                ToolError(
                    tool_name=tool_name,
                    exit_code=returncode,
                    stderr=stderr,
                    message=(
                        "Unknown output_format '%s' for tool '%s'."
                        " Check tools.yaml." % (output_format, tool_name)
                    ),
                )
            ]

        # R5-M3: propagate actual stderr to ToolError objects
        for i, item in enumerate(parsed):
            if isinstance(item, ToolError) and stderr:
                parsed[i] = ToolError(
                    tool_name=item.tool_name,
                    exit_code=item.exit_code,
                    stderr=stderr,
                    message=item.message,
                )

        all_findings.extend(parsed)

    # g. Distinguish required vs optional tool crashes (Round 3 B-1)
    # Build two new lists in a SINGLE pass -- no list mutation
    filtered_findings = []
    tools_failed_set: set[str] = set()
    for item in all_findings:
        if isinstance(item, ToolError):
            tool_cfg = registry.get(item.tool_name)
            if tool_cfg is not None and tool_cfg.required:
                # Required tool crash: keep ToolError, will cause FAIL
                filtered_findings.append(item)
            else:
                # Optional tool crash: track for visibility
                tools_failed_set.add(item.tool_name)
        else:
            filtered_findings.append(item)
    tools_failed = sorted(tools_failed_set)
    all_findings = filtered_findings

    # h. Delta filter
    delta_findings, all_findings_preserved = filter_delta(
        all_findings, changed_lines
    )

    # i. Verdict
    verdict_str, exit_code = determine_verdict(delta_findings)

    # j. Format and print report
    report_versions = {} if args.quiet else tool_versions
    report_skipped = [] if args.quiet else tools_skipped
    report = format_report(
        delta_findings,
        all_findings_preserved,
        report_versions,
        report_skipped,
        tools_failed,  # always passed, never suppressed by --quiet
    )
    print(report)

    # k. Write state
    tools_run = sorted(tool_results.keys())
    write_state(state_path, {
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "diff_spec": diff_spec,
        "verdict": verdict_str,
        "exit_code": exit_code,
        "tools_run": tools_run,
        "tools_skipped": tools_skipped,
        "tools_failed": tools_failed,
        "tool_versions": tool_versions,
        "delta_findings": [item.to_dict() for item in delta_findings],
        "all_findings_count": len(all_findings_preserved),
        "delta_findings_count": len(delta_findings),
        "summary": "%s: %d new / %d pre-existing / %d tools / %d failed"
        % (
            verdict_str,
            len(delta_findings),
            len(all_findings_preserved) - len(delta_findings),
            len(tools_run),
            len(tools_failed),
        ),
    })

    # l. Exit
    sys.exit(exit_code)

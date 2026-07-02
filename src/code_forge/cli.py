# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge CLI entry point.

Subcommands: review (default), gate-check, mutation-check, e2e-check,
install-hooks, install-skill, verify, detect, resolve-outlet, init.
Bare invocation (no subcommand) routes to review for backward compatibility.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from . import __version__
from .runner import capture_tool_version
from .sarif import build_sarif_log, format_summary

if TYPE_CHECKING:
    from .backend import BackendConfig
    from .registry import ToolConfig
from .baseline import (
    EmptyBaseline,
    GitRefBaseline,
    SnapshotBaseline,
    resolve_baseline,
    serialize_baseline_spec,
)
from .env_resolver import (
    resolve_falsification_engine,
    resolve_max_fix_attempts,
    resolve_max_total_rounds,
)
from .errors import BaselineResolutionError, CliError, CoverageConfigError
from .exit_codes import (
    EXIT_BUSY,
    EXIT_CLI_ERROR,
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_TIMEOUT,
    verdict_to_exit,
)
from .factories import build_autofixer, build_falsifier, build_l1_provider, build_revert_fn
from .git import is_git_repo
from .hold import HoldAborted, run_hold_ui
from .lock import ForgeLock, ForgeLockBusy
from .machine import StateMachine, TimeoutBreaker
from .mode_resolver import resolve_mode
from .registry import load_registry
from .source import compute_source_hash
from .state import Mode, Verdict, load_state as _load_state


MAX_HOLD_CYCLES = 10


def _emit_ci_output(
    state_path: Path,
    registry: dict[str, "ToolConfig"],
    post_emit_hook: Optional[Callable[[], None]] = None,
    backend_name: Optional[str] = None,
    backend_model: Optional[str] = None,
) -> None:
    """Emit SARIF to stdout and summary to stderr in CI mode.

    Re-loads state from disk for canonical view (catches any save_state
    divergence). Re-captures tool_versions to avoid constructor-time
    snapshot staleness.

    When backend_name is provided (api backends), the SARIF output
    includes a tokenCost property bag in runs[0].properties.

    If load_state returns None -> silent return (no log warning).
    SARIF is best-effort output, NOT canonical artifact; state.json is
    canonical. Silent return matches "skip SARIF when state absent" semantics.
    """
    final_state = _load_state(state_path)
    if final_state is None:
        return
    tool_versions = {
        name: capture_tool_version(tc.command)
        for name, tc in registry.items()
    }
    log_dict = build_sarif_log(
        final_state, tool_versions, forge_version=__version__,
        backend_name=backend_name, backend_model=backend_model,
    )
    print(json.dumps(log_dict), file=sys.stdout)
    print(format_summary(final_state), file=sys.stderr)
    if post_emit_hook is not None:
        post_emit_hook()


def _load_gate_backends(gate_yaml_path: Path) -> tuple[list, dict]:
    """Load backend configs from gate.yaml.

    Returns (backend_configs, gate_data) where gate_data is the full
    parsed YAML dict (empty dict if file absent/invalid/untrusted).

    Trust guard: refuses to return backend configs for untrusted
    repos. When gate.yaml exists but its backends block hash does not match
    the stored trust record in ~/.config/code-forge/trusted.json, returns
    ([], {}) and prints a warning to stderr. The user must run
    ``code-forge trust`` to explicitly authorize the backends.

    Raises:
        CliError: gate.yaml exists but is corrupt or has invalid backend config.
    """
    import yaml as _y
    from .backend import load_backend_configs


    try:
        with open(gate_yaml_path, "r", encoding="utf-8") as _f:
            gd = _y.safe_load(_f)
    except FileNotFoundError:
        return ([], {})
    except _y.YAMLError as exc:
        raise CliError(
            "gate.yaml parse error: %s" % exc,
            remediation="Check gate.yaml syntax. Run 'code-forge init --force' to regenerate.",
        ) from exc

    if gd is None or not isinstance(gd, dict):
        return ([], {})

    # Trust guard: check trust before loading backends.
    from .trust import is_trusted
    if not is_trusted(gate_yaml_path, gd):
        print(
            "Untrusted repo backends ignored. "
            "Run 'code-forge trust' to enable.",
            file=sys.stderr,
        )
        return ([], {})

    return (load_backend_configs(gd), gd)


def _load_canary_config(args: argparse.Namespace, gate_data: dict) -> dict | None:
    """Extract canary opt-in config from CLI flags or gate_data.

    Uses the gate_data dict already loaded and trust-checked by
    _load_gate_backends -- never re-reads gate.yaml from disk.

    Returns a config dict with keys (enabled, n, threshold_ratio) when
    opted in, or None when the canary check is not requested.
    """
    if getattr(args, "canary", False):
        return {"enabled": True, "n": 5, "threshold_ratio": 0.6}
    canary_section = gate_data.get("canary")
    if isinstance(canary_section, dict) and canary_section.get("enabled") is True:
        return {
            "enabled": True,
            "n": canary_section.get("n", 5),
            "threshold_ratio": canary_section.get("threshold_ratio", 0.6),
        }
    return None


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with subcommands.

    Subcommands:
      - review: existing pipeline (all flags preserved)
      - gate-check: test-based commit gate
      - mutation-check: mutation testing gate
      - e2e-check: cross-component coverage heuristic
      - install-hooks: hook installer
      - install-skill: install bundled skill
      - verify: validate review receipts
      - detect: auto-detect toolchain, write tools.yaml
      - resolve-outlet: resolve review outlet (subprocess/inline/subagent)
      - init: generate a gate.yaml template in .code-forge/

    Backward compat: bare `forge` (no subcommand) defaults to `review`
    in main() for existing workflows.

    --help includes an Exit Codes section in the epilog.
    """
    parser = argparse.ArgumentParser(
        prog="code-forge",
        description="3-state quality gate for code review",
        epilog=(
            "Exit codes:\n"
            "  0  PASS\n"
            "  1  FAIL\n"
            "  2  CLI_ERROR (invalid args, missing config, "
            "parse error)\n"
            "  3  BUSY (another code-forge process holds the lock)\n"
            "  4  ESCALATED (non-convergence or human-frozen)\n"
            "  5  DELEGATED (review delegated to session)\n"
            "  6  TIMEOUT (backend timeout circuit breaker)\n"
            "  7  UNRELIABLE (canary miss on inline outlet)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # --version on root parser so `forge --version` works
    parser.add_argument(
        "--version", action="version",
        version="code-forge %s" % __version__,
    )

    # Subparsers: dest='subcommand' to capture which was invoked
    # required=False (Python 3.7+ default) for backward compat
    subparsers = parser.add_subparsers(
        dest='subcommand',
        help='subcommand to execute',
    )

    # --- REVIEW subcommand: existing pipeline ---
    review_parser = subparsers.add_parser(
        'review',
        help='run the full review pipeline (default)',
        description='3-state quality gate for code review',
        epilog=(
            "Exit codes:\n"
            "  0  PASS\n"
            "  1  FAIL\n"
            "  2  CLI_ERROR (invalid args, missing config, "
            "parse error)\n"
            "  3  BUSY (another code-forge process holds the lock)\n"
            "  4  ESCALATED (non-convergence or human-frozen)\n"
            "  5  DELEGATED (review delegated to session)\n"
            "  6  TIMEOUT (backend timeout circuit breaker)\n"
            "  7  UNRELIABLE (canary miss on inline outlet)\n"
            "\n"
            "Cycle count adapts to diff size: <50 lines = 2 cycles,\n"
            "50-199 = 3 (default), >=200 = 4. Override with\n"
            "FORGE_CLEAN_ROUND_THRESHOLD=N.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    review_parser.add_argument(
        "--mode", choices=["local", "ci"], default=None,
        help="execution mode (default: local if TTY, ci otherwise)",
    )
    review_parser.add_argument(
        "--falsification-engine", choices=["auto", "stub", "real"],
        default=None,
        help="falsification engine (default: auto)",
    )
    review_parser.add_argument(
        "--sandbox", action="store_true",
        help="enable sandbox for autofixer "
             "(Phase 4 hook; v2.0 no-op + warning)",
    )
    review_parser.add_argument(
        "--baseline", default=None,
        help="baseline ref "
             "(HEAD/INDEX/<sha>/empty/<snapshot-path>; "
             "empty reviews whole file in any repo)",
    )
    review_parser.add_argument(
        "--head", default=None,
        help="head ref (git only: WORKING/INDEX/<sha>; "
             "ignored non-git)",
    )
    review_parser.add_argument(
        "--registry", default=".code-forge/tools.yaml",
        help="path to tools.yaml (default: .code-forge/tools.yaml)",
    )
    review_parser.add_argument(
        "--max-total-rounds", type=int, default=None,
        help="LOCAL mode round bound "
             "(default 20 or FORGE_MAX_TOTAL_ROUNDS)",
    )
    review_parser.add_argument(
        "--max-fix-attempts", type=int, default=None,
        help="per-fingerprint fix budget "
             "(default 3 or "
             "FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT)",
    )
    review_parser.add_argument(
        "--quiet", action="store_true",
        help="suppress tool-skipped, version, and deprecation "
             "messages",
    )
    review_parser.add_argument(
        "--outlet", choices=["subprocess", "cli", "inline", "subagent", "sampling"], default=None,
        help="review outlet (default: auto-detect via backend reachability)",
    )
    review_parser.add_argument(
        "--committed", action="store_true",
        help="review the last commit (maps to --baseline HEAD~1 --head HEAD)",
    )
    review_parser.add_argument(
        "--canary", action="store_true",
        help="enable canary laziness check for inline outlet (opt-in)",
    )
    review_parser.add_argument(
        "--contract", default=None, metavar="FILE",
        help="path to per-change intent contract (use - for stdin); "
             "state invariants-to-verify and residual risks, "
             "NOT 'this code is correct'",
    )

    # Backend selection flags
    review_parser.add_argument(
        "--backend", default=None, metavar="NAME",
        help="named backend from gate.yaml backends block "
             "(mutually exclusive with inline backend flags)",
    )
    backend_inline = review_parser.add_argument_group(
        "inline backend flags",
        "Define a transient backend without gate.yaml "
        "(all 4 required together; mutually exclusive with --backend)",
    )
    backend_inline.add_argument(
        "--backend-url", default=None, metavar="URL",
        help="base URL for inline backend (e.g. https://api.deepseek.com/v1)",
    )
    backend_inline.add_argument(
        "--backend-format", default=None,
        choices=["openai", "anthropic", "vertex"],
        help="API format for inline backend",
    )
    backend_inline.add_argument(
        "--backend-key-env", default=None, metavar="VAR_NAME",
        help="env var name holding the API key for inline backend",
    )
    backend_inline.add_argument(
        "--backend-model", default=None, metavar="MODEL_NAME",
        help="model name for inline backend",
    )

    review_parser.add_argument(
        "--whole-file", nargs="+", metavar="PATH",
        help="review specific file(s) in full without baseline comparison; "
             "paths must be relative and resolve under the repo root",
    )
    review_parser.add_argument(
        "--no-color", action="store_true", default=False,
        help="suppress ANSI color codes in output",
    )
    review_parser.add_argument(
        "--allow-main", action="store_true", default=False,
        help="allow review in main worktree (bypass worktree guard)",
    )
    review_parser.add_argument(
        "paths", nargs="*",
        help="files/dirs to review; git mode filters diff, "
             "non-git lists files",
    )

    # --- GATE-CHECK subcommand: test-based commit gate ---
    gate_parser = subparsers.add_parser(
        'gate-check',
        help='run test gate for pre-commit hook',
        description='Test-based commit gate (blocks on new failures)',
    )
    gate_parser.add_argument(
        "--quiet", action="store_true",
        help="suppress warning messages",
    )
    gate_parser.add_argument(
        "--no-color", action="store_true", default=False,
        help="suppress ANSI color codes in output",
    )
    gate_parser.add_argument(
        "--baseline", type=str, default=None,
        help="baseline ref for delta comparison",
    )
    gate_parser.add_argument(
        "--backend", default=None, metavar="NAME",
        help="named backend from gate.yaml backends block",
    )

    # --- MUTATION-CHECK subcommand: mutation testing gate ---
    mutation_parser = subparsers.add_parser(
        'mutation-check',
        help='run mutation testing gate',
        description=(
            'Mutation testing gate: runs mutmut on diff-scoped files '
            'and reports surviving mutants. '
            'Exit codes: 0=PASS, 1=FAIL (survivors found), 2=CLI_ERROR.'
        ),
    )
    mutation_parser.add_argument(
        "--diff", default=None,
        help="path to unified diff file (default: uncommitted changes)",
    )
    mutation_parser.add_argument(
        "--timeout", type=int, default=600,
        help="mutmut run timeout in seconds (default: 600)",
    )
    mutation_parser.add_argument(
        "--paths", default=None,
        help="glob pattern to restrict mutation to matching files",
    )

    # --- E2E-CHECK subcommand: cross-component coverage heuristic ---
    e2e_parser = subparsers.add_parser(
        'e2e-check',
        help='run cross-component e2e coverage heuristic',
        description=(
            'E2E coverage heuristic: detects cross-component signature '
            'changes and checks for e2e artifacts. '
            'Exit codes: 0=PASS (no findings or skip), 1=FAIL (P2 findings), '
            '2=CLI_ERROR.'
        ),
    )
    e2e_parser.add_argument(
        "--diff", default=None,
        help="path to unified diff file (default: uncommitted changes)",
    )
    e2e_parser.add_argument(
        "--repo-root", default=None,
        help="repository root path (default: current directory)",
    )

    # --- INSTALL-HOOKS subcommand: hook installer ---
    hooks_parser = subparsers.add_parser(
        'install-hooks',
        help='install code-forge pre-commit hook',
        description='Write .git/hooks/pre-commit with forge gate-check',
    )
    hooks_parser.add_argument(
        "--quiet", action="store_true",
        help="suppress informational messages",
    )

    # --- INSTALL-SKILL subcommand: copy bundled skills into agent dir ---
    skill_parser = subparsers.add_parser(
        'install-skill',
        help='copy bundled review skills into an agent skill directory',
        description=(
            'Copy bundled skills into a target agent skill directory. '
            'Target conventions (subject to change): '
            'claude=~/.claude/skills/, '
            'vscode=<cwd>/.claude/skills/, '
            'universal=<cwd>/.agents/skills/. '
            'Use --dest to override. '
            'Exit codes: 0=success, 2=CLI_ERROR.'
        ),
    )
    skill_parser.add_argument(
        "--target",
        choices=["claude", "vscode", "universal"],
        default="claude",
        help=(
            "agent target: claude (~/.claude/skills/), "
            "vscode (<cwd>/.claude/skills/), "
            "universal (<cwd>/.agents/skills/) "
            "(default: claude)"
        ),
    )
    skill_parser.add_argument(
        "--dest",
        default=None,
        metavar="DIR",
        help="override --target with an explicit destination directory",
    )
    skill_parser.add_argument(
        "--skill",
        default=None,
        metavar="NAME",
        help="install one named skill (default: all bundled skills)",
    )
    skill_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing skill directories",
    )
    skill_parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress informational messages",
    )

    # --- VERIFY subcommand: validate review receipts ---
    verify_parser = subparsers.add_parser(
        'verify',
        help='validate review receipts',
        description=(
            'Validates review receipts: completeness (9 receipts, cycle/pass '
            'matrix), diff hash, anchor reality, timestamp monotonicity, '
            'excerpt verbatim match, coverage >=60%, Jaccard overlap <0.8. '
            'Exit codes: 0=PASS, 1=FAIL.'
        ),
    )
    verify_parser.add_argument(
        "--quiet", action="store_true",
        help="exit code only, no output",
    )

    # --- DETECT subcommand: toolchain auto-detection ---
    detect_parser = subparsers.add_parser(
        'detect',
        help='detect project toolchain and generate tools.yaml',
        description=(
            'Auto-detect project toolchain. '
            'Generates .code-forge/tools.yaml from detected tools.'
        ),
    )
    detect_parser.add_argument(
        "--force", action="store_true",
        help="overwrite existing tools.yaml",
    )

    # --- RESOLVE-OUTLET subcommand: outlet selection ---
    subparsers.add_parser(
        'resolve-outlet',
        help='resolve outlet selection (subprocess, inline, or subagent)',
        description=(
            'Resolve which review outlet to use. '
            'Outputs subprocess, inline, or subagent to stdout. '
            'Exits 1 with a diagnostic if the configured review '
            'backend is unreachable and no explicit override is set.'
        ),
    )

    # --- INIT subcommand: generate gate.yaml template ---
    init_parser = subparsers.add_parser(
        'init',
        help='generate a gate.yaml template in .code-forge/',
    )
    init_parser.add_argument(
        "--force", action="store_true",
        help="overwrite existing gate.yaml and gate.schema.json",
    )

    # --- SMOKE-RUN subcommand: execute a command and write a smoke receipt ---
    smoke_run_parser = subparsers.add_parser(
        'smoke-run',
        help='run a smoke test and record a receipt',
        description=(
            'Execute a command, capture transcript + exit code, and write '
            'a smoke receipt keyed by diff content-hash. '
            'When no receipt exists for the current diff, the RUNTIME axis '
            'reports UNVERIFIED. Silence never reads as verified. '
            'Exit codes: passthrough from the executed command.'
        ),
    )
    smoke_run_parser.add_argument(
        '--surface',
        default='default',
        help='runtime surface name (default: default)',
    )
    smoke_run_parser.add_argument(
        '--target',
        default='HEAD',
        help='git diff target for diff-hash keying (default: HEAD)',
    )
    smoke_run_parser.add_argument(
        '--timeout',
        type=int,
        default=300,
        help='timeout in seconds (default: 300)',
    )
    smoke_run_parser.add_argument(
        'command',
        nargs=argparse.REMAINDER,
        help='command to execute (may be preceded by -- separator)',
    )

    # --- TRUST subcommand: manage trust for repo-supplied backends ---
    trust_parser = subparsers.add_parser(
        'trust',
        help='manage trust for repo-supplied backends',
    )
    trust_group = trust_parser.add_mutually_exclusive_group()
    trust_group.add_argument(
        "--status", action="store_true",
        help="show trust state for current repo",
    )
    trust_group.add_argument(
        "--revoke", action="store_true",
        help="revoke trust for current repo",
    )

    # --- EVAL subcommand: false-green rate evaluation ---
    eval_parser = subparsers.add_parser(
        'eval',
        help='evaluate false-green rate on bug corpus',
    )
    eval_parser.add_argument(
        "--corpus", required=True, type=Path,
        help="path to corpus.yaml manifest",
    )
    eval_parser.add_argument(
        "--backend", required=True,
        help="backend name to evaluate",
    )
    eval_parser.add_argument(
        "--runs", type=int, default=None,
        help="override run count per entry (must be >= 1)",
    )
    eval_parser.add_argument(
        "--output", type=Path, default=None,
        help="path for JSON results file",
    )

    return parser


def _make_subagent_spawn(
    backend, conv_digest: str, post_image: str, contract_spec: str = "",
):
    """Factory for subagent spawn_fn. Module-level for testability.

    Returns a spawn_fn(pass_name, diff_text) -> str that calls llm_invoke
    per pass with a fresh context (no shared session). The prompt contains
    only the diff, post-image content, conventions digest, contract spec,
    and pass role -- no implementer session context (D3 / SC1-SC3).

    Args:
        backend: BackendConfig for llm_invoke, or None for default.
        conv_digest: conventions digest string (D11 slot), may be "".
        post_image: post-image content of changed files, may be "".
        contract_spec: cross-repo contract reference (D-05 slot), may be "".
    """
    _PASS_ROLES = {
        "qodo": "structural code reviewer: correctness and logic errors",
        "expert": "senior engineer: SOLID, architecture, security",
        "adversarial": "adversarial QE: assume bugs exist",
    }

    def _spawn(pass_name: str, diff_text: str) -> str:
        from .llm_invoke import llm_invoke
        role = _PASS_ROLES.get(pass_name, "code reviewer")
        prompt = (
            "You are a " + role + ". Review this diff.\n"
            'Return JSON: {"findings": [{"file": "...", "line": N, '
            '"severity": "P0"|"P1"|"P2"|"P3", '
            '"description": "..."}], '
            '"code_excerpts": [{"file": "...", "start_line": N, '
            '"end_line": M, "content": "..."}]}\n'
            "Each diff hunk MUST have at least one code_excerpt.\n"
            "Even if findings is empty, provide code_excerpts "
            "covering each changed hunk.\n"
            "code_excerpts content must be actual source code lines, "
            "not diff format -- no +/- prefixes, no @@ headers.\n"
        )
        if post_image:
            prompt += (
                "\n## Post-Image (current file content)\n"
                + post_image + "\n"
            )
        if conv_digest:
            prompt += (
                "\n## Conventions Digest\n"
                + conv_digest + "\n"
            )
        if contract_spec:
            prompt += (
                "\n## Contract Reference\n"
                + contract_spec + "\n"
            )
        prompt += "\nDiff:\n" + diff_text
        result = llm_invoke(prompt, backend=backend)
        content = result.content
        if isinstance(content, dict):
            return json.dumps(content)
        return str(content)

    return _spawn


def _assemble_post_image(cwd: Path, diff_text: str) -> tuple[str, str]:
    """Build post-image content and conventions digest for reviewer context.

    Shared by both Outlet C (subagent) and Outlet A (subprocess) paths.
    Returns (post_image, conventions_digest).
    """
    from .diff import get_changed_files
    from .conventions import get_digest

    changed_files = get_changed_files(diff_text or "")
    cap = 50 * 1024
    parts: list[str] = []
    for cf in changed_files:
        fp = cwd / cf
        try:
            st = fp.stat()
            if st.st_size > cap:
                with open(fp, "rb") as fh:
                    raw = fh.read(cap)
                if b"\x00" in raw[:1024]:
                    continue
                text = raw.decode("utf-8", errors="replace")
                text += "\n... [truncated at 50KB]"
            else:
                text = fp.read_text(encoding="utf-8", errors="replace")
                if b"\x00" in text.encode("utf-8", errors="replace")[:1024]:
                    continue
            parts.append("## File: %s\n```\n%s\n```" % (cf, text))
        except (OSError, IOError):
            pass
    return "\n\n".join(parts), get_digest(cwd)


def _run_test_assertion_review(
    diff_text: str,
    backend: Optional[BackendConfig] = None,
) -> list:
    """SC4: test-assertion review by independent reviewer.

    Fresh llm_invoke, never the impl/test author. Runs BEFORE R1.
    Returns list of advisory findings (do not reset cycle counter).

    Advisory-only: findings are printed to stderr but NOT recorded in
    the receipt system. This is an explicit D8 exception -- the
    test-assertion gate is structurally separate from the 3-cycle
    static review. It provides an independent signal for the human
    backstop to act on, not a machine-verified gate. Rationale:
    recording advisory findings in receipts would contaminate the cycle
    counter.
    """
    from .llm_invoke import llm_invoke
    from .reviewer_json import validate_reviewer_json, _json_to_state_findings
    from .diff import get_changed_files

    changed = get_changed_files(diff_text)
    # Test file heuristic -- precise, not "test" in f.lower():
    # Matches /tests/ path component, tests/ prefix, test_ filename prefix,
    # or _test. filename suffix. Does NOT match "contest.py", "protest.py", etc.
    test_files = [
        f for f in changed
        if "/tests/" in f
        or f.startswith("tests/")
        or "test_" in f.split("/")[-1]
        or "_test." in f.split("/")[-1]
    ]
    if not test_files:
        return []

    prompt = (
        "You are a test-assertion reviewer. Review this diff for test quality.\n"
        "Check: assertion completeness, edge case coverage, mock accuracy, "
        "assertion specificity.\n"
        'Return JSON: {"findings": [{"file": "...", "line": N, '
        '"severity": "P0"|"P1"|"P2"|"P3", '
        '"description": "..."}], '
        '"code_excerpts": [{"file": "...", "start_line": N, '
        '"end_line": M, "content": "..."}]}\n'
        "Each diff hunk MUST have at least one code_excerpt.\n"
        "\nDiff:\n" + diff_text
    )
    # H-R3-01: llm_invoke MUST be inside the try block so that
    # network/timeout/auth errors are caught and fail-open.
    try:
        result = llm_invoke(prompt, backend=backend)
        validated = validate_reviewer_json(result.content)
        return _json_to_state_findings(validated, "test-assertion")
    except Exception:
        return []


def _handle_smoke_run(args, cwd: Path) -> int:
    """Handle ``code-forge smoke-run`` subcommand.

    Executes the user-supplied command, captures stdout+stderr, writes a
    smoke receipt keyed by the current diff content-hash. Exits with the
    command's exit code (passthrough).

    Surface name sanitization: any character outside [a-zA-Z0-9_-] is
    replaced with a dash (T-20-06: no path-traversal in receipt filename).

    Args:
        args: parsed argparse namespace with .surface, .target, .command.
        cwd: current working directory.

    Returns:
        int: the command's exit code (or 2 on usage error).
    """
    import datetime
    import re
    import shlex
    from .runtime import write_smoke_receipt

    # Strip leading "--" separator if present (argparse REMAINDER convention)
    cmd_args = list(args.command or [])
    if cmd_args and cmd_args[0] == '--':
        cmd_args = cmd_args[1:]

    if not cmd_args:
        print(
            "code-forge smoke-run: error: no command specified\n"
            "Usage: code-forge smoke-run [--surface NAME] [--target REF] -- CMD [ARGS...]",
            file=sys.stderr,
        )
        return EXIT_CLI_ERROR

    # Sanitize surface name: replace non-[a-zA-Z0-9_-] with dash (T-20-05/F5).
    raw_surface = args.surface or "default"
    surface = re.sub(r'[^a-zA-Z0-9_\-]', '-', raw_surface)

    # Compute repo root via git rev-parse
    try:
        _gr = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=cwd, check=False,
        )
        if _gr.returncode == 0:
            repo_root = Path(_gr.stdout.strip())
        else:
            repo_root = cwd
    except Exception:
        repo_root = cwd

    # Compute current diff for hash keying
    target = getattr(args, 'target', 'HEAD') or 'HEAD'
    try:
        _diff = subprocess.run(
            ["git", "diff", target],
            capture_output=True, text=True, cwd=repo_root, check=False,
        )
        diff_text = _diff.stdout if _diff.returncode == 0 else ""
    except Exception:
        diff_text = ""

    receipts_dir = repo_root / ".code-forge" / "smoke-receipts"

    # Execute the user command (shell=False; user owns the command -- T-20-06)
    smoke_timeout = getattr(args, "timeout", 300) or 300
    try:
        _result = subprocess.run(
            cmd_args,
            capture_output=True,
            text=False,
            cwd=cwd,
            timeout=smoke_timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            "code-forge smoke-run: command timed out after %d seconds"
            % smoke_timeout,
            file=sys.stderr,
        )
        return EXIT_TIMEOUT
    except FileNotFoundError:
        print(
            "code-forge smoke-run: command not found: %s" % cmd_args[0],
            file=sys.stderr,
        )
        return EXIT_CLI_ERROR
    except Exception as exc:
        print(
            "code-forge smoke-run: error running command: %s" % exc,
            file=sys.stderr,
        )
        return EXIT_CLI_ERROR

    exit_code = _result.returncode
    transcript = _result.stdout + _result.stderr
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd_str = " ".join(shlex.quote(a) for a in cmd_args)

    try:
        receipt_path = write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text=diff_text,
            surface=surface,
            command=cmd_str,
            exit_code=exit_code,
            transcript=transcript,
            timestamp=timestamp,
        )
        status = "VERIFIED" if exit_code == 0 else "FAILED"
        print(
            "smoke-run: %s [surface=%s] -> %s" % (status, surface, receipt_path),
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            "code-forge smoke-run: warning: could not write receipt: %s" % exc,
            file=sys.stderr,
        )

    return exit_code


def _run_eval(args) -> int:
    """Handle ``code-forge eval`` subcommand.

    Loads the corpus manifest, replays each entry through the pipeline,
    computes summary, prints table to stderr, optionally writes JSON.

    Returns EXIT_PASS (0) on success, EXIT_CLI_ERROR (2) on bad args or
    missing/malformed corpus. Does NOT raise CliError (non-review
    subcommand convention, matches gate-check/mutation-check).
    """
    # Validate --runs (must be >= 1 if provided)
    if args.runs is not None and args.runs < 1:
        print("--runs must be >= 1", file=sys.stderr)
        return EXIT_CLI_ERROR

    # Lazy imports (cli.py convention)
    from .eval.corpus import load_corpus
    from .eval.runner import replay_entry
    from .eval.scorer import compute_summary, format_table, write_json_report

    # Load backend config through the trust guard (same path as review).
    # Do NOT read gate.yaml raw here; that bypasses the trust check (SEC-02).
    # _run_eval must not raise CliError (non-review convention); catch it here.
    _gate_path = Path.cwd() / ".code-forge" / "gate.yaml"
    import dataclasses as _dc
    try:
        _eval_cfgs, _ = _load_gate_backends(_gate_path)
    except CliError as _exc:
        print(
            "Warning: could not load gate.yaml backend config: %s" % _exc,
            file=sys.stderr,
        )
        _eval_cfgs = []
    _backend_config = None
    for _cfg in _eval_cfgs:
        if _cfg.name == args.backend:
            _backend_config = _dc.asdict(_cfg)
            break

    # Load corpus
    try:
        entries = load_corpus(args.corpus)
    except FileNotFoundError:
        print(
            "corpus not found: %s" % args.corpus, file=sys.stderr,
        )
        return EXIT_CLI_ERROR
    except ValueError as exc:
        print("corpus error: %s" % exc, file=sys.stderr)
        return EXIT_CLI_ERROR

    # Replay each entry
    corpus_dir = args.corpus.parent
    results = []
    for entry in entries:
        result = replay_entry(
            entry,
            corpus_dir=corpus_dir,
            backend_name=args.backend,
            runs=args.runs,
            backend_config=_backend_config,
        )
        results.append(result)

    # Compute summary + output
    summary = compute_summary(results)
    print(format_table(summary), file=sys.stderr)

    if args.output is not None:
        write_json_report(summary, args.output)

    return EXIT_PASS


def _run_trust(args, cwd: Path) -> int:
    """Handle ``code-forge trust`` subcommand.

    Bare trust: mark current repo's gate.yaml as trusted.
    --status: show trust state for current repo.
    --revoke: remove the repo entry from trusted.json.
    """
    import yaml as _y
    from .trust import (
        find_dangerous_fields,
        record_trust,
        record_trust_contracts,
        revoke_trust,
        revoke_trust_contracts,
        trust_status,
        trust_status_contracts,
    )

    gate_yaml_path = cwd / ".code-forge" / "gate.yaml"
    try:
        with open(gate_yaml_path, "r", encoding="utf-8") as _f:
            gd = _y.safe_load(_f)
    except FileNotFoundError:
        print(
            "gate.yaml not found at %s" % gate_yaml_path,
            file=sys.stderr,
        )
        return EXIT_CLI_ERROR
    except _y.YAMLError as exc:
        print(
            "gate.yaml parse error: %s" % exc, file=sys.stderr,
        )
        return EXIT_CLI_ERROR

    if gd is None or not isinstance(gd, dict):
        print(
            "gate.yaml is empty or invalid", file=sys.stderr,
        )
        return EXIT_CLI_ERROR

    contracts_yaml_path = cwd / ".code-forge" / "contracts.yaml"

    if args.status:
        s = trust_status(gate_yaml_path, gd)
        print("Trusted: %s" % s.trusted, file=sys.stderr)
        print(
            "Stored hash: %s" % (s.stored_hash or "(none)"),
            file=sys.stderr,
        )
        print("Current hash: %s" % s.current_hash, file=sys.stderr)
        print("Path: %s" % s.gate_yaml_path, file=sys.stderr)
        if contracts_yaml_path.is_file():
            from .contract_loader import resolve_contract_specs
            resolved_specs = resolve_contract_specs(
                contracts_yaml_path, cwd,
            )
            trust_contents = [
                (abs_path, content)
                for _, _, abs_path, content, _ in resolved_specs
            ]
            cs = trust_status_contracts(
                contracts_yaml_path, trust_contents,
            )
            print(
                "Contracts trusted: %s" % cs.trusted, file=sys.stderr,
            )
            print(
                "Contracts hash: %s"
                % (cs.stored_hash or "(none)"),
                file=sys.stderr,
            )
        return EXIT_PASS

    if args.revoke:
        revoke_trust(gate_yaml_path)
        print(
            "Trust revoked for %s" % gate_yaml_path, file=sys.stderr,
        )
        if contracts_yaml_path.is_file():
            revoke_trust_contracts(contracts_yaml_path)
            print(
                "Contracts trust revoked for %s"
                % contracts_yaml_path,
                file=sys.stderr,
            )
        return EXIT_PASS

    # Bare trust: display dangerous fields, then record trust.
    dangers = find_dangerous_fields(gd)
    if dangers:
        print("Dangerous fields found:", file=sys.stderr)
        for bname, fname, fvalue in dangers:
            print(
                "  %s.%s = %s" % (bname, fname, fvalue),
                file=sys.stderr,
            )
    record_trust(gate_yaml_path, gd)
    print("Trusted: %s" % gate_yaml_path, file=sys.stderr)
    if contracts_yaml_path.is_file():
        from .contract_loader import resolve_contract_specs
        resolved_specs = resolve_contract_specs(
            contracts_yaml_path, cwd,
        )
        trust_contents = [
            (abs_path, content)
            for _, _, abs_path, content, _ in resolved_specs
        ]
        record_trust_contracts(contracts_yaml_path, trust_contents)
        print(
            "Contracts trusted: %s" % contracts_yaml_path,
            file=sys.stderr,
        )
    return EXIT_PASS


def main() -> int:
    """Entry point. Returns exit code (int).

    setuptools entry-point shim calls sys.exit(main()).

    Subcommand routing:
      - review: existing pipeline (_run)
      - gate-check: gate_check.run_gate_check()
      - mutation-check: _run_mutation_check()
      - e2e-check: _run_e2e_check_cmd()
      - install-hooks: install_hooks.run_install_hooks()
      - None (bare forge): default to review for backward compat

    Backward compat for `forge a.py b.py`:
      If sys.argv doesn't start with a known subcommand, prepend 'review'
      to route positional args to the review subparser.
    """
    parser = _build_parser()

    # Backward compat: detect if first arg is a known subcommand
    # If not, prepend 'review' to sys.argv for argparse
    known_subcommands = {
        'review', 'gate-check', 'mutation-check', 'e2e-check',
        'install-hooks', 'install-skill', 'verify',
        'detect', 'resolve-outlet', 'init', 'trust', 'eval', 'smoke-run',
    }
    argv = sys.argv[1:]  # skip program name

    # Filter out --version and --help which are on root parser
    non_flag_args = [a for a in argv if not a.startswith('-')]

    if non_flag_args and non_flag_args[0] not in known_subcommands:
        # First non-flag arg is not a subcommand, so prepend 'review'
        argv = ['review'] + argv

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else EXIT_CLI_ERROR

    # Backward compat: bare `forge` (no subcommand) defaults to review
    if args.subcommand is None:
        args.subcommand = 'review'

    # Route to subcommand handler
    if args.subcommand == 'review':
        try:
            verdict = _run(args, env=os.environ, cwd=Path.cwd())
        except CliError as exc:
            print("code-forge: error: %s" % exc, file=sys.stderr)
            if exc.remediation:
                print("Hint: %s" % exc.remediation, file=sys.stderr)
            return EXIT_CLI_ERROR
        except ForgeLockBusy as exc:
            print("code-forge: %s" % exc, file=sys.stderr)
            return EXIT_BUSY
        except TimeoutBreaker as exc:
            print("code-forge: %s" % exc, file=sys.stderr)
            return EXIT_TIMEOUT
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(
                "code-forge: unexpected error: %s" % exc, file=sys.stderr
            )
            traceback.print_exc(file=sys.stderr)
            return EXIT_FAIL

        # B2: PENDING guard before verdict_to_exit.
        if verdict == Verdict.PENDING:
            return EXIT_PASS
        return verdict_to_exit(verdict)

    elif args.subcommand == 'gate-check':
        from .gate_check import run_gate_check
        return run_gate_check(
            args=args, env=os.environ, cwd=Path.cwd(),
            stdout=sys.stdout, stderr=sys.stderr
        )

    elif args.subcommand == 'mutation-check':
        return _run_mutation_check(args, cwd=Path.cwd())

    elif args.subcommand == 'e2e-check':
        return _run_e2e_check_cmd(args, cwd=Path.cwd())

    elif args.subcommand == 'install-hooks':
        from .install_hooks import run_install_hooks
        return run_install_hooks(
            args=args, env=os.environ, cwd=Path.cwd(),
            stdout=sys.stdout, stderr=sys.stderr
        )

    elif args.subcommand == 'install-skill':
        return _run_install_skill(args, cwd=Path.cwd())

    elif args.subcommand == 'verify':
        from .source import compute_source_hash
        from .verify import run_verify, parse_diff_files
        import subprocess
        cwd = Path.cwd()
        try:
            diff_result = subprocess.run(
                ["git", "diff", "HEAD"], capture_output=True, text=True, cwd=cwd
            )
        except FileNotFoundError:
            print(
                "code-forge: verify: git not found on PATH",
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR
        if diff_result.returncode != 0:
            print(
                "verify: FAIL -- git diff failed: %s" % diff_result.stderr.strip(),
                file=sys.stderr,
            )
            return EXIT_FAIL
        diff_text = diff_result.stdout
        diff_sha = compute_source_hash(git_diff=diff_text)
        diff_f = parse_diff_files(diff_text)
        vr = run_verify(cwd, diff_sha, diff_f, diff_text=diff_text)
        if not args.quiet:
            print("verify: %s -- %s" % ("PASS" if vr.passed else "FAIL", vr.reason))
        return EXIT_PASS if vr.passed else EXIT_FAIL

    elif args.subcommand == 'detect':
        return _run_detect(args, cwd=Path.cwd())

    elif args.subcommand == 'resolve-outlet':
        return _run_resolve_outlet(env=os.environ, cwd=Path.cwd())

    elif args.subcommand == 'trust':
        return _run_trust(args, cwd=Path.cwd())

    elif args.subcommand == 'eval':
        return _run_eval(args)

    elif args.subcommand == 'smoke-run':
        return _handle_smoke_run(args, cwd=Path.cwd())

    elif args.subcommand == 'init':
        from importlib.resources import files as _pkg_files
        from .init_template import GATE_YAML_TEMPLATE
        gate_dir = Path.cwd() / ".code-forge"
        try:
            gate_dir.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            raise CliError(
                ".code-forge exists but is not a directory",
                remediation="Remove the file: rm %s" % gate_dir,
            )
        gate_path = gate_dir / "gate.yaml"
        if gate_path.exists() and not args.force:
            print(
                "gate.yaml already exists at %s" % gate_path,
                file=sys.stderr,
            )
            print("Use --force to overwrite.", file=sys.stderr)
            return EXIT_CLI_ERROR
        gate_path.write_text(GATE_YAML_TEMPLATE)
        print("Created %s" % gate_path, file=sys.stderr)
        schema_path = gate_dir / "gate.schema.json"
        if not schema_path.exists() or args.force:
            schema_text = _pkg_files('code_forge').joinpath('gate.schema.json').read_text(encoding='utf-8')
            schema_path.write_text(schema_text)
            print("Created %s" % schema_path, file=sys.stderr)
        template_path = gate_dir / "contract-template.md"
        if not template_path.exists() or args.force:
            from .init_template import CONTRACT_TEMPLATE_MD
            template_path.write_text(CONTRACT_TEMPLATE_MD)
            print("Created %s" % template_path, file=sys.stderr)
        print(
            "Next: add a backend under 'backends:' in gate.yaml "
            "(examples inside), then run 'code-forge trust'. "
            "Review refuses to run until a backend is configured.",
            file=sys.stderr,
        )
        return EXIT_PASS

    else:
        print(
            "code-forge: unknown subcommand: %s" % args.subcommand,
            file=sys.stderr
        )
        return EXIT_CLI_ERROR


def _load_gate_siblings(gate_yaml_path: Path) -> tuple:
    """Load siblings section from gate.yaml for cross-repo dispatch.

    Returns (gate_raw_dict, siblings_list_or_None).  A missing or empty
    gate.yaml yields no siblings.  Malformed yaml or a non-mapping gate.yaml
    fails CLOSED (CliError): for inline backends this is the ONLY gate.yaml
    parse, so silently dropping a bad file could hide intended siblings and
    run single-repo without the user knowing (D-06 fail-closed).
    """
    import yaml as _y

    try:
        text = gate_yaml_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    try:
        raw = _y.safe_load(text)
    except _y.YAMLError as exc:
        raise CliError(
            "malformed gate.yaml at %s: %s" % (gate_yaml_path, exc),
            remediation="Validate YAML syntax. Run 'code-forge init --force' to regenerate.",
        )
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        raise CliError(
            "gate.yaml must be a mapping, got %s" % type(raw).__name__
        )
    return raw, raw.get("siblings")


def _cross_repo_verdict_or_none(
    *, gate_yaml_path: Path, cwd: Path, baseline_spec, head_spec,
    mode, engine_choice, backend, max_rounds: Optional[int],
    max_fix: Optional[int], _clean_threshold: int, warn: Callable
) -> Optional[Verdict]:
    """Decide and execute cross-repo dispatch, or return None to fall through."""
    _gate_raw, _gate_siblings = _load_gate_siblings(gate_yaml_path)
    if _gate_siblings is not None and not _gate_siblings:
        warn("gate.yaml has empty siblings: [] section; "
             "falling through to single-repo review")
    if _gate_siblings:
        from .baseline import GitRefBaseline
        from .cross_repo import run_cross_repo
        from .gate_check import validate_siblings

        if not isinstance(baseline_spec, GitRefBaseline):
            raise CliError(
                "cross-repo review requires a git ref baseline, "
                "got %s" % type(baseline_spec).__name__
            )
        if (isinstance(head_spec, GitRefBaseline)
                and head_spec.ref in ("WORKING", "INDEX")):
            raise CliError(
                "cross-repo review requires committed refs, "
                "not %s" % head_spec.ref,
                remediation="Commit your changes first, or use --committed to review the last commit.",
            )
        validate_siblings(
            _gate_siblings,
            gate_yaml_dir=gate_yaml_path.parent,
        )
        # Build primary_ref from raw git ref variables (baseline_spec.ref
        # and head_spec.ref), not baseline_repr which is the serialized
        # form ("git:HEAD") unusable as a ref range.
        _head_ref = head_spec.ref if head_spec is not None else "HEAD"
        _primary_ref = "%s..%s" % (baseline_spec.ref, _head_ref)
        _cross_verdict = run_cross_repo(
            primary_path=cwd,
            primary_ref=_primary_ref,
            primary_label="primary",
            siblings=_gate_siblings,
            gate_config=_gate_raw if isinstance(_gate_raw, dict) else {},
            mode=mode,
            engine_choice=engine_choice,
            backend=backend,
            max_rounds=max_rounds,
            max_fix_attempts=max_fix,
            clean_round_threshold=_clean_threshold,
        )
        return _cross_verdict
    return None


def _load_contract_file(path_str: str, warn_fn=None) -> str:
    """Read and validate a per-change intent contract file.

    Performs file reading and all guards (empty, whitespace-only, binary,
    oversize, encoding). Does NOT summarize -- backend is not available at
    call time. Returns raw content string.

    Args:
        path_str: File path or "-" for stdin.
        warn_fn: Optional callable for warnings (receives a string).

    Raises:
        CliError: On empty path, missing file, permission error, encoding
            error, empty/whitespace-only content, binary content, or
            oversized content.
    """
    if not path_str:
        raise CliError(
            "contract path is empty",
            remediation="Pass a file path or pipe content via stdin.",
        )

    if path_str == "-":
        try:
            raw = sys.stdin.buffer.read(65537)
        except (OSError, ValueError):
            raise CliError("contract: cannot read from stdin")
        if len(raw) > 65536:
            raise CliError("contract from stdin exceeds 64KB limit")
        if b"\x00" in raw:
            raise CliError("contract from stdin appears to be binary")
        try:
            content = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise CliError("contract from stdin is not valid UTF-8")
    else:
        try:
            content = Path(path_str).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise CliError("contract file not found: %s" % path_str)
        except PermissionError:
            raise CliError("contract file not readable: %s" % path_str)
        except OSError as exc:
            raise CliError("contract file error: %s" % exc)
        except ValueError:
            raise CliError(
                "contract file is not valid UTF-8: %s" % path_str
            )

    if not content.strip():
        raise CliError("contract file is empty: %s" % path_str)
    if "\x00" in content:
        raise CliError(
            "contract file appears to be binary: %s" % path_str
        )
    if len(content.encode("utf-8")) > 65536:
        raise CliError(
            "contract file exceeds 64KB limit: %s" % path_str
        )
    return content


_CONFIRMATION_BIAS_DIRECTIVE = (
    "\n\nNOTE: The contract above states invariants to verify and "
    "residual risks. It is NOT a proof of correctness. Assume "
    "violations exist and look for them."
)


def _merge_contract_spec(
    yaml_digest: str,
    file_content: str,
    backend=None,
    warn_fn=None,
) -> str:
    """Merge contracts.yaml digest with --contract file content.

    Optionally summarizes large file content (>4KB bytes) when backend
    is available. Appends the confirmation-bias directive when any
    content is present.

    Args:
        yaml_digest: Digest from contracts.yaml (may be "").
        file_content: Raw content from --contract file (may be "").
        backend: BackendConfig for summarization (None = skip).
        warn_fn: Optional callable for warnings.

    Returns:
        Merged contract spec string, or "" if both inputs empty.
    """
    merged = ""
    if yaml_digest:
        merged = yaml_digest
    if file_content:
        effective_content = file_content
        if len(file_content.encode("utf-8")) > 4096 and backend is not None:
            try:
                from .llm_invoke import llm_invoke
                result = llm_invoke(
                    "Summarize the following contract to its key "
                    "invariants and residual risks:\n" + file_content,
                    backend=backend,
                )
                summary = str(result.content)
                if not summary.strip():
                    if warn_fn:
                        warn_fn(
                            "contract: summarization returned empty, "
                            "injecting raw content"
                        )
                else:
                    effective_content = summary
            except Exception:
                if warn_fn:
                    warn_fn(
                        "contract: summarization failed, "
                        "injecting raw content"
                    )
        merged = (merged + "\n\n" if merged else "") + effective_content
    if merged:
        merged += _CONFIRMATION_BIAS_DIRECTIVE
    return merged


def _run(args, env, cwd: Path) -> Verdict:
    """Main pipeline body. Returns Verdict."""
    warn = (lambda msg: None) if args.quiet else (
        lambda msg: print("code-forge: %s" % msg, file=sys.stderr)
    )

    # Worktree validation (BOTH-03): only if in git repo
    if is_git_repo(cwd):
        allow_main = (
            getattr(args, "allow_main", False)
            or env.get("FORGE_ALLOW_MAIN") == "1"
            or env.get("FORGE_SKIP_WORKTREE_CHECK") == "1"
        )
        if not allow_main:
            try:
                result_work_tree = subprocess.run(
                    ["git", "rev-parse", "--is-inside-work-tree"],
                    capture_output=True, text=True, cwd=cwd, check=False,
                )
                result_git_dir = subprocess.run(
                    ["git", "rev-parse", "--git-dir"],
                    capture_output=True, text=True, cwd=cwd, check=False,
                )
                result_common_dir = subprocess.run(
                    ["git", "rev-parse", "--git-common-dir"],
                    capture_output=True, text=True, cwd=cwd, check=False,
                )

                if result_work_tree.returncode == 0:
                    git_dir = result_git_dir.stdout.strip()
                    common_dir = result_common_dir.stdout.strip()

                    # If git-dir and git-common-dir are the same, not a worktree
                    if git_dir == common_dir:
                        raise CliError(
                            "code-forge review must run inside a linked git "
                            "worktree, not the main tree. Create one: "
                            "git worktree add .worktrees/work <branch>\n"
                            "Or bypass: --allow-main / "
                            "FORGE_ALLOW_MAIN=1"
                        )
            except subprocess.SubprocessError as exc:
                raise CliError(
                    "git worktree check failed: %s" % exc
                ) from exc


    # Validate mutual exclusion BEFORE outlet resolution
    # (prevents --backend from triggering reachability_fn probe)
    inline_flags = [
        getattr(args, 'backend_url', None),
        getattr(args, 'backend_format', None),
        getattr(args, 'backend_key_env', None),
        getattr(args, 'backend_model', None),
    ]
    has_inline = any(f is not None for f in inline_flags)
    has_backend_name = getattr(args, 'backend', None) is not None
    if has_backend_name and has_inline:
        raise CliError(
            "--backend and inline flags are mutually exclusive"
        )
    if has_inline and not all(f is not None for f in inline_flags):
        raise CliError(
            "inline backend requires all 4 flags: "
            "--backend-url/format/key-env/model"
        )


    # Step 0: Outlet resolution (GA1 bridge)
    from .outlet_resolver import resolve_outlet
    gate_yaml_path = cwd / ".code-forge" / "gate.yaml"

    # Load gate backends once through the trust guard; reuse cfgs for outlet
    # resolution, reachability probe, AND backend resolution.  Never re-read
    # gate.yaml raw after this point -- a second read bypasses the trust check.
    cfgs, gate_data = _load_gate_backends(gate_yaml_path)
    # Validate and extract retry config from gate.yaml (D-31-02).
    # _load_gate_backends returns the full YAML dict; load_gate_config
    # (which calls validate_retry_config) is only used by other callers,
    # so we validate here on the actual review path.
    from .gate_check import validate_retry_config
    retry_cfg = gate_data.get("retry", {})
    validate_retry_config(retry_cfg)

    # Early contract file read (D-32-22): validate before backend resolution.
    _contract_file_content = ""
    if getattr(args, "contract", None) is not None:
        _contract_file_content = _load_contract_file(
            args.contract, warn_fn=warn,
        )

    # has_explicit_backend is True when the user passed --backend <name>
    # or assembled an inline backend via --backend-url/format/key-env/model.
    _backend_arg = getattr(args, 'backend', None)
    has_explicit_backend = has_inline or (_backend_arg is not None)

    def _reachability():
        from .backend import resolve_backend, probe_backend
        backend = resolve_backend(
            env,
            configs=cfgs,
            cli_value=_backend_arg,
        )
        return probe_backend(backend, env=env)

    outlet = resolve_outlet(
        env,
        gate_yaml_path if gate_yaml_path.exists() else None,
        cli_value=getattr(args, 'outlet', None),
        configs=cfgs,
        has_explicit_backend=has_explicit_backend,
        reachability_fn=_reachability,
    )
    if outlet == "sampling":
        raise CliError(
            "outlet 'sampling' is only available within the MCP server context"
        )
    if outlet == "inline":
        canary_config = _load_canary_config(args, gate_data)
        if canary_config is not None:
            try:
                from .canary_gen import run_inline_canary
                from .backend import resolve_backend
                from .llm_invoke import llm_invoke as _llm_invoke

                import subprocess as _sp
                _diff_result = _sp.run(
                    ["git", "diff", "HEAD"],
                    capture_output=True, text=True, cwd=str(cwd),
                )
                diff_text = _diff_result.stdout if _diff_result.returncode == 0 else ""

                try:
                    backend = resolve_backend(
                        env, configs=cfgs,
                        cli_value=getattr(args, "backend", None),
                    )
                except Exception:
                    backend = None

                n_canaries = canary_config.get("n", 5)

                def _canary_provider(diff_text_arg: str) -> list:
                    if backend is None:
                        return []
                    import json as _json
                    prompt = (
                        "You are a code mutation expert. Given this Python diff, "
                        "generate %d subtle semantic mutations. Each mutation must "
                        "introduce a real bug (off-by-one, None deref, resource "
                        "leak, etc.) that requires non-local reasoning to detect. "
                        "Do NOT include comments explaining the bug.\n"
                        "Each snippet MUST be <= 5 lines. "
                        '"line" is the 1-based line number of the bug WITHIN '
                        "the 'code' snippet (not a file line number).\n"
                        'Return JSON: {"mutations": [{"file": "...", '
                        '"line": N, "original": "<unmodified code snippet>", '
                        '"code": "<mutated code snippet>", '
                        '"description": "..."}]}\n\n'
                        "Diff:\n" + diff_text_arg
                    ) % n_canaries
                    try:
                        result = _llm_invoke(prompt, backend=backend)
                        content = result.content
                        if isinstance(content, dict):
                            return content.get("mutations", [])
                        parsed = _json.loads(str(content))
                        return parsed.get("mutations", [])
                    except Exception as exc:
                        sys.stderr.write(
                            "code-forge: canary generation failed: %s, "
                            "falling back to templates\n" % exc
                        )
                        return []

                def _review_provider(prompt: str) -> str:
                    if backend is None:
                        raise RuntimeError("no backend available for canary review")
                    import json as _json
                    result = _llm_invoke(prompt, backend=backend)
                    return (
                        _json.dumps(result.content)
                        if isinstance(result.content, dict)
                        else str(result.content)
                    )

                def _source_lookup(filepath: str):
                    import os
                    cwd_real = os.path.realpath(str(cwd))
                    full = os.path.realpath(os.path.join(cwd_real, filepath))
                    if not full.startswith(cwd_real + os.sep) and full != cwd_real:
                        return None
                    try:
                        os.path.commonpath([cwd_real, full])
                    except ValueError:
                        return None
                    if not os.path.isfile(full):
                        return None
                    with open(full, encoding="utf-8", errors="replace") as f:
                        return f.readlines()

                verdict, real_findings = run_inline_canary(
                    diff_text=diff_text,
                    n=canary_config.get("n", 5),
                    threshold_ratio=canary_config.get("threshold_ratio", 0.6),
                    canary_provider=_canary_provider,
                    review_provider=_review_provider,
                    source_lookup=_source_lookup,
                )
                if real_findings:
                    sys.stderr.write("code-forge: canary-verified findings:\n")
                    for f in real_findings:
                        sys.stderr.write("  %s:%s [%s] %s\n" % (
                            f.get("file", "?"), f.get("line", "?"),
                            f.get("severity", "?"), f.get("description", "?"),
                        ))
                return verdict
            except Exception as exc:
                sys.stderr.write(
                    "code-forge: canary check failed (%s), "
                    "falling back to DELEGATED\n" % exc
                )
        # D4 honesty floor: inline does not run the StateMachine gate.
        # Declare DELEGATED so callers can distinguish from a real PASS.
        sys.stderr.write(
            "code-forge: DELEGATED -- review delegated to session"
            " + external R1; exit 5\n"
        )
        return Verdict.DELEGATED

    # Step 6: backend resolution (moved above subagent dispatch so backend
    # is available to the C-leg spawn_fn closure -- M-R2-07).
    # has_inline is defined at line ~645, confirmed in scope here.
    from .backend import (
        BackendConfig,
        resolve_backend,
    )
    from .llm_invoke import LLMInvokeError

    if has_inline:
        # All 4 inline flags: construct transient BackendConfig, skip gate.yaml
        backend = BackendConfig(
            name="inline",
            type="api",
            format=args.backend_format,
            base_url=args.backend_url,
            api_key_env=args.backend_key_env,
            model=args.backend_model,
            max_tokens=16384,
        )
    else:
        # Use cfgs from _load_gate_backends above -- trust-guarded, never
        # re-read gate.yaml raw here.  A raw load_backend_configs(gate_data)
        # call at this point would bypass the trust check (SEC-02).
        try:
            backend = resolve_backend(
                env,
                configs=cfgs,
                cli_value=getattr(args, 'backend', None),
            )
        except CliError:
            raise

    # outlet == "subprocess" (Outlet A): fall through to review pipeline

    state_dir = cwd / ".code-forge"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"
    lock_path = state_dir / "code-forge.lock"

    # Step 1: mode
    mode = resolve_mode(args.mode, env, sys.stdout.isatty())

    # Step 2: registry (with auto-detect fallback)
    is_default_registry = (args.registry == ".code-forge/tools.yaml")

    def _safe_load_registry(path):
        """Load registry, translating ValueError to CliError."""
        try:
            return load_registry(path)
        except ValueError as exc:
            raise CliError(
                "registry load failed: %s" % exc,
                remediation="Verify the path exists. Omit --registry to use the default (.code-forge/tools.yaml).",
            ) from exc

    try:
        registry = _safe_load_registry(args.registry)
    except FileNotFoundError:
        if is_default_registry:
            from .detect import detect_and_init
            detect_and_init(cwd, quiet=True)
            registry = _safe_load_registry(args.registry)
        else:
            raise CliError(
                "registry load failed: %s not found" % args.registry,
                remediation="Verify the path exists. Omit --registry to use the default (.code-forge/tools.yaml).",
            )

    if registry == {} and is_default_registry:
        from .detect import detect_and_init
        detect_and_init(cwd, quiet=True)
        registry = _safe_load_registry(args.registry)

    # Step 3: env overrides
    max_rounds = resolve_max_total_rounds(
        args.max_total_rounds, env
    )
    max_fix = resolve_max_fix_attempts(
        args.max_fix_attempts, env
    )
    engine_choice = resolve_falsification_engine(
        args.falsification_engine, env
    )

    # Step 4: baseline / head (H4: two-phase paths resolution)
    baseline_spec, head_spec = _build_baseline_specs(
        args, cwd, warn=warn
    )
    initial_paths = _paths(args, cwd, resolved=None)
    try:
        resolved = resolve_baseline(
            baseline_spec, head_spec, initial_paths, cwd
        )
    except BaselineResolutionError as exc:
        raise CliError(
            "baseline resolution failed: %s" % exc,
            remediation="Check that the ref exists: git rev-parse <ref>. Omit --baseline to skip delta.",
        )
    # Late-phase paths: extract from diff if user passed none.
    if not initial_paths:
        effective_paths = _paths(args, cwd, resolved=resolved)
        if effective_paths:
            resolved = resolve_baseline(
                baseline_spec, head_spec, effective_paths, cwd
            )

    # Step 5: source identity (B3: keyword args on mode_hint)
    if resolved.mode_hint == "git":
        source_hash = compute_source_hash(
            git_diff=resolved.git_diff or ""
        )
    else:
        source_hash = compute_source_hash(
            files=resolved.source_files
        )
    baseline_repr = serialize_baseline_spec(baseline_spec)

    # Compute diff-size tier threshold
    from .diff import count_diff_lines, tier_threshold
    _line_count = count_diff_lines(resolved.git_diff or "")
    _whole_file = bool(getattr(args, "whole_file", None))
    _env_threshold = None
    try:
        _env_raw = os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD")
        if _env_raw is not None:
            _env_threshold = int(_env_raw)
    except (ValueError, TypeError):
        pass
    _clean_threshold = tier_threshold(
        _line_count, _whole_file, _env_threshold
    )

    # Outlet C (subagent): dispatch via run_outlet_c with llm_invoke-based
    # spawn_fn. Backend is resolved above. resolved/source_hash
    # are now in scope at this point in the flow.
    if outlet == "subagent":
        from .outlet_c import run_outlet_c
        from .taint import TaintRunner
        from .runtime import RuntimeRunner
        from .legacy import LegacyRunner
        from .graph_triage import GraphTriageRunner
        from .daemon_state import DaemonStateRunner

        _post_image, _conv_digest = _assemble_post_image(
            cwd, resolved.git_diff or ""
        )
        _contracts_yaml_c = cwd / ".code-forge" / "contracts.yaml"
        _yaml_digest_c = ""
        if _contracts_yaml_c.is_file():
            from .contract_loader import load_contract_digest
            _yaml_digest_c = load_contract_digest(
                _contracts_yaml_c, cwd, backend=backend,
            )
        _contract_spec_c = _merge_contract_spec(
            _yaml_digest_c, _contract_file_content,
            backend=backend, warn_fn=warn,
        )
        _subagent_spawn = _make_subagent_spawn(
            backend, _conv_digest, _post_image,
            contract_spec=_contract_spec_c,
        )
        _c_taint = TaintRunner()
        _c_runtime = RuntimeRunner(backend=backend)
        _c_graph = GraphTriageRunner()
        # Do NOT set _c_graph._cached_findings here.
        # The pre-fetched graph findings variable is defined later in _run,
        # after this block's early return -- it is not in scope here.
        # GraphTriageRunner fetches fresh findings on its advisory pass.
        _c_daemon = DaemonStateRunner(backend=backend)
        _c_legacy = LegacyRunner()
        verdict = run_outlet_c(
            resolved_review=resolved,
            source_hash=source_hash,
            cwd=cwd,
            spawn_fn=_subagent_spawn,
            clean_round_threshold=_clean_threshold,
            registry=registry,
            backend=backend,
            engine=engine_choice,
            advisory_runners=[_c_taint, _c_runtime, _c_graph, _c_daemon, _c_legacy],
        )
        # Test-assertion review gate (D14/SC4): advisory findings to stderr.
        # D8 exception: not recorded in receipts (see _run_test_assertion_review).
        if resolved.git_diff:
            _ta_findings = _run_test_assertion_review(
                resolved.git_diff, backend
            )
            for _f in _ta_findings:
                sys.stderr.write(
                    "[test-assertion] %s\n" % _f.description
                )
        return verdict

    # M6: non-git snapshot auto-detection.
    if (resolved.mode_hint == "non-git"
            and args.baseline is None
            and isinstance(baseline_spec, EmptyBaseline)):
        from .snapshot import find_existing_snapshot
        snap_path = find_existing_snapshot(source_hash, cwd)
        if snap_path is not None:
            baseline_spec = SnapshotBaseline(path=snap_path)
            try:
                resolved = resolve_baseline(
                    baseline_spec, head_spec,
                    resolved.source_files, cwd,
                )
            except BaselineResolutionError as exc:
                raise CliError(
                    "snapshot baseline resolution failed: %s"
                    % exc
                )
            baseline_repr = serialize_baseline_spec(
                baseline_spec
            )

    # Step 6 (backend) already resolved above for both outlet paths.

    if args.sandbox:
        warn(
            "warning: --sandbox is a Phase 4 hook; "
            "ignored in v2.0"
        )

    _post_image_a, _conv_digest_a = _assemble_post_image(
        cwd, resolved.git_diff or ""
    )

    _contracts_yaml_a = cwd / ".code-forge" / "contracts.yaml"
    _yaml_digest_a = ""
    if _contracts_yaml_a.is_file():
        from .contract_loader import load_contract_digest
        _yaml_digest_a = load_contract_digest(
            _contracts_yaml_a, cwd, backend=backend,
        )
    _contract_spec_a = _merge_contract_spec(
        _yaml_digest_a, _contract_file_content,
        backend=backend, warn_fn=warn,
    )

    # Pre-loop graph triage: build impact context for L1 prompt.
    # Runs once before the hold loop; findings are NOT added to
    # advisories (prompt context only). The runner is discarded
    # after building the context string.
    _graph_impact_context = ""
    _pre_graph_findings: list = []
    try:
        from .graph_triage import GraphTriageRunner as _PreGT
        _pre_graph = _PreGT()
        _pre_graph_findings = _pre_graph.run(
            resolved.git_diff or "", cwd,
        )
        if _pre_graph_findings:
            _rows = []
            for _f in _pre_graph_findings:
                _desc = _f.description
                # Parse "name (impact: N downstream) -- top dependents: a, b"
                _parts = _desc.split(" (impact: ", 1)
                _ename = _parts[0] if _parts else "unknown"
                _downstream = "0"
                _deps = ""
                if len(_parts) > 1:
                    _rest = _parts[1]
                    _dp = _rest.split(" downstream)", 1)
                    _downstream = _dp[0] if _dp else "0"
                    if len(_dp) > 1 and "-- top dependents: " in _dp[1]:
                        _deps = _dp[1].split(
                            "-- top dependents: ", 1
                        )[1].strip()
                _rows.append(
                    "| %s | %s | %s | %s |"
                    % (_ename, _f.file, _downstream, _deps)
                )
            _graph_impact_context = (
                "| Entity | File | Downstream | Top Dependents |\n"
                "|--------|------|------------|----------------|\n"
                + "\n".join(_rows)
            )
    except Exception:
        _pre_graph_findings = []

    falsifier = build_falsifier(engine_choice, backend=backend)
    autofixer = build_autofixer(resolved)
    revert_fn = build_revert_fn(resolved, cwd)

    from .machine import TimeoutCircuitBreaker
    breaker = TimeoutCircuitBreaker(threshold=5)

    l1_provider = build_l1_provider(
        engine_choice, resolved, backend=backend,
        conventions_digest=_conv_digest_a,
        post_image=_post_image_a,
        graph_impact_context=_graph_impact_context,
        contract_spec=_contract_spec_a,
        breaker=breaker,
        max_attempts=retry_cfg.get("max_attempts", 5),
        initial_delay_s=retry_cfg.get("initial_delay_s", 2.0),
    )

    # Coverage gate inputs: L1 examines every changed file only when it
    # actually runs over a diff (engine != stub AND a non-empty git diff).
    # A non-git review or the stub engine leaves L1 inactive, so only L0
    # tool matches provide per-file coverage.
    coverage_l1_active = (
        engine_choice != "stub" and bool(resolved.git_diff)
    )
    from .coverage import load_coverage_exempt_patterns
    try:
        coverage_exempt = load_coverage_exempt_patterns(cwd)
    except CoverageConfigError as exc:
        raise CliError(str(exc))

    # Cross-repo dispatch: if gate.yaml has a non-empty siblings section,
    # dispatch to run_cross_repo before acquiring the lock.  Single-repo
    # (no siblings) falls through to _run_hold_loop unchanged.
    # NOTE: this reads gate.yaml independently from the backend-resolution
    # load at line ~1249 because gate_data is scoped inside the has_inline
    # else-block and may not exist when the user passed --backend-url/etc.
    _cv = _cross_repo_verdict_or_none(
        gate_yaml_path=gate_yaml_path,
        cwd=cwd,
        baseline_spec=baseline_spec,
        head_spec=head_spec,
        mode=mode,
        engine_choice=engine_choice,
        backend=backend,
        max_rounds=max_rounds,
        max_fix=max_fix,
        _clean_threshold=_clean_threshold,
        warn=warn,
    )
    if _cv is not None:
        return _cv

    # Step 7: lock + run
    try:
        with ForgeLock(lock_path):
            verdict = _run_hold_loop(
                mode=mode,
                falsifier=falsifier,
                autofixer=autofixer,
                revert_fn=revert_fn,
                l1_provider=l1_provider,
                resolved=resolved,
                source_hash=source_hash,
                baseline_repr=baseline_repr,
                cwd=cwd,
                registry=registry,
                max_rounds=max_rounds,
                max_fix_attempts=max_fix,
                state_path=state_path,
                coverage_l1_active=coverage_l1_active,
                coverage_exempt_patterns=coverage_exempt,
                clean_round_threshold=_clean_threshold,
                backend=backend,
                pre_graph_findings=_pre_graph_findings,
            )
            # SARIF emission in CI mode, inside lock scope.
            if mode == Mode.CI:
                _emit_ci_output(
                    state_path, registry,
                    backend_name=(
                        backend.name if backend.type == "api" else None
                    ),
                    backend_model=(
                        backend.model if backend.type == "api" else None
                    ),
                )
    except LLMInvokeError as exc:
        # re-wrap LLMInvokeError as CliError
        raise CliError(
            "backend %s: %s" % (backend.name, exc)
        ) from exc

    # Test-assertion review gate (D14/SC4) on subprocess path: runs BEFORE
    # return, advisory-only (D8 exception per _run_test_assertion_review).
    if resolved.git_diff:
        _ta_findings_a = _run_test_assertion_review(
            resolved.git_diff, backend
        )
        for _f_a in _ta_findings_a:
            sys.stderr.write(
                "[test-assertion] %s\n" % _f_a.description
            )
    return verdict


def _run_hold_loop(
    *, mode, falsifier, autofixer, revert_fn, l1_provider, resolved,
    source_hash, baseline_repr, cwd, registry,
    max_rounds, max_fix_attempts, state_path,
    coverage_l1_active=True, coverage_exempt_patterns=None,
    clean_round_threshold=3,
    backend=None,
    pre_graph_findings=None,
    input_fn=input, output_fn=print,
) -> Verdict:
    """HOLD-resume loop. Bounded by MAX_HOLD_CYCLES."""
    for cycle in range(MAX_HOLD_CYCLES):
        # Fresh runners per cycle to prevent cross-cycle state
        # accumulation (infra_errors, source_files).
        from .taint import TaintRunner
        from .runtime import RuntimeRunner
        from .legacy import LegacyRunner
        from .graph_triage import GraphTriageRunner
        from .daemon_state import DaemonStateRunner

        _taint_runner = TaintRunner()
        _runtime_runner = RuntimeRunner(backend=backend)
        _graph_triage_runner = GraphTriageRunner()
        _graph_triage_runner._cached_findings = pre_graph_findings
        _daemon_state_runner = DaemonStateRunner(backend=backend)
        _legacy_runner = LegacyRunner()
        sm = StateMachine(
            mode=mode,
            falsifier=falsifier,
            autofixer=autofixer,
            revert_fn=revert_fn,
            l1_provider=l1_provider,
            resolved_review=resolved,
            source_hash=source_hash,
            baseline_spec_repr=baseline_repr,
            cwd=cwd,
            registry=registry,
            max_total_rounds=max_rounds,
            max_fix_attempts=max_fix_attempts,
            coverage_l1_active=coverage_l1_active,
            coverage_exempt_patterns=coverage_exempt_patterns or [],
            clean_round_threshold=clean_round_threshold,
            advisory_runners=[
                _taint_runner, _runtime_runner,
                _graph_triage_runner, _daemon_state_runner,
                _legacy_runner,
            ],
        )
        verdict = sm.run()
        if verdict != Verdict.PENDING:
            # CLI-08 B6: load final state from disk for cost fields.
            from .state import load_state as _load_cost_state
            final_state = _load_cost_state(state_path)
            if final_state is not None and final_state.cost_passes > 0:
                total_tokens = (
                    final_state.cost_total_input
                    + final_state.cost_total_output
                )
                if total_tokens > 0:
                    token_str = "%d tokens (%d in + %d out)" % (
                        total_tokens,
                        final_state.cost_total_input,
                        final_state.cost_total_output,
                    )
                else:
                    token_str = "tokens: N/A (cli backend)"
                print(
                    "code-forge: cost: %s, %d passes, %.1fs" % (
                        token_str,
                        final_state.cost_passes,
                        final_state.cost_total_duration,
                    ),
                    file=sys.stderr,
                )
            return verdict
        # M3: load state from disk (public API, not sm._state).
        from .state import load_state
        loaded = load_state(state_path)
        if loaded is None:
            return Verdict.ESCALATED
        try:
            run_hold_ui(
                loaded, state_path,
                input_fn=input_fn, output_fn=output_fn,
            )
        except HoldAborted as exc:
            print(
                "code-forge: %s; state preserved at %s"
                % (exc, state_path),
                file=sys.stderr,
            )
            return Verdict.PENDING

    # MAX_HOLD_CYCLES exhausted.
    from .state import State, load_state, save_state
    final = load_state(state_path)
    if final is None:
        # fallback if state.json deleted mid-run.
        final = State(
            mode=mode,
            source_hash=source_hash,
            baseline_spec_repr=baseline_repr,
        )
    final.infra_errors.append(
        "MAX_HOLD_CYCLES=%d exhausted; human re-entered HOLD "
        "too many times" % MAX_HOLD_CYCLES
    )
    final.verdict = Verdict.ESCALATED
    final.converged = False
    save_state(final, state_path)
    return Verdict.ESCALATED


def _run_mutation_check(args, cwd: Path) -> int:
    """Synchronous wrapper for mutation-check subcommand.

    Reads diff-scoped files from git, calls run_mutation(), translates
    findings to exit code.

    Exit codes:
      0  PASS (no survivors)
      1  FAIL (survivors found)
      2  CLI_ERROR (git or invocation error)
    """
    from .mutation import run_mutation

    # Resolve diff source.
    if args.diff is not None:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(
                "code-forge: mutation-check: diff file not found: %s"
                % args.diff,
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR
        try:
            diff_text = diff_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                "code-forge: mutation-check: cannot read diff: %s" % exc,
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR
        from .diff import get_changed_files
        diff_files = get_changed_files(diff_text)
    else:
        # Uncommitted changes via git diff.
        import subprocess
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(cwd),
            )
            if result.returncode != 0:
                print(
                    "code-forge: mutation-check: git diff failed: %s"
                    % result.stderr.strip(),
                    file=sys.stderr,
                )
                return EXIT_CLI_ERROR
            diff_files = [
                f for f in result.stdout.splitlines() if f.strip()
            ]
        except FileNotFoundError:
            print(
                "code-forge: mutation-check: git not found",
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR

    # Apply --paths glob filter if requested.
    if getattr(args, "paths", None):
        from fnmatch import fnmatch as _fnmatch
        glob_pat = args.paths
        diff_files = [f for f in diff_files if _fnmatch(f, glob_pat)]

    # Default baseline command: pytest (same as gate_check convention).
    baseline_cmd = ["pytest", "--tb=no", "-q"]

    findings, infra_errors = run_mutation(
        diff_files=diff_files,
        baseline_cmd=baseline_cmd,
        timeout=args.timeout,
        cwd=cwd,
    )

    # Report infra errors to stderr (informational).
    for err in infra_errors:
        print("code-forge: mutation-check: %s" % err, file=sys.stderr)

    # Translate findings to exit code.
    # CONFIRMED findings with source=MUTANT and id starting "mutant-" are
    # survivors. DISMISSED findings (skips) are not failures.
    from .disposition import Disposition
    survivors = [
        f for f in findings
        if (f.disposition == Disposition.CONFIRMED
            and f.source == "MUTANT"
            and f.id.startswith("mutant-"))
    ]
    if survivors:
        print(
            "code-forge: mutation-check: %d survivor(s) found"
            % len(survivors),
            file=sys.stderr,
        )
        for s in survivors:
            print(
                "  %s" % s.description,
                file=sys.stderr,
            )
        return EXIT_FAIL

    print("code-forge: mutation-check: PASS", file=sys.stderr)
    return EXIT_PASS


def _run_e2e_check_cmd(args, cwd: Path) -> int:
    """Synchronous wrapper for e2e-check subcommand.

    Reads diff text, calls run_e2e_check(), translates findings to exit code.

    Exit codes:
      0  PASS (no UNCERTAIN findings or no diff)
      1  FAIL (UNCERTAIN findings present -- P2 equivalent)
      2  CLI_ERROR (diff read error)
    """
    from .e2e_check import run_e2e_check
    from .disposition import Disposition

    repo_root = Path(args.repo_root) if args.repo_root else cwd

    # Resolve diff source.
    if args.diff is not None:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(
                "code-forge: e2e-check: diff file not found: %s" % args.diff,
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR
        try:
            diff_text = diff_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                "code-forge: e2e-check: cannot read diff: %s" % exc,
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR
    else:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(cwd),
            )
            if result.returncode != 0:
                print(
                    "code-forge: e2e-check: git diff failed: %s"
                    % result.stderr.strip(),
                    file=sys.stderr,
                )
                return EXIT_CLI_ERROR
            diff_text = result.stdout
        except FileNotFoundError:
            print(
                "code-forge: e2e-check: git not found",
                file=sys.stderr,
            )
            return EXIT_CLI_ERROR

    if not diff_text or not diff_text.strip():
        print("code-forge: e2e-check: no diff -- SKIP", file=sys.stderr)
        return EXIT_PASS

    findings, infra_errors = run_e2e_check(
        diff_text=diff_text,
        repo_root=repo_root,
    )

    for err in infra_errors:
        print("code-forge: e2e-check: %s" % err, file=sys.stderr)

    # UNCERTAIN findings are the P2-equivalent gate failures.
    uncertain = [
        f for f in findings
        if f.disposition == Disposition.UNCERTAIN
    ]
    if uncertain:
        print(
            "code-forge: e2e-check: %d finding(s)" % len(uncertain),
            file=sys.stderr,
        )
        for f in uncertain:
            print("  %s" % f.description, file=sys.stderr)
        return EXIT_FAIL

    print("code-forge: e2e-check: PASS", file=sys.stderr)
    return EXIT_PASS


def _resolve_whole_file_specs(args, cwd: Path):
    """Resolve --whole-file paths into (baseline, head, paths) tuple.

    Returns None when --whole-file is not set so callers can fall through
    to their normal logic.

    When set, validates all paths are relative and under cwd, enforces
    mutual-exclusion with --committed/--baseline/--head, and
    returns a 3-tuple: (EmptyBaseline(), head_spec, [Path, ...]).
    """
    whole_file = getattr(args, "whole_file", None)
    if whole_file is None:
        return None
    in_git = is_git_repo(cwd)
    # Mutual-exclusion: --whole-file conflicts with mode-selection flags
    if getattr(args, "committed", False):
        raise CliError("--whole-file cannot be combined with --committed")
    if args.baseline is not None:
        raise CliError("--whole-file cannot be combined with --baseline")
    if args.head is not None:
        raise CliError("--whole-file cannot be combined with --head")
    if getattr(args, "paths", None):
        raise CliError("--whole-file cannot be combined with positional paths")
    # Path validation: all entries must be relative and under cwd
    cwd_resolved = cwd.resolve()
    for p in whole_file:
        pp = Path(p)
        if pp.is_absolute():
            raise CliError(
                "--whole-file: path must be relative, got: %s" % p
            )
        resolved_p = (cwd / pp).resolve()
        try:
            resolved_p.relative_to(cwd_resolved)
        except ValueError:
            raise CliError(
                "--whole-file: path escapes repo root: %s" % p
            )
    head_spec = GitRefBaseline("WORKING") if in_git else None
    return EmptyBaseline(), head_spec, [Path(p) for p in whole_file]


def _build_baseline_specs(
    args, cwd: Path, warn=None,
) -> tuple:
    """Parse --baseline + --head into BaselineSpec union members."""
    in_git = is_git_repo(cwd)

    # Handle --whole-file via shared resolver; keep 2-tuple return for callers
    wf_result = _resolve_whole_file_specs(args, cwd)
    if wf_result is not None:
        baseline, head, _ = wf_result
        return baseline, head

    # Check --committed conflicts first
    if args.committed:
        if args.baseline is not None:
            raise CliError(
                "--committed cannot be combined with --baseline"
            )
        if args.head is not None:
            raise CliError(
                "--committed cannot be combined with --head"
            )
    # Apply --committed mapping
    if args.committed:
        baseline = GitRefBaseline("HEAD~1")
        head = GitRefBaseline("HEAD")
        return baseline, head

    if args.baseline is None:
        baseline = (
            GitRefBaseline("HEAD") if in_git else EmptyBaseline()
        )
    elif args.baseline == "empty":
        baseline = EmptyBaseline()
    elif (args.baseline.startswith(".code-forge/snapshots/")
          or (args.baseline.endswith(".json")
              and "snapshots" in args.baseline)):
        baseline = SnapshotBaseline(path=Path(args.baseline))
    else:
        baseline = GitRefBaseline(args.baseline)

    if args.head is None:
        head = GitRefBaseline("WORKING") if in_git else None
    else:
        head = GitRefBaseline(args.head)
    return baseline, head


def _paths(args, cwd: Path, resolved=None) -> list:
    """H4: derive paths from explicit args OR git_diff extraction."""
    wf_result = _resolve_whole_file_specs(args, cwd)
    if wf_result is not None:
        _, _, paths_list = wf_result
        return paths_list
    if args.paths:
        return [Path(p) for p in args.paths]
    if resolved is None:
        return []
    if resolved.mode_hint == "git" and resolved.git_diff:
        from .diff import get_changed_files
        return [Path(p) for p in get_changed_files(
            resolved.git_diff
        )]
    if resolved.mode_hint == "non-git":
        raise CliError(
            "non-git mode requires explicit paths argument(s); "
            "no files would be reviewed otherwise"
        )
    return []


def _run_install_skill(args, cwd: Path) -> int:
    """Install bundled review skills into an agent skill directory.

    Target directory conventions (subject to change as agent ecosystems evolve):
      claude    -> ~/.claude/skills/
      vscode    -> <cwd>/.claude/skills/
      universal -> <cwd>/.agents/skills/
      --dest D  -> D/

    Returns 0 on success, 2 on CLI_ERROR.
    """
    import shutil
    from importlib.resources import files as _pkg_files

    quiet = args.quiet

    def _info(msg: str) -> None:
        if not quiet:
            print("code-forge: install-skill: %s" % msg)

    def _warn(msg: str) -> None:
        print("code-forge: install-skill: %s" % msg, file=sys.stderr)

    # Resolve destination directory
    if args.dest is not None:
        dest_root = Path(args.dest)
    elif args.target == "claude":
        dest_root = Path.home() / ".claude" / "skills"
    elif args.target == "vscode":
        dest_root = cwd / ".claude" / "skills"
    elif args.target == "universal":
        dest_root = cwd / ".agents" / "skills"
    else:
        _warn("unknown target: %s" % args.target)
        return EXIT_CLI_ERROR

    # Locate bundled skills via importlib.resources
    try:
        src_root = _pkg_files("code_forge") / "skills"
    except Exception as exc:
        _warn("cannot locate bundled skills: %s" % exc)
        return EXIT_CLI_ERROR

    # Build list of skill names to install
    if args.skill is not None:
        # Reject names that contain path separators (path traversal guard)
        if "/" in args.skill or "\\" in args.skill or args.skill in (".", ".."):
            _warn("invalid skill name: %s" % args.skill)
            return EXIT_CLI_ERROR
        skill_src = src_root / args.skill

        def _show_available() -> None:
            try:
                avail = sorted(e.name for e in src_root.iterdir() if e.is_dir())
            except Exception:
                avail = []
            if avail:
                _warn("available skills: %s" % ", ".join(avail))

        # Validate the named skill exists in the bundle
        try:
            # Access __iter__ or check the traversable exists
            skill_files = list(skill_src.iterdir())
            if not skill_files:
                _warn("skill not found in bundle: %s" % args.skill)
                _show_available()
                return EXIT_CLI_ERROR
        except (FileNotFoundError, NotADirectoryError, TypeError):
            _warn("skill not found in bundle: %s" % args.skill)
            _show_available()
            return EXIT_CLI_ERROR
        skill_names = [args.skill]
    else:
        try:
            skill_names = sorted(
                entry.name for entry in src_root.iterdir()
                if entry.is_dir()
            )
        except Exception as exc:
            _warn("cannot list bundled skills: %s" % exc)
            return EXIT_CLI_ERROR
        if not skill_names:
            _warn("no bundled skills found")
            return EXIT_CLI_ERROR

    # Create destination root if needed
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _warn("cannot create destination directory %s: %s" % (dest_root, exc))
        return EXIT_CLI_ERROR

    # Copy each skill
    for name in skill_names:
        skill_src_dir = src_root / name
        skill_dest_dir = dest_root / name

        if skill_dest_dir.exists() and not args.force:
            _warn(
                "SKIP %s (exists; use --force to overwrite)" % name
            )
            continue

        # If force and dest exists, remove it first
        if skill_dest_dir.exists() and args.force:
            try:
                shutil.rmtree(str(skill_dest_dir))
            except OSError as exc:
                _warn(
                    "cannot remove existing %s: %s" % (skill_dest_dir, exc)
                )
                return EXIT_CLI_ERROR

        # Copy from importlib.resources traversable to filesystem
        # importlib.resources Traversable does not support shutil.copytree
        # directly; walk the traversable tree manually.
        try:
            _copy_traversable_tree(skill_src_dir, skill_dest_dir)
        except OSError as exc:
            _warn("failed to copy %s: %s" % (name, exc))
            return EXIT_CLI_ERROR

        _info("INSTALLED %s -> %s" % (name, skill_dest_dir))

    return EXIT_PASS


def _copy_traversable_tree(src, dest: Path) -> None:
    """Recursively copy an importlib.resources Traversable tree to dest.

    dest is created by this function. Caller must ensure it does not exist.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        child_dest = dest / entry.name
        if entry.is_dir():
            _copy_traversable_tree(entry, child_dest)
        else:
            child_dest.write_bytes(entry.read_bytes())


def _run_detect(args, cwd: Path) -> int:
    """Run toolchain detection and generate tools.yaml.

    Lazy-imports detect_and_init to avoid circular imports and
    keep startup fast for other subcommands.

    Returns:
        EXIT_PASS on success, EXIT_CLI_ERROR on detection failure.
    """
    from .detect import detect_and_init
    try:
        detect_and_init(cwd, force=args.force)
    except CliError as exc:
        print(
            "code-forge: detect: %s" % exc,
            file=sys.stderr,
        )
        return EXIT_CLI_ERROR
    return EXIT_PASS


def _run_resolve_outlet(env, cwd: Path) -> int:
    """Resolve and print the active review outlet.

    Returns:
        EXIT_PASS on success.
        EXIT_FAIL on backend-unreachable (runtime condition).
        EXIT_CLI_ERROR on config/validation error (ValueError).
    """
    from .outlet_resolver import resolve_outlet
    gate_yaml_path = cwd / ".code-forge" / "gate.yaml"
    cfgs, _ = _load_gate_backends(gate_yaml_path)

    def _reachability():
        from .backend import resolve_backend, probe_backend
        backend = resolve_backend(env, configs=cfgs, cli_value=None)
        return probe_backend(backend, env=env)

    try:
        outlet = resolve_outlet(
            env,
            gate_yaml_path if gate_yaml_path.exists() else None,
            cli_value=None,
            configs=cfgs,
            reachability_fn=_reachability,
        )
        print(outlet)
        return EXIT_PASS
    except CliError as exc:
        print(
            "code-forge: resolve-outlet: %s" % exc,
            file=sys.stderr,
        )
        return EXIT_FAIL
    except ValueError as exc:
        print(
            "code-forge: resolve-outlet: %s" % exc,
            file=sys.stderr,
        )
        return EXIT_CLI_ERROR

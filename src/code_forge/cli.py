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
    verdict_to_exit,
)
from .factories import build_autofixer, build_falsifier, build_l1_provider, build_revert_fn
from .git import is_git_repo
from .hold import HoldAborted, run_hold_ui
from .lock import ForgeLock, ForgeLockBusy
from .machine import StateMachine
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


def _load_gate_backends(gate_yaml_path: Path) -> list:
    """Load backend configs from gate.yaml, or return [] if absent.

    Raises:
        CliError: gate.yaml exists but is corrupt or has invalid backend config.
    """
    import yaml as _y
    from .backend import load_backend_configs


    try:
        with open(gate_yaml_path, "r", encoding="utf-8") as _f:
            gd = _y.safe_load(_f)
    except FileNotFoundError:
        return []
    except _y.YAMLError as exc:
        raise CliError("gate.yaml parse error: %s" % exc) from exc

    if isinstance(gd, dict):
        return load_backend_configs(gd)
    return []


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
        "--state-dir", default=None,
        help="DEPRECATED: state directory is hardcoded to "
             "cwd/.code-forge; value is ignored.",
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
        "--staged", action="store_true",
        help="DEPRECATED v2.1: use --head INDEX "
             "(mapped internally with warning)",
    )
    review_parser.add_argument(
        "--outlet", choices=["subprocess", "cli", "inline", "subagent"], default=None,
        help="review outlet (default: auto-detect via backend reachability)",
    )
    review_parser.add_argument(
        "--committed", action="store_true",
        help="review the last commit (maps to --baseline HEAD~1 --head HEAD)",
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
        help="overwrite existing gate.yaml",
    )

    return parser


def _make_subagent_spawn(backend, conv_digest: str, post_image: str):
    """Factory for subagent spawn_fn. Module-level for testability.

    Returns a spawn_fn(pass_name, diff_text) -> str that calls llm_invoke
    per pass with a fresh context (no shared session). The prompt contains
    only the diff, post-image content, conventions digest, and pass role --
    no implementer session context (D3 / SC1-SC3).

    Args:
        backend: BackendConfig for llm_invoke, or None for default.
        conv_digest: conventions digest string (D11 slot), may be "".
        post_image: post-image content of changed files, may be "".
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
    backend: Optional["BackendConfig"] = None,
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
    counter (Pitfall 4 in RESEARCH.md).
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
        'detect', 'resolve-outlet', 'init',
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
            return EXIT_CLI_ERROR
        except ForgeLockBusy as exc:
            print("code-forge: %s" % exc, file=sys.stderr)
            return EXIT_BUSY
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
        diff_result = subprocess.run(
            ["git", "diff", "HEAD"], capture_output=True, text=True, cwd=cwd
        )
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

    elif args.subcommand == 'init':
        from .init_template import GATE_YAML_TEMPLATE
        gate_dir = Path.cwd() / ".code-forge"
        gate_dir.mkdir(parents=True, exist_ok=True)
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
        return EXIT_PASS

    else:
        print(
            "code-forge: unknown subcommand: %s" % args.subcommand,
            file=sys.stderr
        )
        return EXIT_CLI_ERROR


def _run(args, env, cwd: Path) -> Verdict:
    """Main pipeline body. Returns Verdict."""
    warn = (lambda msg: None) if args.quiet else (
        lambda msg: print("code-forge: %s" % msg, file=sys.stderr)
    )

    # Worktree validation (BOTH-03): only if in git repo
    if is_git_repo(cwd):
        skip_check = env.get("FORGE_SKIP_WORKTREE_CHECK") == "1"
        if not skip_check:
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
                            "git worktree add .worktrees/work <branch>"
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

    # Load gate backends once; share with both the zero-config guard and
    # the reachability closure so they operate on the same config list.
    cfgs = _load_gate_backends(gate_yaml_path)
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
    if outlet == "inline":
        # SKILL.md owns the review pipeline; CLI exits early
        return Verdict.PASS

    # Step 6: backend resolution (moved above subagent dispatch so backend
    # is available to the C-leg spawn_fn closure -- M-R2-07).
    # has_inline is defined at line ~645, confirmed in scope here.
    import yaml as _yaml
    from .backend import (
        BackendConfig,
        load_backend_configs,
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
        # T1: Load gate.yaml backends block (D-16 lightweight loader)
        _gate_yaml_path_b = cwd / ".code-forge" / "gate.yaml"
        try:
            with open(_gate_yaml_path_b, "r", encoding="utf-8") as _f:
                gate_data = _yaml.safe_load(_f)
        except FileNotFoundError:
            gate_data = None
        except _yaml.YAMLError as exc:
            raise CliError("gate.yaml parse error: %s" % exc) from exc

        if isinstance(gate_data, dict):
            configs = load_backend_configs(gate_data)
        else:
            configs = []

        try:
            backend = resolve_backend(
                env,
                configs=configs,
                cli_value=getattr(args, 'backend', None),
            )
        except CliError:
            raise

    # outlet == "subprocess" (Outlet A): fall through to review pipeline

    # R4-M2: --state-dir deprecated; hardcode to cwd/.forge.
    if (args.state_dir is not None
            and args.state_dir != ".code-forge"):
        warn(
            "warning: --state-dir is deprecated v2.1; v2.0 always "
            "uses cwd/.code-forge (your value %r is ignored)"
            % args.state_dir
        )
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
            raise CliError("registry load failed: %s" % exc) from exc

    try:
        registry = _safe_load_registry(args.registry)
    except FileNotFoundError:
        if is_default_registry:
            from .detect import detect_and_init
            detect_and_init(cwd, quiet=True)
            registry = _safe_load_registry(args.registry)
        else:
            raise CliError(
                "registry load failed: %s not found" % args.registry
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
        raise CliError("baseline resolution failed: %s" % exc)
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

    # Outlet C (subagent): dispatch via run_outlet_c with llm_invoke-based
    # spawn_fn. Backend is resolved above. resolved/source_hash
    # are now in scope at this point in the flow.
    if outlet == "subagent":
        from .outlet_c import run_outlet_c

        _post_image, _conv_digest = _assemble_post_image(
            cwd, resolved.git_diff or ""
        )
        _subagent_spawn = _make_subagent_spawn(backend, _conv_digest, _post_image)
        verdict = run_outlet_c(
            resolved_review=resolved,
            source_hash=source_hash,
            cwd=cwd,
            spawn_fn=_subagent_spawn,
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

    falsifier = build_falsifier(engine_choice, backend=backend)
    autofixer = build_autofixer(resolved)
    revert_fn = build_revert_fn(resolved, cwd)
    l1_provider = build_l1_provider(
        engine_choice, resolved, backend=backend,
        conventions_digest=_conv_digest_a,
        post_image=_post_image_a,
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
        # T4: D-04/D-14 boundary: re-wrap LLMInvokeError as CliError
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
    input_fn=input, output_fn=print,
) -> Verdict:
    """HOLD-resume loop. Bounded by MAX_HOLD_CYCLES."""
    for cycle in range(MAX_HOLD_CYCLES):
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
        # R4-L3: fallback if state.json deleted mid-run.
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
    mutual-exclusion with --committed/--staged/--baseline/--head, and
    returns a 3-tuple: (EmptyBaseline(), head_spec, [Path, ...]).
    """
    whole_file = getattr(args, "whole_file", None)
    if whole_file is None:
        return None
    in_git = is_git_repo(cwd)
    # Mutual-exclusion: --whole-file conflicts with mode-selection flags
    if getattr(args, "committed", False):
        raise CliError("--whole-file cannot be combined with --committed")
    if getattr(args, "staged", False):
        raise CliError("--whole-file cannot be combined with --staged")
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
        if args.staged:
            raise CliError(
                "--committed and --staged are mutually exclusive "
                "(--staged is deprecated; use --head INDEX)"
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

    # R2-M4: warn ANY time --staged is set.
    if args.staged:
        msg = (
            "warning: --staged is deprecated; use --head INDEX "
            "(will be removed in v2.1)"
        )
        if warn is not None:
            warn(msg)
        else:
            print("code-forge: %s" % msg, file=sys.stderr)

    if args.staged and args.head is None:
        head = GitRefBaseline("INDEX")
    elif args.head is None:
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
        # Validate the named skill exists in the bundle
        try:
            # Access __iter__ or check the traversable exists
            skill_files = list(skill_src.iterdir())
            if not skill_files:
                _warn("skill not found in bundle: %s" % args.skill)
                return EXIT_CLI_ERROR
        except (FileNotFoundError, NotADirectoryError, TypeError):
            _warn("skill not found in bundle: %s" % args.skill)
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
    cfgs = _load_gate_backends(gate_yaml_path)

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

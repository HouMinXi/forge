# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge CLI entry point.

02-05 owns this file. Replaces Phase 1 L0-only pipeline with the
full 02-XX integration.

Pipeline (single invocation):
  1. parse args (argparse) -- CLI-01
  2. resolve env overrides -- CLI-03
  3. validate paths + registry -- exit 2 on failure
  4. acquire lock (02-04 ForgeLock) -- exit 3 on busy
  5. resolve mode (02-04 resolve_mode)
  6. construct BaselineSpec (02-03)
  7. resolve_baseline -> ResolvedReview (02-03)
  8. compute_source_hash + serialize_baseline_spec (02-03)
  9. build factories (Falsifier/AutoFixer/revert_fn) -- STATE-10
 10. construct StateMachine (02-02)
 11. HOLD-resume loop: run -> on PENDING run_hold_ui -> re-run
 12. map terminal Verdict to exit code (CLI-02)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from . import __version__
from .runner import capture_tool_version
from .sarif import build_sarif_log, format_summary

if TYPE_CHECKING:
    from .registry import ToolConfig
from .baseline import (
    BaselineSpec,
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
from .errors import BaselineResolutionError, CliError
from .exit_codes import (
    EXIT_BUSY,
    EXIT_CLI_ERROR,
    EXIT_FAIL,
    EXIT_PASS,
    verdict_to_exit,
)
from .factories import build_autofixer, build_falsifier, build_revert_fn
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
) -> None:
    """LAYER0-07: SARIF stdout + summary stderr in CI mode.

    Re-loads state from disk for canonical view (catches any save_state
    divergence). Re-captures tool_versions per Integration 2 (avoids
    02-02 StateMachine constructor surface change).

    If load_state returns None -> silent return (no log warning).
    Rationale: SARIF is best-effort output, NOT canonical artifact;
    state.json is canonical. Silent return matches "skip SARIF when
    state absent" semantics.
    """
    final_state = _load_state(state_path)
    if final_state is None:
        return
    tool_versions = {
        name: capture_tool_version(tc.command)
        for name, tc in registry.items()
    }
    log_dict = build_sarif_log(
        final_state, tool_versions, forge_version=__version__
    )
    print(json.dumps(log_dict), file=sys.stdout)
    print(format_summary(final_state), file=sys.stderr)
    if post_emit_hook is not None:
        post_emit_hook()


def _build_parser() -> argparse.ArgumentParser:
    """CLI-01 argparse surface.

    Defaults documented in REQUIREMENTS line 75; --help includes
    Exit Codes section per CLI-02.
    """
    parser = argparse.ArgumentParser(
        prog="forge",
        description="3-state quality gate for code review",
        epilog=(
            "Exit codes:\n"
            "  0  PASS\n"
            "  1  FAIL\n"
            "  2  CLI_ERROR (invalid args, missing config, "
            "parse error)\n"
            "  3  BUSY (another forge process holds the lock)\n"
            "  4  ESCALATED (non-convergence or human-frozen)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", choices=["local", "ci"], default=None,
        help="execution mode (default: local if TTY, ci otherwise)",
    )
    parser.add_argument(
        "--falsification-engine", choices=["auto", "stub", "real"],
        default=None,
        help="STATE-10 engine select (default: auto)",
    )
    parser.add_argument(
        "--sandbox", action="store_true",
        help="enable sandbox for autofixer "
             "(Phase 4 hook; v2.0 no-op + warning)",
    )
    parser.add_argument(
        "--baseline", default=None,
        help="baseline ref "
             "(git: HEAD/INDEX/<sha>; non-git: empty|<snapshot-path>)",
    )
    parser.add_argument(
        "--head", default=None,
        help="head ref (git only: WORKING/INDEX/<sha>; "
             "ignored non-git)",
    )
    parser.add_argument(
        "--registry", default=".forge/tools.yaml",
        help="path to tools.yaml (default: .forge/tools.yaml)",
    )
    parser.add_argument(
        "--state-dir", default=None,
        help="DEPRECATED v2.1: state directory is hardcoded to "
             "cwd/.forge per 02-02 StateMachine. Accepted for Phase 1 "
             "compat; value is ignored.",
    )
    parser.add_argument(
        "--max-total-rounds", type=int, default=None,
        help="LOCAL mode round bound "
             "(default 20 or FORGE_MAX_TOTAL_ROUNDS)",
    )
    parser.add_argument(
        "--max-fix-attempts", type=int, default=None,
        help="per-fingerprint fix budget "
             "(default 3 or "
             "FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="suppress tool-skipped, version, and deprecation "
             "messages",
    )
    parser.add_argument(
        "--version", action="version",
        version="forge %s" % __version__,
    )
    # H1: Phase 1 flags preserved with deprecation.
    parser.add_argument(
        "--staged", action="store_true",
        help="DEPRECATED v2.1: use --head INDEX "
             "(mapped internally with warning)",
    )
    parser.add_argument(
        "paths", nargs="*",
        help="files/dirs to review; git mode filters diff, "
             "non-git lists files",
    )
    return parser


def main() -> int:
    """Entry point. Returns exit code (int).

    setuptools entry-point shim calls sys.exit(main()).
    """
    parser = _build_parser()
    try:
        args = parser.parse_args()
    except SystemExit as e:
        return int(e.code) if e.code is not None else EXIT_CLI_ERROR

    try:
        verdict = _run(args, env=os.environ, cwd=Path.cwd())
    except CliError as exc:
        print("forge: error: %s" % exc, file=sys.stderr)
        return EXIT_CLI_ERROR
    except ForgeLockBusy as exc:
        print("forge: %s" % exc, file=sys.stderr)
        return EXIT_BUSY
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(
            "forge: unexpected error: %s" % exc, file=sys.stderr
        )
        traceback.print_exc(file=sys.stderr)
        return EXIT_FAIL

    # B2: PENDING guard before verdict_to_exit.
    if verdict == Verdict.PENDING:
        return EXIT_PASS
    return verdict_to_exit(verdict)


def _run(args, env, cwd: Path) -> Verdict:
    """Main pipeline body. Returns Verdict."""
    warn = (lambda msg: None) if args.quiet else (
        lambda msg: print("forge: %s" % msg, file=sys.stderr)
    )
    # R4-M2: --state-dir deprecated; hardcode to cwd/.forge.
    if (args.state_dir is not None
            and args.state_dir != ".forge"):
        warn(
            "warning: --state-dir is deprecated v2.1; v2.0 always "
            "uses cwd/.forge (your value %r is ignored)"
            % args.state_dir
        )
    state_dir = cwd / ".forge"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"
    lock_path = state_dir / "forge.lock"

    # Step 1: mode
    mode = resolve_mode(args.mode, env, sys.stdout.isatty())

    # Step 2: registry
    try:
        registry = load_registry(args.registry)
    except (FileNotFoundError, ValueError) as exc:
        raise CliError("registry load failed: %s" % exc)

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

    # Step 6: factories
    if args.sandbox:
        warn(
            "warning: --sandbox is a Phase 4 hook; "
            "ignored in v2.0"
        )
    falsifier = build_falsifier(engine_choice)
    autofixer = build_autofixer(resolved)
    revert_fn = build_revert_fn(resolved, cwd)

    # Step 7: lock + run
    with ForgeLock(lock_path):
        verdict = _run_hold_loop(
            mode=mode,
            falsifier=falsifier,
            autofixer=autofixer,
            revert_fn=revert_fn,
            resolved=resolved,
            source_hash=source_hash,
            baseline_repr=baseline_repr,
            cwd=cwd,
            registry=registry,
            max_rounds=max_rounds,
            max_fix_attempts=max_fix,
            state_path=state_path,
        )
        # 02-06: SARIF emission in CI mode, INSIDE lock scope.
        if mode == Mode.CI:
            _emit_ci_output(state_path, registry)
    return verdict


def _run_hold_loop(
    *, mode, falsifier, autofixer, revert_fn, resolved,
    source_hash, baseline_repr, cwd, registry,
    max_rounds, max_fix_attempts, state_path,
    input_fn=input, output_fn=print,
) -> Verdict:
    """HOLD-resume loop. Bounded by MAX_HOLD_CYCLES."""
    for cycle in range(MAX_HOLD_CYCLES):
        sm = StateMachine(
            mode=mode,
            falsifier=falsifier,
            autofixer=autofixer,
            revert_fn=revert_fn,
            resolved_review=resolved,
            source_hash=source_hash,
            baseline_spec_repr=baseline_repr,
            cwd=cwd,
            registry=registry,
            max_total_rounds=max_rounds,
            max_fix_attempts=max_fix_attempts,
        )
        verdict = sm.run()
        if verdict != Verdict.PENDING:
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
                "forge: %s; state preserved at %s"
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


def _build_baseline_specs(
    args, cwd: Path, warn=None,
) -> tuple:
    """Parse --baseline + --head into BaselineSpec union members."""
    in_git = is_git_repo(cwd)
    if args.baseline is None:
        baseline = (
            GitRefBaseline("HEAD") if in_git else EmptyBaseline()
        )
    elif args.baseline == "empty":
        baseline = EmptyBaseline()
    elif (args.baseline.startswith(".forge/snapshots/")
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
            print("forge: %s" % msg, file=sys.stderr)

    if args.staged and args.head is None:
        head = GitRefBaseline("INDEX")
    elif args.head is None:
        head = GitRefBaseline("WORKING") if in_git else None
    else:
        head = GitRefBaseline(args.head)
    return baseline, head


def _paths(args, cwd: Path, resolved=None) -> list:
    """H4: derive paths from explicit args OR git_diff extraction."""
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

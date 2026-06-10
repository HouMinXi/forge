"""Eval pipeline replay runner: per-entry subprocess invocation (D-19).

Each corpus entry runs through the COMPLETE forge pipeline via subprocess.
Each run is an independent subprocess invocation with fresh context (separate
temp dir, no shared LLM conversation) to preserve statistical independence
for D-11's 2-of-3 majority.

Run count is axis-dependent (D-11):
  - Deterministic axes (TRUST, SEC, FIXVAL): default runs=1
  - LLM-reviewed axes (RUNTIME, LEGACY, INTENT): default runs=3

AxisHook is an INTERNAL registration seam for the 5 scheduled axes (D-13,
carry-forward 3). It is NOT a public SPI -- no entry_points, no importlib,
no config-driven plugin discovery.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.scorer import EvalResult
from code_forge.trust import record_trust


# -- Axis-dependent run counts (D-11) -----------------------------------------

DETERMINISTIC_TAGS: frozenset[str] = frozenset({"TRUST", "SEC", "FIXVAL"})
"""Axis tags that produce deterministic results -- default to 1 run."""

_DEFAULT_LLM_RUNS = 3
"""Default run count for LLM-reviewed axes (RUNTIME, LEGACY, INTENT)."""


# -- Axis hook seam (D-13, carry-forward 3) ------------------------------------


class AxisHook:
    """Hook for axis-specific pre/post review logic.

    Internal registration seam for scheduled axes (Phases 18-22).
    NOT a public/third-party plugin SPI.
    """

    def pre_review(self, entry: CorpusEntry) -> None:
        """Called before each replay run. Override in subclass."""

    def post_review(self, entry: CorpusEntry, result: EvalResult) -> None:
        """Called after each replay run. Override in subclass."""


_AXIS_HOOKS: list[AxisHook] = []
"""Module-level hook list. Internal only."""


def register_axis_hook(hook: AxisHook) -> None:
    """Register an axis hook for pre/post review callbacks.

    Appends to the module-level list. No entry_points, no importlib,
    no config-driven plugin discovery (carry-forward 3).
    """
    _AXIS_HOOKS.append(hook)


# -- Pipeline replay -----------------------------------------------------------


def _default_runs(entry: CorpusEntry) -> int:
    """Determine default run count from axis tags (D-11).

    If any tag is in DETERMINISTIC_TAGS, default to 1 run.
    Otherwise (LLM-reviewed), default to 3 runs.
    """
    if any(tag in DETERMINISTIC_TAGS for tag in entry.axis_tags):
        return 1
    return _DEFAULT_LLM_RUNS


def _create_gate_yaml(repo_dir: Path, backend_name: str) -> Path:
    """Create a minimal gate.yaml in the temp repo for eval."""
    gate_dir = repo_dir / ".code-forge"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate_path = gate_dir / "gate.yaml"
    gate_data = {
        "backends": {
            backend_name: {
                "type": "api",
                "format": "openai",
                "base_url": "http://localhost:0/v1",
                "model": "eval-placeholder",
            },
        },
    }
    gate_path.write_text(
        yaml.dump(gate_data, default_flow_style=False),
        encoding="utf-8",
    )
    return gate_path


def replay_entry(
    entry: CorpusEntry,
    corpus_dir: Path,
    backend_name: str,
    runs: Optional[int] = None,
) -> EvalResult:
    """Run a single corpus entry through code-forge review via subprocess.

    Each run creates an isolated temp directory with a fresh git repo,
    applies the diff, and invokes code-forge review. Run count is
    axis-dependent per D-11 unless overridden with ``runs``.

    Args:
        entry: corpus entry to evaluate.
        corpus_dir: directory containing the corpus manifest and diff files.
        backend_name: backend to use for review.
        runs: override run count (None = axis-dependent default).

    Returns:
        EvalResult with actual verdict, run count, and caught count.
    """
    # Check diff file exists
    diff_path = corpus_dir / entry.diff_file
    if not diff_path.exists():
        return EvalResult(
            entry=entry,
            actual_verdict="SKIPPED",
            runs=0,
            caught_count=0,
            skipped_reason="diff file not found: %s" % entry.diff_file,
        )

    # Determine run count
    num_runs = runs if runs is not None else _default_runs(entry)
    caught_count = 0

    # Call pre_review hooks
    for hook in _AXIS_HOOKS:
        hook.pre_review(entry)

    for _ in range(num_runs):
        temp_dir = tempfile.mkdtemp(prefix="forge-eval-")
        try:
            flagged, skip_reason = _run_single(
                entry, diff_path, temp_dir, backend_name,
            )
            if skip_reason:
                # SKIPPED -- return immediately
                eval_result = EvalResult(
                    entry=entry,
                    actual_verdict="SKIPPED",
                    runs=num_runs,
                    caught_count=caught_count,
                    skipped_reason=skip_reason,
                )
                # Call post_review hooks
                for hook in _AXIS_HOOKS:
                    hook.post_review(entry, eval_result)
                return eval_result
            if flagged:
                caught_count += 1
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Determine actual verdict (D-11 majority vote for multi-run)
    threshold = math.ceil(num_runs / 2) if num_runs > 1 else 1
    if caught_count >= threshold:
        actual_verdict = "HOLD"
    else:
        actual_verdict = "PASS"

    eval_result = EvalResult(
        entry=entry,
        actual_verdict=actual_verdict,
        runs=num_runs,
        caught_count=caught_count,
        skipped_reason="",
    )

    # Call post_review hooks
    for hook in _AXIS_HOOKS:
        hook.post_review(entry, eval_result)

    return eval_result


def _run_single(
    entry: CorpusEntry,
    diff_path: Path,
    temp_dir: str,
    backend_name: str,
) -> tuple[bool, str]:
    """Run one replay pass in an isolated temp directory.

    Returns:
        Tuple of (flagged, skip_reason). If skip_reason is non-empty,
        the run was skipped (apply error or timeout). If skip_reason is
        empty, flagged indicates whether forge flagged the entry.
    """
    repo_path = Path(temp_dir)

    # Initialize git repo
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=temp_dir, capture_output=True, check=False,
    )
    subprocess.run(
        ["git", "-c", "user.name=eval", "-c", "user.email=eval@test",
         "commit", "--allow-empty", "-m", "init"],
        cwd=temp_dir, capture_output=True, check=False,
    )

    # Create gate.yaml and trust it
    gate_path = _create_gate_yaml(repo_path, backend_name)

    # Set XDG_CONFIG_HOME to isolate trust state
    xdg_dir = repo_path / ".xdg-config"
    xdg_dir.mkdir(parents=True, exist_ok=True)
    eval_env = os.environ.copy()
    eval_env["XDG_CONFIG_HOME"] = str(xdg_dir)

    # Read gate data and record trust
    gate_data = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
    # Temporarily set XDG_CONFIG_HOME for record_trust
    old_xdg = os.environ.get("XDG_CONFIG_HOME")
    try:
        os.environ["XDG_CONFIG_HOME"] = str(xdg_dir)
        record_trust(gate_path, gate_data)
    finally:
        if old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_xdg

    # Apply the diff (note: no --allow-empty flag, that is git-commit only)
    apply_result = subprocess.run(
        ["git", "apply", str(diff_path.resolve())],
        cwd=temp_dir, capture_output=True, check=False,
    )
    if apply_result.returncode != 0:
        stderr_text = apply_result.stderr
        if isinstance(stderr_text, bytes):
            stderr_text = stderr_text.decode("utf-8", errors="replace")
        return False, "git apply failed: %s" % stderr_text

    # Run code-forge review
    try:
        review_result = subprocess.run(
            ["code-forge", "review", "--backend", backend_name],
            cwd=temp_dir, timeout=300,
            capture_output=True, check=False,
            env=eval_env,
        )
        # exit 0 = PASS, non-zero = flagged (HOLD/FAIL)
        return review_result.returncode != 0, ""
    except subprocess.TimeoutExpired:
        return False, "code-forge review timeout after 300s"

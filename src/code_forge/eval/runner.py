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

Advisory scoring (D-06/D-12):
  - After each _run_single call, BEFORE temp dir cleanup, reads
    advisory-findings.json from the temp dir.
  - Concatenates description text of findings whose id != "runtime-smoke-summary"
    (GM-R6: surface names in summary would false-positive keyword matching).
  - Calls advisory_caught(concat_text, entry.expected_advisory) per-run.
  - Accumulates advisory_hit_count; sets EvalResult.advisory_caught_count.
  - advisory_caught_count is SEPARATE from caught_count; never affects
    actual_verdict computation (DS-R3).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.scorer import EvalResult, advisory_caught
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


class FixvalAxisHook(AxisHook):
    """FIXVAL eval axis hook: scores fix-validation results.

    pre_review: no-op (FIXVAL runs inside forge's pipeline, not at
    the eval layer).

    post_review: if entry has "FIXVAL" in axis_tags, checks the
    actual verdict. HOLD (forge blocked) -> scored as caught.
    PASS (forge did not block) -> scored as missed (false-green).
    The hook trusts forge's internal FIXVAL gate output.
    """

    def pre_review(self, entry: CorpusEntry) -> None:
        """No-op: FIXVAL runs inside forge's pipeline."""

    def post_review(self, entry: CorpusEntry, result: EvalResult) -> None:
        """Score FIXVAL axis results on entries with FIXVAL tag."""
        if "FIXVAL" not in entry.axis_tags:
            return
        # Logging only -- the actual scoring is done by the scorer
        # module using the EvalResult. This hook exists for future
        # axis-specific post-processing (e.g., recording which
        # specific FIXVAL sub-check triggered the verdict).


register_axis_hook(FixvalAxisHook())



class RuntimeAxisHook(AxisHook):
    """RUNTIME eval axis hook: advisory content-match scoring (D-06/D-12).

    pre_review: no-op (no per-entry setup needed for RUNTIME advisory).

    post_review: no-op (advisory scoring is handled in the runner's
    per-run loop BEFORE temp dir cleanup, not in hooks which run after
    EvalResult is already computed). This hook exists for registration
    confirmation and future axis-specific post-processing.

    Scoring architecture (GM-R4/Kimi-R2): post_review runs after EvalResult
    is constructed and the temp dir is cleaned up, so it cannot read
    advisory-findings.json. Advisory scoring must happen in the per-run
    loop inside replay_entry(), BEFORE shutil.rmtree().
    """

    def pre_review(self, entry: CorpusEntry) -> None:
        """No-op: no per-entry setup needed for RUNTIME advisory axis."""

    def post_review(self, entry: CorpusEntry, result: EvalResult) -> None:
        """No-op: advisory scoring is done in the runner per-run loop."""


register_axis_hook(RuntimeAxisHook())


# -- Advisory findings reading -------------------------------------------------


def _read_advisory_findings(temp_dir: str) -> list[dict]:
    """Read advisory-findings.json from temp review directory.

    Returns list of finding dicts. Empty list if file absent or malformed.
    Called BEFORE temp dir cleanup (shutil.rmtree) in the per-run loop.
    """
    findings_path = Path(temp_dir) / "advisory-findings.json"
    if not findings_path.exists():
        return []
    try:
        data = json.loads(findings_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _concat_advisory_text(findings: list[dict]) -> str:
    """Concatenate advisory finding descriptions, excluding runtime-smoke-summary.

    Excludes findings with id == "runtime-smoke-summary" (GM-R6: the summary
    finding contains surface names that would false-positive keyword matching
    in eval scoring -- e.g., "NOT VERIFIED: [nftables]" would match the
    "nftables" keyword even if the LLM found no stale-nftables risk).

    Args:
        findings: list of advisory finding dicts from advisory-findings.json.

    Returns:
        Space-joined description strings, excluding the smoke summary finding.
    """
    parts: list[str] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        if f.get("id") == "runtime-smoke-summary":
            continue
        desc = str(f.get("description", ""))
        if desc:
            parts.append(desc)
    return " ".join(parts)


# -- Infra failure detection ----------------------------------------------------

_INFRA_PATTERNS: tuple[str, ...] = (
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ECONNREFUSED",
    "Connection refused",
    "Connection timed out",
    "Read timed out",
    "No such backend",
    "Backend not found",
    "APIConnectionError",
)


def _is_infra_failure(stderr: str) -> bool:
    """Detect backend/infra failures vs review findings.

    Backend down or connection refused must score SKIPPED/ERROR, never
    "caught". Only pattern-match stderr; a review that exits non-zero
    with findings is NOT an infra failure.
    """
    lower = stderr.lower()
    return any(pat.lower() in lower for pat in _INFRA_PATTERNS)


# -- Pipeline replay -----------------------------------------------------------


def _default_runs(entry: CorpusEntry) -> int:
    """Determine default run count from axis tags (D-11).

    If any tag is in DETERMINISTIC_TAGS, default to 1 run.
    Otherwise (LLM-reviewed), default to 3 runs.
    """
    if any(tag in DETERMINISTIC_TAGS for tag in entry.axis_tags):
        return 1
    return _DEFAULT_LLM_RUNS


def _create_gate_yaml(
    repo_dir: Path,
    backend_name: str,
    backend_config: Optional[dict] = None,
) -> Path:
    """Create or merge harness gate.yaml in the temp repo for eval.

    If gate.yaml already exists (e.g., from the applied diff), merge the
    harness backend into it. The harness backend config wins if the diff
    created one with the same name.
    """
    gate_dir = repo_dir / ".code-forge"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate_path = gate_dir / "gate.yaml"

    existing: dict = {}
    if gate_path.exists():
        loaded = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded

    if backend_config is not None:
        harness_backend = dict(backend_config)
    else:
        harness_backend = {
            "type": "api",
            "format": "openai",
            "base_url": "http://localhost:0/v1",
            "model": "eval-placeholder",
        }

    backends = existing.get("backends", {})
    if not isinstance(backends, dict):
        backends = {}
    backends[backend_name] = harness_backend
    existing["backends"] = backends

    gate_path.write_text(
        yaml.dump(existing, default_flow_style=False),
        encoding="utf-8",
    )
    return gate_path


def replay_entry(
    entry: CorpusEntry,
    corpus_dir: Path,
    backend_name: str,
    runs: Optional[int] = None,
    backend_config: Optional[dict] = None,
) -> EvalResult:
    """Run a single corpus entry through code-forge review via subprocess.

    Each run creates an isolated temp directory with a fresh git repo,
    applies the diff, and invokes code-forge review. Run count is
    axis-dependent per D-11 unless overridden with ``runs``.

    Advisory scoring (D-06/D-12): for entries with expected_advisory, reads
    advisory-findings.json from the temp dir BEFORE cleanup, concatenates
    descriptions (excluding runtime-smoke-summary), and calls advisory_caught()
    per-run. The per-run advisory hit count is stored in advisory_caught_count
    on the final EvalResult; it does NOT affect caught_count or actual_verdict.

    Args:
        entry: corpus entry to evaluate.
        corpus_dir: directory containing the corpus manifest and diff files.
        backend_name: backend to use for review.
        runs: override run count (None = axis-dependent default).
        backend_config: optional backend config dict.

    Returns:
        EvalResult with actual verdict, run count, caught count, and
        advisory_caught_count.
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
    advisory_hit_count = 0

    # Call pre_review hooks
    for hook in _AXIS_HOOKS:
        hook.pre_review(entry)

    for _ in range(num_runs):
        temp_dir = tempfile.mkdtemp(prefix="forge-eval-")
        try:
            flagged, skip_reason = _run_single(
                entry, diff_path, temp_dir, backend_name,
                backend_config, corpus_dir,
            )
            if skip_reason:
                # SKIPPED -- return immediately
                eval_result = EvalResult(
                    entry=entry,
                    actual_verdict="SKIPPED",
                    runs=num_runs,
                    caught_count=caught_count,
                    skipped_reason=skip_reason,
                    advisory_caught_count=advisory_hit_count,
                )
                # Call post_review hooks
                for hook in _AXIS_HOOKS:
                    hook.post_review(entry, eval_result)
                return eval_result

            # Advisory scoring: read advisory-findings.json BEFORE cleanup (D-06).
            # Only score if entry has expected_advisory keywords.
            if entry.expected_advisory:
                findings = _read_advisory_findings(temp_dir)
                concat_text = _concat_advisory_text(findings)
                if advisory_caught(concat_text, entry.expected_advisory):
                    advisory_hit_count += 1

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
        advisory_caught_count=advisory_hit_count,
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
    backend_config: Optional[dict] = None,
    corpus_dir: Optional[Path] = None,
) -> tuple[bool, str]:
    """Run one replay pass in an isolated temp directory.

    The diff is applied before the harness gate.yaml is written so that
    diffs which create .code-forge/gate.yaml (e.g., gate-yaml-rce) do not
    collide with the harness backend config.

    Returns:
        Tuple of (flagged, skip_reason). If skip_reason is non-empty,
        the run was skipped (apply error or timeout). If skip_reason is
        empty, flagged indicates whether forge flagged the entry.
    """
    repo_path = Path(temp_dir)

    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=temp_dir, capture_output=True, check=False,
    )
    subprocess.run(
        ["git", "-c", "user.name=eval", "-c", "user.email=eval@test",
         "commit", "--allow-empty", "-m", "init"],
        cwd=temp_dir, capture_output=True, check=False,
    )

    if corpus_dir is not None:
        base_dir = corpus_dir / "base_files" / entry.name
        if base_dir.is_dir():
            try:
                shutil.copytree(base_dir, temp_dir, dirs_exist_ok=True)
            except OSError as exc:
                return False, "infra: base_files seed error: %s" % exc
            subprocess.run(
                ["git", "add", "-A"],
                cwd=temp_dir, capture_output=True, check=False,
            )
            subprocess.run(
                ["git", "-c", "user.name=eval", "-c", "user.email=eval@test",
                 "commit", "-m", "seed base files"],
                cwd=temp_dir, capture_output=True, check=False,
            )

    apply_result = subprocess.run(
        ["git", "apply", str(diff_path.resolve())],
        cwd=temp_dir, capture_output=True, check=False,
    )
    if apply_result.returncode != 0:
        stderr_text = apply_result.stderr
        if isinstance(stderr_text, bytes):
            stderr_text = stderr_text.decode("utf-8", errors="replace")
        return False, "git apply failed: %s" % stderr_text

    gate_path = _create_gate_yaml(repo_path, backend_name, backend_config)

    xdg_dir = repo_path / ".xdg-config"
    xdg_dir.mkdir(parents=True, exist_ok=True)
    eval_env = os.environ.copy()
    eval_env["XDG_CONFIG_HOME"] = str(xdg_dir)
    eval_env["FORGE_SKIP_WORKTREE_CHECK"] = "1"

    # Pass xdg_dir directly to avoid mutating os.environ (thread-safe).
    gate_data = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
    record_trust(gate_path, gate_data, config_dir=xdg_dir)

    try:
        review_result = subprocess.run(
            ["code-forge", "review", "--backend", backend_name],
            cwd=temp_dir, timeout=300,
            capture_output=True, check=False,
            env=eval_env,
        )
    except subprocess.TimeoutExpired:
        return False, "infra: code-forge review timeout after 300s"

    stderr_text = review_result.stderr
    if isinstance(stderr_text, bytes):
        stderr_text = stderr_text.decode("utf-8", errors="replace")

    if review_result.returncode != 0 and _is_infra_failure(stderr_text):
        return False, "infra: backend failure: %s" % stderr_text[:200]

    return review_result.returncode != 0, ""

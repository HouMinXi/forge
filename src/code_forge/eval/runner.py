"""Eval pipeline replay runner: per-entry subprocess invocation.

Each corpus entry runs through the COMPLETE forge pipeline via subprocess.
Each run is an independent subprocess invocation with fresh context (separate
temp dir, no shared LLM conversation) to preserve statistical independence
for 2-of-3 majority voting.

Run count is axis-dependent:
  - Deterministic axes (TRUST, SEC, FIXVAL): default runs=1
  - LLM-reviewed axes (RUNTIME, LEGACY, INTENT): default runs=3

AxisHook is an INTERNAL registration seam for the 5 scheduled axes. It is NOT a public SPI -- no entry_points, no importlib,
no config-driven plugin discovery.

Advisory scoring:
  - After each _run_single call, BEFORE temp dir cleanup, reads
    advisory-findings.json from the temp dir.
  - Concatenates description text of findings whose id != "runtime-smoke-summary"
    (surface names in the summary would false-positive keyword matching).
  - Calls advisory_caught(concat_text, entry.expected_advisory) per-run.
  - Accumulates advisory_hit_count; sets EvalResult.advisory_caught_count.
  - advisory_caught_count is SEPARATE from caught_count; never affects
    actual_verdict computation.
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
from code_forge.proc import (
    group_of,
    kill_group_or_child,
    terminate_group_or_child,
)
from code_forge.trust import record_trust


# -- Axis-dependent run counts -----------------------------------------

DETERMINISTIC_TAGS: frozenset[str] = frozenset({"TRUST", "SEC", "FIXVAL"})
"""Axis tags that produce deterministic results -- default to 1 run."""

_DEFAULT_LLM_RUNS = 3
"""Default run count for LLM-reviewed axes (RUNTIME, LEGACY, INTENT)."""

_DEFAULT_REVIEW_TIMEOUT_S = 1800
"""Wall clock a single eval review may take before it is abandoned.

Raised from 300s, which was below the floor for a real multi-round review
and so turned slow backends into skipped entries rather than results. One
call on a reasoning model measured 77-295s here, three passes run per
round, and a review runs several rounds: 300s could not fit even one round
on that backend, and a skip reads as "no data", not as "missed the bug" --
the entry silently leaves the numerator and the denominator.

This bounds a hang, not the work. Set FORGE_EVAL_REVIEW_TIMEOUT_S to
override; values that do not parse as a positive int fall back here.
"""

_STDERR_TAIL_BYTES = 64 * 1024
"""How much of a review's stderr is read back after it exits.

The caller matches infra-failure keywords against this and then keeps 200
characters, so the only thing a larger number buys is the chance to turn
an unbounded file into an unbounded string. 64 KB holds a Python
traceback and a gateway error body several times over.
"""

_TEARDOWN_GRACE_S = 10
"""How long an abandoned review gets to tear itself down after SIGTERM.

Longer than the five seconds the MCP-side teardown allows, because what
has to finish inside this window is itself a SIGTERM, a five-second wait
and then a SIGKILL -- the review signalling the backend CLI it started in
a session this one cannot reach. Five here would end the review in the
middle of that, leaving exactly the process the SIGTERM was for.

Ten seconds against a review already abandoned after half an hour is not
a cost worth tuning.
"""

_REAP_TIMEOUT_S = 5
"""How long to wait for a child that has already been sent SIGKILL.

A reaped child costs nothing and an unreaped one is a zombie for the rest
of the run, so the wait is worth making -- but it is worth bounding, and
the thing it is bounded against is the one child SIGKILL does not reach:
a process stopped in the kernel, in uninterruptible sleep on a mount that
is not answering. That child stays unkillable for as long as the mount
does, and an unbounded wait for it does not fail, it hangs, taking every
review after it in a run that is hundreds long.

So the trade is one leaked zombie against a whole run, and it is not
close. Matching the five the async teardown already allows for the same
wait, because the question is the same and a second number would only
invite the two to drift.
"""


def _review_timeout_s() -> int:
    """Resolve the per-review timeout, honouring FORGE_EVAL_REVIEW_TIMEOUT_S."""
    raw = os.environ.get("FORGE_EVAL_REVIEW_TIMEOUT_S")
    if raw is None:
        return _DEFAULT_REVIEW_TIMEOUT_S
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return _DEFAULT_REVIEW_TIMEOUT_S
    return value if value > 0 else _DEFAULT_REVIEW_TIMEOUT_S


def _run_review(cmd: list[str], cwd: str, env: dict, timeout_s: int):
    """Run one review to completion or abandon it, leaving nothing behind.

    Returns (returncode, stderr_text), or raises subprocess.TimeoutExpired.

    Two things this does that subprocess.run with capture_output cannot.

    Output goes to unlinked temp files rather than pipes. capture_output
    accumulates the child's stream in the parent's memory for as long as
    the child runs, so the ceiling on that buffer is whatever the child
    emits in the timeout window -- a child emitting continuously grew the
    parent by 6.5 GB in two seconds, on a host with less RAM than that, and
    the window is now half an hour rather than five minutes. Disk absorbs
    it instead. The files carry no directory entry, so the kernel reclaims
    them when the handles close whether or not the cleanup path runs.

    The child gets its own process group, which is what makes it safe to
    aim a signal at a group at all: without that, the group is ours and
    the signal comes home. What the group does not buy is reach past the
    review. A review shells out to its backend CLI and starts that CLI in
    a session of its own, so the process actually holding the connection
    is outside the group by the time the timeout fires. Getting to it
    means asking the review to do it, which is why the teardown opens
    with SIGTERM rather than SIGKILL.

    Only the tail of stderr comes back. Reading all of it would undo the
    first half of this: a file the child was free to grow without bound
    becomes a Python string of the same size the moment it is read. The
    tail is the useful end -- a traceback's exception line and a gateway's
    error body both land there -- and the caller classifies the failure by
    keyword and then keeps 200 characters of it.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=out, stderr=err, env=env,
            start_new_session=True,
        )
        try:
            returncode = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            raise
        size = err.seek(0, os.SEEK_END)
        err.seek(max(0, size - _STDERR_TAIL_BYTES))
        return returncode, err.read().decode("utf-8", errors="replace")


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Ask the abandoned review to tear down, then insist, then reap it.

    The signal that matters is the first one, and it is SIGTERM rather
    than SIGKILL for a reason the process group cannot express. A review
    shells out to the backend CLI, and it starts that CLI in a session of
    its own -- deliberately, so that its own teardown can signal the CLI
    together with the shell wrapper around it. The effect here is that
    the CLI is no longer in the group this signals, so a group signal
    reaches the review and nothing beneath it. Measured: killpg returns
    success while the escaped grandchild keeps running, reparented to
    init, holding the backend connection the timeout existed to end.
    Nothing raises, nothing is logged, and the leak is invisible.

    SIGTERM reaches the review's own handler, installed when it imports
    the invoke module, and that handler signals the CLI's group where
    this one cannot. SIGKILL cannot be caught, so it does the opposite of
    what it looks like: the more forceful signal is the one that leaves
    the connection open.

    The grace is longer than the five seconds the async teardown allows,
    because what runs inside it is itself a SIGTERM followed by a
    five-second wait and then a SIGKILL. Cutting that off at five would
    end the review in the middle of its own escalation.

    A review too wedged to run its handler still gets SIGKILL, and that
    case still leaks the grandchild -- there is nothing left to reach it
    with from here, short of walking the process tree.

    The final wait is what reaps the child; without it the direct child
    stays a zombie for the life of the eval run, and an eval run is
    hundreds of reviews. It is bounded anyway, because a child SIGKILL
    cannot reach would otherwise hang the run rather than cost it one
    zombie.

    Which of the group and the child receives each signal is decided in
    proc.py, alongside the async teardown that has to answer the same
    question. What cannot travel with it is the liveness check below:
    Popen.returncode stays None until something calls poll or wait, so
    the test the async side spells as returncode has to be poll here.
    """
    if proc.poll() is not None:
        return

    pgid = group_of(proc)
    terminate_group_or_child(proc, pgid)
    try:
        proc.wait(timeout=_TEARDOWN_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        pass

    kill_group_or_child(proc, pgid)
    try:
        proc.wait(timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass


# -- Axis hook seam ------------------------------------


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
    """RUNTIME eval axis hook: advisory content-match scoring.

    pre_review: no-op (no per-entry setup needed for RUNTIME advisory).

    post_review: no-op (advisory scoring is handled in the runner's
    per-run loop BEFORE temp dir cleanup, not in hooks which run after
    EvalResult is already computed). This hook exists for registration
    confirmation and future axis-specific post-processing.

    Scoring architecture: post_review runs after EvalResult
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

    Excludes findings with id == "runtime-smoke-summary" (the summary
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
    """Determine default run count from axis tags.

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
    axis-dependent unless overridden with ``runs``.

    Advisory scoring: for entries with expected_advisory, reads
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

            # Advisory scoring: read advisory-findings.json BEFORE cleanup.
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

    # Determine actual verdict (majority vote for multi-run)
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

    timeout_s = _review_timeout_s()
    try:
        returncode, stderr_text = _run_review(
            ["code-forge", "review", "--backend", backend_name],
            temp_dir, eval_env, timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, "infra: code-forge review timeout after %ds" % timeout_s

    if returncode != 0 and _is_infra_failure(stderr_text):
        return False, "infra: backend failure: %s" % stderr_text[:200]

    return returncode != 0, ""

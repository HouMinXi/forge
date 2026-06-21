# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Cross-repo merge review orchestration.

Provides diff acquisition, joint context assembly, and per-repo
isolation utilities.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

import yaml

from .diff import get_changed_files
from .git import git_diff, resolve_git_ref


def get_sibling_diff(repo_path: Path, ref_spec: str) -> str:
    """Acquire unified diff for a sibling repo.

    Args:
        repo_path: resolved absolute path to sibling repo dir.
        ref_spec: "baseline..head" format (e.g. "main..feature-x").

    Returns:
        Unified diff string (empty string if no changes).

    Raises:
        ValueError: if ref_spec format is wrong.
        BaselineResolutionError: if a ref does not exist (from git.py).
            Never caught here -- caller sees it directly (fail-closed).
    """
    if ".." not in ref_spec or "..." in ref_spec:
        raise ValueError(
            "ref_spec must be 'baseline..head', got: %r" % ref_spec
        )
    baseline_ref, head_ref = ref_spec.split("..", 1)
    if not baseline_ref or not head_ref:
        raise ValueError(
            "ref_spec must be 'baseline..head', got: %r" % ref_spec
        )
    from .gate_check import _validate_ref_part

    _validate_ref_part("baseline", baseline_ref, "ref_spec")
    _validate_ref_part("head", head_ref, "ref_spec")
    resolve_git_ref(baseline_ref, repo_path)
    resolve_git_ref(head_ref, repo_path)
    return git_diff(baseline_ref, head_ref, [], repo_path)


def build_cross_repo_context(
    repos: list[dict],
) -> str:
    """Assemble joint review context string from per-repo diffs.

    Args:
        repos: list of {"label": str, "ref": str, "diff": str} dicts.

    Returns:
        A string with summary header, per-repo stats, and labeled diff
        blocks.  Empty string if repos list is empty.
    """
    if not repos:
        return ""

    # Summary header
    header = "Cross-repo review: " + " + ".join(
        "%s (%s)" % (r["label"], r["ref"]) for r in repos
    )

    # Per-repo stats and blocks
    stats_lines = []
    blocks = []
    for r in repos:
        label = r["label"]
        ref = r["ref"]
        diff = r["diff"]

        if diff:
            lines = diff.splitlines()
            files_changed = sum(
                1 for line in lines if line.startswith("diff --git ")
            )
            added = sum(
                1 for line in lines
                if line.startswith("+") and not line.startswith("+++")
            )
            removed = sum(
                1 for line in lines
                if line.startswith("-") and not line.startswith("---")
            )
            stats_lines.append(
                "%s: %d file%s changed, +%d/-%d"
                % (
                    label,
                    files_changed,
                    "s" if files_changed != 1 else "",
                    added,
                    removed,
                )
            )
            blocks.append(
                "## Repo: [%s] (%s)\n%s\n" % (label, ref, diff)
            )
        else:
            stats_lines.append("%s: no changes" % label)
            blocks.append(
                "## Repo: [%s] (%s)\n(no changes)\n" % (label, ref)
            )

    return (
        header + "\n"
        + "\n".join(stats_lines) + "\n"
        + "\n".join(blocks)
    )


def make_per_repo_cwd(
    label: str,
    gate_config: dict | None = None,
) -> Path:
    """Create isolated .code-forge/ work dir for a single StateMachine thread.

    Args:
        label: identifier used in the temp dir prefix.
        gate_config: if provided, written as gate.yaml into the
            .code-forge/ subdir (eliminates TOCTOU on disk re-read).

    Returns:
        Path to the temp dir.  Caller must clean up via
        shutil.rmtree or tempfile.TemporaryDirectory.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="forge-cross-%s-" % label))
    code_forge_dir = tmp_dir / ".code-forge"
    code_forge_dir.mkdir()
    if gate_config is not None:
        (code_forge_dir / "gate.yaml").write_text(
            yaml.safe_dump(gate_config, default_flow_style=False)
        )
    return tmp_dir


def derive_source_files(
    repo_path: Path,
    per_repo_diff: str,
) -> list[Path]:
    """Derive changed-file list from a per-repo diff as absolute paths.

    Args:
        repo_path: absolute path to the repo root.
        per_repo_diff: unified diff text for this repo.

    Returns:
        List of absolute Path objects for each changed file.
        Empty list when per_repo_diff is empty (no changes).
    """
    if not per_repo_diff:
        return []
    rel_files = get_changed_files(per_repo_diff)
    return [Path(repo_path / f).resolve() for f in rel_files]


def run_cross_repo(
    *,
    primary_path: Path,
    primary_ref: str,
    primary_label: str,
    siblings: list[dict],
    gate_config: dict,
    mode,
    engine_choice: str,
    backend,
    max_rounds: int,
    max_fix_attempts: int,
    clean_round_threshold: int,
    output_fn=print,
):
    """Orchestrate cross-repo parallel review.

    Acquires diffs for primary + all siblings, assembles joint context,
    launches one StateMachine thread per repo (with per-repo ephemeral
    cwd), collects verdicts, and merges them per the primary-authoritative
    rule.  Per-repo tmp cwds are cleaned up on exit.

    Does NOT acquire ForgeLock.  Cross-repo mode is dispatched before the
    lock site in cli.py.  Threads write to distinct per-repo cwds created
    by make_per_repo_cwd(), so no lock contention is possible.

    Returns the joint Verdict (primary-authoritative).
    """
    from .autofix import StubAutoFixer
    from .baseline import ResolvedReview
    from .factories import build_falsifier, build_l1_provider
    from .llm_invoke import Usage
    from .source import compute_source_hash
    from .state import Verdict

    # -- Step 1: validate siblings (before any git access) --
    from .detect import detect_toolchain
    from .gate_check import validate_siblings

    primary_lang = detect_toolchain(primary_path).language
    # Narrow base: primary_path / ".code-forge" gives
    # gate_root = primary_path -- same containment boundary as
    # the dispatch caller and load_gate_config.  Makes
    # run_cross_repo self-authoritative for path safety.
    validate_siblings(
        siblings,
        gate_yaml_dir=primary_path / ".code-forge",
        primary_language=primary_lang,
    )

    # -- Step 2: acquire diffs (fail-closed on bad ref) --
    repo_entries = []
    primary_diff = get_sibling_diff(primary_path, primary_ref)
    repo_entries.append({
        "label": primary_label,
        "repo_path": primary_path,
        "ref": primary_ref,
        "diff": primary_diff,
    })
    for sib in siblings:
        sib_path = Path(sib["repo"]).resolve()
        sib_ref = sib["ref"]
        sib_label = sib.get("label") or os.path.basename(
            sib["repo"].rstrip("/")
        )
        sib_diff = get_sibling_diff(sib_path, sib_ref)
        repo_entries.append({
            "label": sib_label,
            "repo_path": sib_path,
            "ref": sib_ref,
            "diff": sib_diff,
        })

    # -- Step 3: assemble joint context --
    repos_data = [
        {"label": e["label"], "ref": e["ref"], "diff": e["diff"]}
        for e in repo_entries
    ]
    joint_diff = build_cross_repo_context(repos_data)

    # -- Step 3b: load contract spec for primary repo (D-06 amended) --
    _contract_spec = ""
    _contracts_yaml = primary_path / ".code-forge" / "contracts.yaml"
    if _contracts_yaml.is_file():
        from .contract_loader import load_contract_digest
        _contract_spec = load_contract_digest(
            _contracts_yaml, primary_path, backend=backend,
        )

    # -- Step 4: build per-repo cwds (cleanup via ExitStack) --
    # -- Step 5: launch threads --
    # -- Step 6-9: collect, merge, return --
    results: dict[str, Verdict] = {}
    errors: dict[str, Exception] = {}
    per_repo_findings: dict[str, list[dict]] = {}

    with contextlib.ExitStack() as stack:
        thread_args = []
        for entry in repo_entries:
            label = entry["label"]
            is_primary = label == primary_label
            cwd = make_per_repo_cwd(
                label,
                gate_config=gate_config if is_primary else None,
            )
            stack.callback(shutil.rmtree, cwd, True)
            thread_args.append((
                label, entry["repo_path"], entry["diff"], cwd, is_primary,
            ))

        def _thread_fn(label, repo_path, diff_text, per_cwd, is_primary):
            try:
                source_files = derive_source_files(repo_path, diff_text)

                # StateMachine gets per-repo raw diff (not joint context)
                resolved_for_sm = ResolvedReview(
                    source_files=source_files,
                    baseline_content=None,
                    git_diff=diff_text,
                    mode_hint="git",
                )
                source_hash = compute_source_hash(git_diff=diff_text)

                if is_primary:
                    # L1 sees the joint cross-repo context
                    resolved_for_l1 = ResolvedReview(
                        source_files=source_files,
                        baseline_content=None,
                        git_diff=joint_diff,
                        mode_hint="git",
                    )
                    from .machine import TimeoutCircuitBreaker
                    breaker = TimeoutCircuitBreaker(threshold=5)

                    l1_provider = build_l1_provider(
                        engine_choice, resolved_for_l1, backend=backend,
                        breaker=breaker,
                        contract_spec=_contract_spec,
                    )
                    from .daemon_state import DaemonStateRunner
                    from .graph_triage import GraphTriageRunner
                    from .legacy import LegacyRunner
                    from .runtime import RuntimeRunner
                    from .taint import TaintRunner

                    advisory_runners = [
                        TaintRunner(),
                        RuntimeRunner(backend=backend),
                        GraphTriageRunner(),
                        DaemonStateRunner(backend=backend),
                        LegacyRunner(),
                    ]
                else:
                    # Siblings: no L1 cost, no advisory runners
                    l1_provider = lambda: ([], [], Usage(), 0.0)
                    advisory_runners = []

                falsifier = build_falsifier(engine_choice, backend=backend)

                from .machine import StateMachine

                sm = StateMachine(
                    mode=mode,
                    falsifier=falsifier,
                    autofixer=StubAutoFixer(),
                    revert_fn=lambda f: None,
                    resolved_review=resolved_for_sm,
                    source_hash=source_hash,
                    baseline_spec_repr=label,
                    cwd=per_cwd,
                    registry={},
                    l1_provider=l1_provider,
                    advisory_runners=advisory_runners,
                    max_total_rounds=max_rounds,
                    max_fix_attempts=max_fix_attempts,
                    clean_round_threshold=clean_round_threshold,
                )
                verdict = sm.run()
                results[label] = verdict
            except Exception as exc:
                errors[label] = exc

        threads = [
            threading.Thread(
                target=_thread_fn, args=args, daemon=True,
            )
            for args in thread_args
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # -- Step 6: fail-closed on PRIMARY error; sibling errors are advisory --
        if primary_label in errors:
            raise errors[primary_label]
        for label, exc in errors.items():
            # Sibling crash -> treat as FAIL verdict with warning, not
            # a hard abort.  Sibling failures are advisory (primary
            # is authoritative for the joint verdict).
            results[label] = Verdict.FAIL
            output_fn(
                "[cross-repo] WARNING: sibling %r crashed: %s"
                % (label, exc)
            )

        # -- Step 7: verdict merge + PENDING guard --
        primary_verdict = results[primary_label]
        if primary_verdict == Verdict.PENDING:
            output_fn(
                "[cross-repo] primary returned PENDING (HOLD); "
                "cross-repo does not support interactive HOLD "
                "-- treating as FAIL"
            )
            primary_verdict = Verdict.FAIL

        sibling_fails = [
            label for label, v in results.items()
            if label != primary_label
            and v in (Verdict.FAIL, Verdict.ESCALATED)
        ]
        if sibling_fails:
            output_fn(
                "[cross-repo] WARNING: sibling(s) %s have findings "
                "-- see per-repo receipts" % sibling_fails
            )

        # -- Step 8: collect per-repo receipts --
        primary_receipts_dest = primary_path / ".code-forge"
        primary_receipts_dest.mkdir(parents=True, exist_ok=True)
        for label, _repo_path, _diff, per_cwd, _is_primary in thread_args:
            receipts_dir = per_cwd / ".code-forge" / "receipts"
            if not receipts_dir.is_dir():
                per_repo_findings[label] = []
                continue
            receipts = sorted(receipts_dir.glob("receipt-*.json"))
            findings = []
            for r in receipts:
                dst = primary_receipts_dest / ("%s-%s" % (label, r.name))
                shutil.copy2(r, dst)
                data = json.loads(r.read_text())
                findings.extend(data.get("findings", []))
            per_repo_findings[label] = findings

    # -- Step 9: grouped output (after receipts are fully collected) --
    ordered_labels = [primary_label] + [
        s.get("label") or os.path.basename(s["repo"].rstrip("/"))
        for s in siblings
    ]
    format_cross_repo_output(per_repo_findings, ordered_labels, output_fn)

    # -- Step 10: return joint verdict --
    return primary_verdict


def format_cross_repo_output(
    per_repo_findings: dict,
    ordered_labels: list,
    output_fn=print,
) -> None:
    """Emit grouped verdict output: findings under their repo's section header.

    Each label in ordered_labels gets a === [label] === header.  Findings
    from per_repo_findings[label] appear under that header in
    [label] file:line -- description format.  A label with no findings
    still gets its header (no body lines, no exception).
    """
    for label in ordered_labels:
        output_fn("=== [%s] ===" % label)
        for finding in per_repo_findings.get(label, []):
            file_ref = finding.get("file", "?")
            line_ref = finding.get("line", 0)
            desc = finding.get("description", "")
            output_fn("[%s] %s:%s -- %s" % (label, file_ref, line_ref, desc))

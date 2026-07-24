# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""LegacyRunner advisory axis (REVIEW-LEGACY-01 + REVIEW-INTENT-01).

Detects pre-existing L0 issues in files the diff touches, annotates each
with git-blame attribution, and classifies each as "intended" (SATD /
commit signal) or "unintended". Advisory only -- never blocks convergence.

Follows TaintRunner / RuntimeRunner structural model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .advisory import AdvisoryFinding
from .diff import extract_changed_lines
from .git import git_blame
from .state import StateFinding

# ---------------------------------------------------------------------------
# SATD keywords (in-code markers indicating known technical debt)
# ---------------------------------------------------------------------------
SATD_KEYWORDS = frozenset({
    "todo", "fixme", "hack", "workaround", "xxx", "kludge",
})

# ---------------------------------------------------------------------------
# Commit-message intent signals (substring match, ~55% precision)
# ---------------------------------------------------------------------------
INTENT_SIGNALS = frozenset({
    "workaround", "hack", "temp", "fixme", "known-issue",
    "known issue", "legacy", "grandfather", "suppress", "intentional",
})


def _build_legacy_skipped(reason: str) -> AdvisoryFinding:
    """Build a SKIPPED AdvisoryFinding (never-silent pattern from RuntimeRunner)."""
    return AdvisoryFinding(
        id="legacy-skipped",
        axis="legacy",
        file="",
        line_range=[0, 0],
        description="LEGACY axis SKIPPED: %s" % reason,
        attribution="legacy-axis/infra-error",
    )


def _classify_intent(
    commit_subject: str,
    source_lines: dict[int, str],
    finding_line: int,
) -> str:
    """Classify a pre-existing finding as intended or unintended.

    Checks commit message for intent signals, then surrounding source
    lines (+/-3) for SATD keywords. ~55% precision is accepted.

    Args:
        commit_subject: first line of the blame commit message.
        source_lines: {line_no: line_text} dict for the file.
        finding_line: 1-indexed line number of the finding.

    Returns:
        "intended" or "unintended".
    """
    # Check commit subject for intent signals.
    subj_norm = commit_subject.lower().replace("-", " ").replace("_", " ")
    for signal in INTENT_SIGNALS:
        sig_norm = signal.replace("-", " ")
        # Guard semantic flips: "un-X" contains "X" as substring.
        if sig_norm == "intentional" and "unintentional" in subj_norm:
            continue
        if sig_norm == "known issue" and "unknown issue" in subj_norm:
            continue
        if sig_norm in subj_norm:
            return "intended"

    # Check surrounding lines for SATD keywords.
    for line_no in range(max(1, finding_line - 3), finding_line + 4):
        line_text = source_lines.get(line_no, "").lower()
        for keyword in SATD_KEYWORDS:
            if keyword in line_text:
                return "intended"

    return "unintended"


class LegacyRunner:
    """Advisory axis: pre-existing finding detection with blame + intent.

    Satisfies the AxisRunner Protocol (is_advisory=True).
    machine.py sets source_files and registry before dispatch.
    """

    def __init__(self, l0_runner: Optional[Callable] = None) -> None:
        self.source_files: Optional[list[Path]] = None
        self.registry: Optional[dict] = None
        self.infra_errors: list[str] = []
        self._l0_runner: Optional[Callable] = l0_runner

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle counter."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run legacy detection on diff-touched files.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding for pre-existing issues found.
        """
        # Step 1: clear infra_errors, resolve repo_root.
        self.infra_errors.clear()
        repo_root = repo_root.resolve()

        # Step 2: guard empty diff.
        if not diff_text or not diff_text.strip():
            return []

        # Step 3: guard no source_files.
        if self.source_files is None or len(self.source_files) == 0:
            return [_build_legacy_skipped("no source_files provided")]

        # Step 4: guard no registry.
        if self.registry is None:
            return [_build_legacy_skipped("registry not injected")]

        # Step 5: extract changed lines (with absolute-path expansion).
        changed_lines = extract_changed_lines(
            diff_text, repo_root=repo_root
        )
        if not changed_lines:
            return []

        # Step 6: resolve l0_runner callable.
        runner_fn = self._l0_runner

        # Step 7: run L0 tools.
        try:
            if runner_fn is None:
                from .machine import _default_l0_runner
                runner_fn = _default_l0_runner
            l0_findings, l0_infra = runner_fn(
                self.registry, list(self.source_files)
            )
        except Exception as exc:
            msg = "L0 re-run failed: %r" % exc
            self.infra_errors.append(msg)
            return [_build_legacy_skipped(msg)]

        if l0_infra:
            self.infra_errors.extend(l0_infra)

        # Step 8: extract_changed_lines already registered absolute
        # keys when repo_root was passed in Step 5.
        changed_lines_norm = changed_lines

        # Step 9: manual line-intersection (replaces filter_delta).
        changed_files_set = set(changed_lines_norm.keys())
        delta_ids: set[str] = set()
        for sf in l0_findings:
            if not isinstance(sf, StateFinding) or not sf.line_range:
                continue
            lines = changed_lines_norm.get(sf.file, set())
            if lines:
                start = sf.line_range[0]
                end = sf.line_range[-1] if len(sf.line_range) > 1 else start
                if any(ln in lines for ln in range(start, end + 1)):
                    delta_ids.add(sf.id)

        pre_existing = [
            sf for sf in l0_findings
            if isinstance(sf, StateFinding)
            and sf.line_range
            and sf.id not in delta_ids
            and sf.file in changed_files_set
        ]

        # Step 10: nothing pre-existing found.
        if not pre_existing:
            return []

        # Step 11: build advisories with blame + intent.
        advisories: list[AdvisoryFinding] = []
        blame_cache: dict[str, dict[int, dict]] = {}
        source_lines_cache: dict[str, dict[int, str]] = {}

        for sf in pre_existing:
            # Normalize sf.file to repo-relative for git blame.
            blame_key = sf.file
            if Path(sf.file).is_absolute():
                try:
                    blame_key = str(Path(sf.file).relative_to(repo_root))
                except ValueError:
                    pass

            # Cache blame per file.
            if blame_key not in blame_cache:
                blame_cache[blame_key] = git_blame(blame_key, repo_root)

            # Cache source lines per file.
            if blame_key not in source_lines_cache:
                try:
                    abs_path = repo_root / blame_key
                    source_lines_cache[blame_key] = {
                        i + 1: line
                        for i, line in enumerate(
                            abs_path.read_text(
                                encoding="utf-8", errors="replace"
                            ).splitlines()
                        )
                    }
                except OSError:
                    source_lines_cache[blame_key] = {}

            source_lines = source_lines_cache[blame_key]
            line_no = sf.line_range[0]

            # Build attribution from blame.
            blame_entry = blame_cache[blame_key].get(line_no, {})
            sha = blame_entry.get("sha", "")
            if sha == "0" * 40:
                attribution = "git-blame: uncommitted staged change"
            elif sha:
                parts = [
                    blame_entry.get("author", "unknown"),
                    sha[:8],
                    blame_entry.get("date", ""),
                    blame_entry.get("subject", ""),
                ]
                attribution = "git-blame: " + " ".join(
                    p for p in parts if p
                )
            else:
                attribution = "git-blame: unavailable"

            # Classify intent.
            intent = _classify_intent(
                blame_entry.get("subject", ""), source_lines, line_no
            )

            # Build finding ID.
            rule_hint = sf.description[:16].replace(" ", "_")
            finding_id = "legacy:%s:%d:%s" % (blame_key, line_no, rule_hint)

            # Build description.
            description = "[pre-existing] %s [intent: %s]" % (
                sf.description or "",
                intent,
            )

            advisories.append(
                AdvisoryFinding(
                    id=finding_id,
                    axis="legacy",
                    file=sf.file,
                    line_range=sf.line_range,
                    description=description,
                    attribution=attribution,
                )
            )

        # Step 12: return advisories.
        return advisories

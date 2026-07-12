# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""DaemonStateRunner advisory axis: cross-subsystem state-conflict detection.

Detects when daemon/service code mutates shared external state (nftables marks,
routing rules, locks, PID files) that another concurrently-active subsystem
depends on.

Three capabilities:
  (a) Heuristic fallback: when no daemon_state in gate.yaml, scans diff for
      stateful keywords and emits a one-line advisory.
  (b) Static conflict rules: gate.yaml daemon_state.conflicts triplets
      matched against diff content (static rules first, LLM supplements).
  (c) Two-step LLM call: Q1 enumerates external state, grep repo for
      context, Q2+Q3 analyze conflicts with grep context.

Full axis does NOT run without explicit opt-in (daemon_state in gate.yaml).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import yaml

from .advisory import AdvisoryFinding
from .llm_invoke import LLMInvokeError, llm_invoke


# ---------------------------------------------------------------------------
# Default keyword set (narrow + extensible via gate.yaml patterns)
# ---------------------------------------------------------------------------

DEFAULT_DAEMON_KEYWORDS: frozenset[str] = frozenset({
    "nft", "iptables", "ip route", "systemctl", "firewall-cmd", "tc",
})


# ---------------------------------------------------------------------------
# Exact question wording (locked constants)
# SKILL.md carries a verbatim mirror; drift test asserts equality.
# ---------------------------------------------------------------------------

DAEMON_STATE_Q1 = (
    "You are reviewing a code diff for cross-subsystem state conflicts.\n\n"
    "Enumerate every piece of external state (nftables marks, routing rules, "
    "locks, PID files, shared sockets) this diff creates, modifies, or deletes. "
    "For each, list ALL possible values at call time.\n\n"
    "Return your answer as JSON with exactly this key:\n"
    '{"external_state": ["state item 1", "state item 2"]}\n\n'
    "If no external state is affected, return: "
    '{"external_state": []}\n\n'
    "%(runtime_surfaces)s"
    "Diff:\n%(diff_text)s"
)

DAEMON_STATE_Q2Q3 = (
    "You are reviewing a code diff for cross-subsystem state conflicts.\n\n"
    "For each external state item identified:\n"
    "Q2: For each external state item above, which OTHER subsystem or function "
    "(not in this diff) reads, writes, or depends on that same state? Name the "
    "subsystem and the specific operation.\n"
    "Q3: For each (state, subsystem) pair with a conflict: describe the concrete "
    "failure scenario -- what happens when both subsystems run concurrently and "
    "the state values collide or are consumed out of order?\n\n"
    "%(static_rules)s"
    "Grep context from the repository:\n%(grep_context)s\n\n"
    "Return your answer as JSON with exactly this key:\n"
    '{"conflicts": [{"subsystem": "name", "mutates": "what", '
    '"interferes_with": "what", "scenario": "description"}]}\n\n'
    "If no conflicts found, return: "
    '{"conflicts": []}\n\n'
    "Diff:\n%(diff_text)s"
)

# Max bytes of grep output to inject into Q2Q3 prompt.
_MAX_GREP_OUTPUT_BYTES = 50000


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _diff_contains_keywords(diff_text: str, keywords: frozenset[str]) -> bool:
    """Check if any keyword appears in added lines of the diff (case-insensitive).

    Only scans lines starting with '+' (added lines in unified diff format).
    """
    for line in diff_text.splitlines():
        if not line.startswith("+"):
            continue
        lower_line = line.lower()
        for kw in keywords:
            if kw.lower() in lower_line:
                return True
    return False


def _extract_grep_keywords(state_items: list) -> list[str]:
    """Extract searchable identifiers from Q1 response items.

    Strips empty/whitespace entries. Returns unique non-empty strings.
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in state_items:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _grep_repo(
    keywords: list[str],
    repo_root: Path,
    top_k: int = 5,
    context_lines: int = 5,
) -> str:
    """Grep repo for keywords, return relevance-ranked context.

    subprocess.run with list args, no shell=True.
    sort by hit count, take top-K files, extract context.
    """
    if not keywords:
        return ""

    file_hits: dict[str, int] = {}
    for keyword in keywords:
        if not keyword or not keyword.strip():
            continue
        try:
            result = subprocess.run(
                ["grep", "-rn",
                 "--include=*.py", "--include=*.sh",
                 "--include=*.yaml", "--include=*.yml",
                 keyword.strip(), str(repo_root)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            for line in result.stdout.splitlines():
                parts = line.split(":", 2)
                if len(parts) >= 2:
                    fpath = parts[0]
                    file_hits[fpath] = file_hits.get(fpath, 0) + 1
        except (subprocess.TimeoutExpired, OSError):
            continue

    # Sort by hit count descending, take top-K
    ranked = sorted(file_hits.items(), key=lambda x: x[1], reverse=True)[:top_k]

    # Extract context around matches
    context_parts: list[str] = []
    total_bytes = 0
    for fpath, _ in ranked:
        for keyword in keywords:
            if not keyword or not keyword.strip():
                continue
            if total_bytes >= _MAX_GREP_OUTPUT_BYTES:
                context_parts.append("[truncated]")
                return "\n".join(context_parts)
            try:
                result = subprocess.run(
                    ["grep", "-n", "-C", str(context_lines),
                     keyword.strip(), fpath],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                )
                if result.stdout.strip():
                    chunk = "--- %s ---\n%s" % (fpath, result.stdout)
                    total_bytes += len(chunk.encode("utf-8", errors="replace"))
                    context_parts.append(chunk)
            except (subprocess.TimeoutExpired, OSError):
                continue

    return "\n".join(context_parts)


def _match_static_rules(
    conflicts: list[dict],
    diff_text: str,
) -> list[AdvisoryFinding]:
    """Check each conflict triplet against diff content.

    triplet shape {subsystem, mutates, interferes_with}.
    description formatted as
    "[subsystemA] mutates X; [subsystemB] depends on Y -> conflict scenario".
    """
    findings: list[AdvisoryFinding] = []
    diff_lower = diff_text.lower()

    for idx, rule in enumerate(conflicts):
        if not isinstance(rule, dict):
            continue
        subsystem = str(rule.get("subsystem", ""))
        mutates = str(rule.get("mutates", ""))
        interferes = str(rule.get("interferes_with", ""))

        # Match if the mutated state appears in the diff
        if mutates.lower() in diff_lower:
            description = (
                "[%s] mutates %s; interferes with %s"
                % (subsystem, mutates, interferes)
            )
            findings.append(AdvisoryFinding(
                id="daemon-state-static-%d" % idx,
                axis="DAEMON-STATE",
                file="",
                line_range=[0, 0],
                description=description,
                attribution="daemon-state/static-rule",
            ))

    return findings


def _build_skipped_finding(reason: str) -> AdvisoryFinding:
    """Build a SKIPPED AdvisoryFinding."""
    return AdvisoryFinding(
        id="daemon-state-skipped",
        axis="DAEMON-STATE",
        file="",
        line_range=[0, 0],
        description="DAEMON-STATE axis SKIPPED: %s" % reason,
        attribution="daemon-state/infra-error",
    )


def _load_daemon_config(
    repo_root: Path,
    infra_errors: list[str],
) -> dict | None:
    """Load daemon_state section from gate.yaml.

    Returns None if gate.yaml absent or daemon_state section absent.
    Loads conflicts_file if referenced (logs warning if missing).
    """
    gate_path = repo_root / ".code-forge" / "gate.yaml"
    if not gate_path.exists():
        return None

    try:
        with open(str(gate_path), "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    section = data.get("daemon_state")
    if section is None:
        return None

    if not isinstance(section, dict):
        return None

    # Load external conflicts_file if referenced
    conflicts_file = section.get("conflicts_file")
    if conflicts_file and isinstance(conflicts_file, str):
        cf_path = repo_root / ".code-forge" / conflicts_file
        if not cf_path.exists():
            infra_errors.append(
                "daemon_state.conflicts_file '%s' not found; "
                "continuing with inline rules only" % conflicts_file
            )
        else:
            try:
                with open(str(cf_path), "r", encoding="utf-8") as f:
                    cf_data = yaml.safe_load(f)
                if isinstance(cf_data, dict) and "conflicts" in cf_data:
                    existing = section.get("conflicts", [])
                    if not isinstance(existing, list):
                        existing = []
                    section["conflicts"] = existing + cf_data["conflicts"]
            except (yaml.YAMLError, OSError) as exc:
                infra_errors.append(
                    "Failed to load conflicts_file '%s': %s"
                    % (conflicts_file, exc)
                )

    return section


# ---------------------------------------------------------------------------
# DaemonStateRunner -- AxisRunner Protocol implementation
# ---------------------------------------------------------------------------


class DaemonStateRunner:
    """Advisory axis: cross-subsystem state-conflict detection.

    Satisfies the AxisRunner Protocol (is_advisory=True).
    full axis only runs with explicit gate.yaml opt-in.
    reads RuntimeRunner.last_surfaces for cross-axis data.
    """

    def __init__(self, backend=None) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []
        self._backend = backend
        self._runtime_runner: Optional[object] = None

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle counter."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run the DAEMON-STATE advisory axis on the given diff.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding. Advisory only -- never blocks verdict.
        """
        self.infra_errors.clear()

        if not diff_text or not diff_text.strip():
            return []

        # Load daemon config from gate.yaml
        config = _load_daemon_config(repo_root, self.infra_errors)

        # Build effective keyword set: gate.yaml patterns + defaults
        extra_patterns = set()
        if config and isinstance(config.get("patterns"), list):
            extra_patterns = set(
                str(p) for p in config["patterns"] if p
            )
        effective_keywords = DEFAULT_DAEMON_KEYWORDS | frozenset(extra_patterns)

        # Unconfigured behavior
        if config is None:
            if _diff_contains_keywords(diff_text, effective_keywords):
                return [AdvisoryFinding(
                    id="daemon-state-heuristic",
                    axis="DAEMON-STATE",
                    file="",
                    line_range=[0, 0],
                    description=(
                        "Detected stateful subsystem keywords; "
                        "enable daemon_state in gate.yaml for deeper analysis"
                    ),
                    attribution="daemon-state/heuristic",
                )]
            return []

        # Configured but disabled
        if config.get("enabled") is False:
            return []

        # Get runtime surfaces
        runtime_surfaces: list[str] = []
        if (self._runtime_runner is not None
                and hasattr(self._runtime_runner, "last_surfaces")
                and self._runtime_runner.last_surfaces):
            runtime_surfaces = list(self._runtime_runner.last_surfaces)
        else:
            self.infra_errors.append(
                "RuntimeRunner surfaces unavailable; "
                "falling back to heuristic detection"
            )

        # Match static conflict rules first
        static_conflicts = config.get("conflicts", [])
        if not isinstance(static_conflicts, list):
            static_conflicts = []
        static_findings = _match_static_rules(static_conflicts, diff_text)

        # Build static rules text for injection into Q2Q3 prompt
        static_rules_text = ""
        if static_findings:
            lines = ["Known static conflict rules (do NOT re-report these, "
                      "only report NEW conflicts not listed here):"]
            for sf in static_findings:
                lines.append("- %s" % sf.description)
            static_rules_text = "\n".join(lines) + "\n\n"

        # LLM Step 1 -- Q1 enumerate state
        runtime_ctx = ""
        if runtime_surfaces:
            runtime_ctx = (
                "Prior runtime surface analysis:\n"
                + "\n".join(runtime_surfaces)
                + "\n\n"
            )

        q1_prompt = DAEMON_STATE_Q1 % {
            "diff_text": diff_text,
            "runtime_surfaces": runtime_ctx,
        }

        try:
            q1_result = llm_invoke(
                q1_prompt,
                backend=self._backend,
                expected_keys=frozenset({"external_state"}),
            )
        except LLMInvokeError as exc:
            reason = str(exc)
            self.infra_errors.append(
                "DAEMON-STATE Q1 LLM call failed: %s" % reason
            )
            return static_findings + [_build_skipped_finding(reason)]

        # Parse Q1 response
        q1_content = q1_result.content
        if isinstance(q1_content, dict):
            state_items = q1_content.get("external_state")
        else:
            state_items = None

        if state_items is None:
            reason = "Q1 response missing external_state key"
            self.infra_errors.append(reason)
            return static_findings + [_build_skipped_finding(reason)]

        if not isinstance(state_items, list):
            state_items = []

        # Extract grep keywords from Q1 response
        keywords = _extract_grep_keywords(state_items)

        # grep repo for keywords
        grep_context = _grep_repo(keywords, repo_root, top_k=5)

        # LLM Step 2 -- Q2+Q3 with grep context
        q2q3_prompt = DAEMON_STATE_Q2Q3 % {
            "diff_text": diff_text,
            "grep_context": grep_context if grep_context else "(no grep matches)",
            "static_rules": static_rules_text,
        }

        try:
            q2q3_result = llm_invoke(
                q2q3_prompt,
                backend=self._backend,
                expected_keys=frozenset({"conflicts"}),
            )
        except LLMInvokeError as exc:
            reason = str(exc)
            self.infra_errors.append(
                "DAEMON-STATE Q2Q3 LLM call failed: %s" % reason
            )
            return static_findings + [_build_skipped_finding(reason)]

        # Parse Q2Q3 response
        q2q3_content = q2q3_result.content
        if isinstance(q2q3_content, dict):
            llm_conflicts = q2q3_content.get("conflicts", [])
        else:
            llm_conflicts = []

        if not isinstance(llm_conflicts, list):
            llm_conflicts = []

        # Build LLM findings
        llm_findings: list[AdvisoryFinding] = []
        for idx, conflict in enumerate(llm_conflicts):
            if not isinstance(conflict, dict):
                continue
            subsystem = str(conflict.get("subsystem", "unknown"))
            mutates = str(conflict.get("mutates", "unknown"))
            interferes = str(conflict.get("interferes_with", "unknown"))
            scenario = str(conflict.get("scenario", ""))
            description = (
                "[%s] mutates %s; interferes with %s"
                % (subsystem, mutates, interferes)
            )
            if scenario:
                description += " -> %s" % scenario
            llm_findings.append(AdvisoryFinding(
                id="daemon-state-llm-%d" % idx,
                axis="DAEMON-STATE",
                file="",
                line_range=[0, 0],
                description=description,
                attribution="daemon-state/llm",
            ))

        # static findings first, then LLM-discovered
        return static_findings + llm_findings

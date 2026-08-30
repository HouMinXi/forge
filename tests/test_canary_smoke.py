"""Gated smoke tests for canary pipeline with real model (mimo-pro).

NEVER runs in default unit suite or CI. Requires FORGE_SMOKE_MIMO=1.

These tests exercise the real dispatch path that Plans 01-03 built with stub
providers. The spike protocol validates discrimination power; the end-to-end
smoke proves the full pipeline completes against a live model.

Prerequisites:
  - FORGE_SMOKE_MIMO=1 in environment
  - MIMO_API_KEY set to a valid mimo-pro API key
  - FORGE_LLM_TIMEOUT_S >= 600 (cross-Pacific latency; default 120 is too low)
  - MIMO_MODEL (optional, defaults to MoMo-72B-Preview)
  - MIMO_BASE_URL (optional, defaults to api.xiaomimimo.com/v1)
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
import unittest

from code_forge.backend import BackendConfig
from code_forge.canary import (
    evaluate_canary_coverage,
    partition_canary_findings,
)
from code_forge.canary_gen import (
    generate_canaries,
    run_inline_canary,
    validate_canary_findings,
)
from code_forge.llm_invoke import llm_invoke, LLMInvokeError
from code_forge.state import Verdict

_GATE_MSG = "gated: set FORGE_SMOKE_MIMO=1 to run real-model smoke"

# -- mimo-pro backend configuration -----------------------------------------

_MIMO_MODEL = os.environ.get("MIMO_MODEL", "MoMo-72B-Preview")
_MIMO_BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")

_MIMO_BACKEND = BackendConfig(
    name="mimo-smoke",
    type="api",
    model=_MIMO_MODEL,
    format="openai",
    base_url=_MIMO_BASE_URL,
    api_key_env="MIMO_API_KEY",
    max_tokens=16384,
)

# -- Synthetic Python diff for testing ---------------------------------------
# A multi-function diff with realistic structure so the canary generator has
# enough material to produce semantic mutations.

_SAMPLE_DIFF = textwrap.dedent("""\
    diff --git a/ledger.py b/ledger.py
    --- a/ledger.py
    +++ b/ledger.py
    @@ -1,30 +1,35 @@
    +\"\"\"Account ledger helpers.\"\"\"
    +
    +
     def clamp(value, lo, hi):
    -    return max(lo, min(value, hi))
    +    \"\"\"Constrain value to [lo, hi].\"\"\"
    +    if value < lo:
    +        return lo
    +    if value > hi:
    +        return hi
    +    return value
    +
    +
    +def format_currency(cents):
    +    \"\"\"Render integer cents as dollar string.\"\"\"
    +    return f"${cents / 100:.2f}"
    +
    +
    +def safe_ratio(numerator, denominator):
    +    \"\"\"Divide, returning 0.0 when denominator is zero.\"\"\"
    +    return numerator / denominator if denominator else 0.0
    +
    +
    +def paginate(items, page, page_size):
    +    \"\"\"Return one page of items (page is 1-indexed).\"\"\"
    +    offset = (page - 1) * page_size
    +    return items[offset:offset + page_size]
    +
    +
    +def can_afford(cost, balance):
    +    \"\"\"True when balance covers cost (exact equality affords).\"\"\"
    +    return cost <= balance
""")


# -- Shared helpers ----------------------------------------------------------

def _mimo_canary_provider(diff_text: str) -> list:
    """Generate canary mutations via mimo-pro LLM."""
    n_canaries = 5
    prompt = (
        "You are a code mutation expert. Given this Python diff, "
        "generate %d subtle semantic mutations. Each mutation must "
        "introduce a real bug (off-by-one, None deref, resource "
        "leak, etc.) that requires non-local reasoning to detect. "
        "Do NOT include comments explaining the bug.\n"
        "Each snippet MUST be <= 5 lines. "
        "\"line\" is the 1-based line number of the bug WITHIN "
        "the 'code' snippet (not a file line number).\n"
        "Return JSON: {\"mutations\": [{\"file\": \"...\", "
        "\"line\": N, \"original\": \"<unmodified code snippet>\", "
        "\"code\": \"<mutated code snippet>\", "
        "\"description\": \"...\"}]}\n\n"
        "Diff:\n" % n_canaries + diff_text
    )
    result = llm_invoke(
        prompt,
        backend=_MIMO_BACKEND,
        expected_keys=frozenset({"mutations"}),
    )
    content = result.content
    if isinstance(content, dict):
        return content.get("mutations", [])
    return json.loads(str(content)).get("mutations", [])


def _mimo_review_provider(prompt: str) -> str:
    """Standard review via mimo-pro LLM."""
    result = llm_invoke(prompt, backend=_MIMO_BACKEND)
    content = result.content
    return content if isinstance(content, str) else json.dumps(content)


def _overloaded_review_provider(prompt: str) -> str:
    """Deliberately degraded review via mimo-pro LLM.

    Prepends a large distractor block and instructs brevity to simulate
    an overloaded reviewer who does not carefully trace logic.
    """
    distractor = (
        "# CONTEXT: You have 40 PRs queued. This is a routine helpers "
        "module from a reliable senior teammate, very low risk. Read it "
        "once at normal speed; do NOT trace arithmetic, loop bounds, or "
        "boundary conditions. Approve unless something obviously wrong "
        "jumps out.\n"
        + "# unrelated change: " * 200 + "\n"
        "Be extremely brief, one line per issue. If nothing jumps out, "
        "return an empty findings array.\n\n"
    )
    return _mimo_review_provider(distractor + prompt)


def _source_lookup(file_path: str):
    """Dummy source lookup that returns None (no real files to verify)."""
    return None


# -- Tests -------------------------------------------------------------------

@unittest.skipUnless(
    os.environ.get("FORGE_SMOKE_MIMO") == "1", _GATE_MSG
)
class TestCanaryDiscriminationMimo(unittest.TestCase):
    """Spike-protocol validation: canary set has discrimination power.

    Runs the spike protocol from CONTEXT sec 9: generate canaries, then
    run GENUINE and OVERLOADED reviews independently to verify separation.
    Uses majority voting (>= 2/3 runs) to handle non-determinism.
    """

    def test_canary_discrimination_mimo(self) -> None:
        timeout_s = int(os.environ.get("FORGE_LLM_TIMEOUT_S", "600"))
        self.assertGreaterEqual(
            timeout_s, 600,
            "FORGE_LLM_TIMEOUT_S must be >= 600 for cross-Pacific mimo-pro",
        )

        # Step 1: Generate canaries from the sample diff.
        canaries = generate_canaries(
            _SAMPLE_DIFF, 5, provider=_mimo_canary_provider
        )
        # If generation returned CanarySkip, the diff was insufficient.
        self.assertIsInstance(
            canaries, list,
            "generate_canaries returned CanarySkip: %s" % canaries,
        )
        self.assertGreaterEqual(len(canaries), 2, "need >= 2 verified canaries")

        # Build a manifest from the generated canaries for evaluation.
        from code_forge.canary_gen import inject_canaries_into_diff
        modified_diff, manifest = inject_canaries_into_diff(
            _SAMPLE_DIFF, canaries
        )
        threshold = max(1, len(manifest) * 3 // 5)  # 60%

        # Step 2: Run 3 repetitions each of genuine and overloaded reviews.
        n_runs = 3
        genuine_passes = 0
        overloaded_passes = 0

        for run_idx in range(n_runs):
            print(
                "\n--- Discrimination run %d/%d ---" % (run_idx + 1, n_runs)
            )
            # Genuine review: standard effort.
            try:
                genuine_raw = _mimo_review_provider(
                    "You are a code reviewer. Review this diff for bugs, "
                    "security issues, and code quality problems.\n"
                    "Return JSON: {\"findings\": [...]}\n"
                    "Each finding needs: file, line, severity, description."
                    "\n\nDiff:\n" + modified_diff
                )
                genuine_data = json.loads(genuine_raw)
                genuine_findings = validate_canary_findings(
                    genuine_data.get("findings", [])
                    if isinstance(genuine_data, dict) else []
                )
            except (LLMInvokeError, json.JSONDecodeError, TypeError) as exc:
                print("  genuine run %d failed: %s" % (run_idx + 1, exc))
                genuine_findings = []

            genuine_result = evaluate_canary_coverage(
                genuine_findings, manifest, threshold=threshold
            )
            if genuine_result.passed:
                genuine_passes += 1
            print(
                "  genuine: caught=%d/%d threshold=%d passed=%s"
                % (
                    len(genuine_result.caught),
                    genuine_result.total,
                    threshold,
                    genuine_result.passed,
                )
            )

            # Overloaded review: deliberately degraded.
            try:
                overloaded_raw = _overloaded_review_provider(
                    "You are a code reviewer. Review this diff for bugs, "
                    "security issues, and code quality problems.\n"
                    "Return JSON: {\"findings\": [...]}\n"
                    "Each finding needs: file, line, severity, description."
                    "\n\nDiff:\n" + modified_diff
                )
                overloaded_data = json.loads(overloaded_raw)
                overloaded_findings = validate_canary_findings(
                    overloaded_data.get("findings", [])
                    if isinstance(overloaded_data, dict) else []
                )
            except (LLMInvokeError, json.JSONDecodeError, TypeError) as exc:
                print("  overloaded run %d failed: %s" % (run_idx + 1, exc))
                overloaded_findings = []

            overloaded_result = evaluate_canary_coverage(
                overloaded_findings, manifest, threshold=threshold
            )
            if overloaded_result.passed:
                overloaded_passes += 1
            print(
                "  overloaded: caught=%d/%d threshold=%d passed=%s"
                % (
                    len(overloaded_result.caught),
                    overloaded_result.total,
                    threshold,
                    overloaded_result.passed,
                )
            )

        # Step 3: Separation assertion on majority (>= 2/3 runs).
        print(
            "\n--- Separation summary ---\n"
            "  genuine passes: %d/%d\n"
            "  overloaded passes: %d/%d"
            % (genuine_passes, n_runs, overloaded_passes, n_runs)
        )
        majority = n_runs * 2 // 3  # >= 2 of 3

        # Genuine should catch enough in the majority of runs.
        self.assertGreaterEqual(
            genuine_passes, majority,
            "Genuine review failed to catch canaries in majority of runs "
            "(%d/%d passed, need >= %d). The canary set may be too subtle "
            "or the model too weak for this diff."
            % (genuine_passes, n_runs, majority),
        )

        # Overloaded should fail to catch enough in the majority of runs.
        self.assertLess(
            overloaded_passes, majority,
            "Overloaded review caught canaries in majority of runs "
            "(%d/%d passed, expected < %d). The canary set has no "
            "discrimination power -- canaries are too easy/salient."
            % (overloaded_passes, n_runs, majority),
        )

        print("\nSEPARATION CONFIRMED: canary set discriminates.")


@unittest.skipUnless(
    os.environ.get("FORGE_SMOKE_MIMO") == "1", _GATE_MSG
)
class TestRunInlineCanaryE2EMimo(unittest.TestCase):
    """End-to-end real-model smoke of the full canary pipeline.

    Exercises the complete dispatch path: generation + injection + real
    review + partition + cite-verify + gate. This is the "Run the real
    path once" proof required by the golden rules.

    Tolerant assertions: verdict in {DELEGATED, UNRELIABLE} (never crashes,
    never PASS). Timeout/error returns DELEGATED (graceful degradation).
    """

    def test_run_inline_canary_e2e_mimo(self) -> None:
        timeout_s = int(os.environ.get("FORGE_LLM_TIMEOUT_S", "600"))
        self.assertGreaterEqual(
            timeout_s, 600,
            "FORGE_LLM_TIMEOUT_S must be >= 600 for cross-Pacific mimo-pro",
        )

        # Run the full pipeline end-to-end.
        try:
            verdict, real_findings = run_inline_canary(
                _SAMPLE_DIFF,
                n=3,
                threshold_ratio=0.6,
                canary_provider=_mimo_canary_provider,
                review_provider=_mimo_review_provider,
                source_lookup=_source_lookup,
            )
        except Exception as exc:
            # If run_inline_canary itself raises (should not happen -- it has
            # internal try/except), record and fail with diagnostics.
            self.fail(
                "run_inline_canary raised unexpectedly: %s" % exc
            )

        print(
            "\n--- E2E smoke result ---\n"
            "  verdict: %s\n"
            "  real_findings: %d"
            % (verdict.value, len(real_findings))
        )

        # Verdict must be DELEGATED or UNRELIABLE (inline never returns PASS).
        self.assertIn(
            verdict,
            (Verdict.DELEGATED, Verdict.UNRELIABLE),
            "Expected DELEGATED or UNRELIABLE, got %s" % verdict.value,
        )

        # If real_findings is non-empty, verify no canary entries leaked.
        # Re-generate canaries to get the manifest for partition check.
        if real_findings:
            canaries = generate_canaries(
                _SAMPLE_DIFF, 3, provider=_mimo_canary_provider
            )
            if isinstance(canaries, list) and canaries:
                from code_forge.canary_gen import inject_canaries_into_diff
                _, manifest = inject_canaries_into_diff(
                    _SAMPLE_DIFF, canaries
                )
                partition = partition_canary_findings(
                    real_findings, manifest
                )
                self.assertEqual(
                    len(partition.canary), 0,
                    "Canary entries leaked into real_findings: %s"
                    % [f.get("description", "") for f in partition.canary],
                )

        # Working tree must be clean (no stray files written).
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Filter out the test file itself and spike fixtures (staged by us).
        stray = [
            line for line in result.stdout.strip().splitlines()
            if line.strip()
            and "test_canary_smoke.py" not in line
        ]
        self.assertEqual(
            stray, [],
            "Working tree has stray files after smoke: %s" % stray,
        )

        print("E2E SMOKE COMPLETE: verdict=%s" % verdict.value)

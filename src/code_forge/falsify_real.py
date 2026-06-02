"""RealFalsifier: LLM-backed finding verification.

Invokes llm_invoke with a 10-step anti-hallucination protocol to
verify each L1 candidate.  Maps the verdict to a Disposition value.
"""
from __future__ import annotations

from typing import Optional

from .backend import BackendConfig
from .disposition import Disposition
from .falsify import Falsifier
from .llm_invoke import LLMInvokeError, llm_invoke
from .state import StateFinding

_PROMPT_PREFIX = (
    "You are a code review verifier.  A reviewer flagged the following "
    "finding.  Verify whether it is real.\n\n"
    "Protocol: (1) re-read code at location, (2) prove path reachable, "
    "(3) identify concrete failure mode, (4) check 2-3 levels of callers, "
    "(5) check patch context, (6) verify against ground truth, "
    "(7) check for intentional design, (8) test multi-step conditions, "
    "(9) anti-hallucination: does the symbol actually exist?, "
    "(10) debate: author vs reviewer perspective.\n\n"
    'Respond JSON only:\n'
    '{"verdict": "CONFIRMED" | "DISMISSED" | "UNCERTAIN", '
    '"reasoning": "..."}\n\n'
    "Finding:\n"
)


class RealFalsifier(Falsifier):
    def __init__(self, backend: Optional[BackendConfig] = None):
        self._backend = backend

    def falsify(self, finding: StateFinding) -> Disposition:
        prompt = (
            _PROMPT_PREFIX
            + "File: " + finding.file + "\n"
            + "Lines: " + str(finding.line_range) + "\n"
            + "Description: " + finding.description + "\n"
        )
        try:
            response = llm_invoke(prompt, backend=self._backend)
        except LLMInvokeError:
            return Disposition.UNCERTAIN

        if not isinstance(response, dict):
            return Disposition.UNCERTAIN

        verdict_str = response.get("verdict", "UNCERTAIN")
        if verdict_str == "FIXED":
            raise ValueError(
                "FIXED is not a valid falsifier output "
                "(LLM returned FIXED for %s)" % finding.fingerprint
            )
        try:
            return Disposition(verdict_str)
        except ValueError:
            return Disposition.UNCERTAIN

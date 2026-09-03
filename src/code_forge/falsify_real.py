"""RealFalsifier: LLM-backed finding verification.

Invokes llm_invoke with a 10-step anti-hallucination protocol to
verify each L1 candidate.  Maps the verdict to a Disposition value.
"""
from __future__ import annotations

from typing import Optional

from .backend import BackendConfig
from .disposition import Disposition
from .falsify import Falsifier
from .falsify_receipt import check_receipt
from .llm_invoke import llm_invoke
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
    "If your verdict rests on how an external library, API or runtime "
    "BEHAVES -- not on the logic of the code under review -- you must "
    "supply an execution receipt: the exact command that demonstrates "
    "the behaviour and its actual output.  Without one, such a verdict "
    "will be downgraded to UNCERTAIN, because neither you nor the "
    "reviewer can check a library's behaviour by reasoning about it.\n\n"
    'Respond JSON only:\n'
    '{"verdict": "CONFIRMED" | "DISMISSED" | "UNCERTAIN", '
    '"reasoning": "...", '
    '"receipt": {"command": "...", "output": "..."}}\n\n'
    "Omit receipt entirely when the finding is about the diff's own "
    "logic.\n\n"
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
        # LLMInvokeError deliberately propagates. Returning UNCERTAIN here
        # would make an unreachable backend indistinguishable from a
        # finding the verifier genuinely could not decide, and the
        # convergence check treats any UNCERTAIN as a reason to reset the
        # clean-round counter. A run whose backend is down would then
        # grind to max_total_rounds learning nothing. llm_invoke already
        # owns the retry budget; exhausting it is an infrastructure
        # outcome, and the caller routes it as one.
        result = llm_invoke(
            prompt,
            backend=self._backend,
            expected_keys=frozenset({"verdict", "reasoning"}),
        )
        response = result.content

        if not isinstance(response, dict):
            return Disposition.UNCERTAIN

        verdict_str = response.get("verdict", "UNCERTAIN")
        if verdict_str == "FIXED":
            raise ValueError(
                "FIXED is not a valid falsifier output "
                "(LLM returned FIXED for %s)" % finding.fingerprint
            )
        try:
            disposition = Disposition(verdict_str)
        except ValueError:
            return Disposition.UNCERTAIN

        # A verdict that turns on library behaviour needs an execution
        # receipt. Without one the model is reasoning about behaviour it
        # cannot observe, which is how a one-line-falsifiable claim about
        # numpy reached CONFIRMED after 4287 seconds. Downgrading to
        # UNCERTAIN routes it to a human instead of pretending it was
        # checked.
        #
        # DISMISSED is downgraded too, not just CONFIRMED: an unverified
        # dismissal buries a real defect, which is the worse direction to
        # be wrong in.
        if disposition in (Disposition.CONFIRMED, Disposition.DISMISSED):
            check = check_receipt(finding.description, response)
            if check.should_downgrade:
                return Disposition.UNCERTAIN

        return disposition

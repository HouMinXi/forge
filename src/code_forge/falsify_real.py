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
from .llm_invoke import FalsifyProtocolError, llm_invoke
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


_DIFF_CAP = 8 * 1024

_DIFF_SECTION = (
    "\n## Diff (the change under review, this file only)\n"
    "Line tags: [+NNN] added lines (present after the change), "
    "[----] removed lines (present before the change), "
    "[ NNN] unchanged context.\n"
    "The finding is a defect only if the ADDED lines do something wrong "
    "or the REMOVED lines took away something needed. A description "
    "that is true about the code but describes what the change was "
    "meant to do -- restoring a guard, dropping a normalisation, "
    "widening a type -- is not a defect; answer DISMISSED and say why "
    "the change is the intended direction.\n\n"
)


_READERS_SECTION = (
    "\n## Still referenced after the change\n"
    "Identifiers the diff removes from this file that other code in the "
    "post-change tree still reads. A removed parameter, attribute or "
    "function with live readers is the shape of a regression; one with "
    "none is the shape of a cleanup. Locations tagged (test) are test "
    "files: a surviving test call is evidence the contract was public, "
    "not evidence production code depends on it.\n\n"
)


def _readers_for_file(rows, path: str) -> str:
    """Rows from RemovedSymbolReaders for this finding's file, one line
    each, or "" when there are none (which keeps the prompt identical to
    the no-context form so the A4-0 number can be re-run)."""
    if not rows or not path:
        return ""
    lines = []
    for r in rows:
        if getattr(r, "source", "") != "removed-symbol-readers":
            continue
        if r.file != path:
            continue
        lines.append("- %s: %s reader(s): %s" % (r.entity, r.downstream, r.dependents))
        # The lines behind the addresses (A4-0c).
        snippets = getattr(r, "snippets", None) or {}
        enclosing = getattr(r, "enclosing", None) or {}
        for loc, text in snippets.items():
            head = enclosing.get(loc)
            lines.append("    %s%s\n        %s" % (
                loc, ("  [in %s]" % head) if head else "", text.strip()))
    return "\n".join(lines) + "\n" if lines else ""


def _diff_for_file(diff_text: Optional[str], path: str) -> str:
    """Annotated hunks for one file, or "" when the file has none."""
    if not diff_text or not path:
        return ""
    from .diff import annotate_diff_lines, split_diff_for_files
    section = split_diff_for_files(diff_text, [path])
    if not section:
        return ""
    text = annotate_diff_lines(section)
    if len(text) > _DIFF_CAP:
        text = text[:_DIFF_CAP] + "\n... [truncated to %d bytes]\n" % _DIFF_CAP
    return text


class RealFalsifier(Falsifier):
    def __init__(
        self,
        backend: Optional[BackendConfig] = None,
        diff_text: Optional[str] = None,
        context_rows=None,
    ):
        self._backend = backend
        # FactRows from context sources (RemovedSymbolReaders today). The
        # A4-0 misses that remained were refactor-shaped changes whose
        # wrongness lives in a reader the hunk does not show; this hands
        # the judge those readers. Empty/None: prompt unchanged.
        self._context_rows = list(context_rows) if context_rows else []
        # The diff under review. Without it the falsifier judges a
        # sentence about the code rather than the code: Phase 59-A2
        # measured 8/20 agreement on a frozen set, and seven of the ten
        # clean-side misses were confirmations of findings that
        # described the upstream fix as a defect. None keeps the old
        # prompt byte-for-byte so that number can be re-run.
        self._diff_text = diff_text

    def falsify(self, finding: StateFinding) -> Disposition:
        prompt = (
            _PROMPT_PREFIX
            + "File: " + finding.file + "\n"
            + "Lines: " + str(finding.line_range) + "\n"
            + "Description: " + finding.description + "\n"
        )
        hunks = _diff_for_file(self._diff_text, finding.file)
        if hunks:
            prompt += _DIFF_SECTION + hunks
        readers = _readers_for_file(self._context_rows, finding.file)
        if readers:
            prompt += _READERS_SECTION + readers
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
            raise FalsifyProtocolError(
                "falsifier returned non-dict content for %s"
                % finding.fingerprint, raw=response)

        if "verdict" not in response:
            raise FalsifyProtocolError(
                "falsifier response lacks 'verdict' for %s"
                % finding.fingerprint, raw=response)
        verdict_str = response["verdict"]
        if verdict_str == "FIXED":
            # FIXED is a Disposition member but not a falsifier verdict
            # (only the verify path may set it). Same protocol violation
            # as an unknown string; a plain ValueError here would escape
            # machine.py's infra arms and abort the whole review.
            raise FalsifyProtocolError(
                "falsifier returned FIXED for %s; only verify may set it"
                % finding.fingerprint, raw=response)
        try:
            disposition = Disposition(verdict_str)
        except (ValueError, TypeError):
            raise FalsifyProtocolError(
                "falsifier verdict %r not in Disposition for %s"
                % (verdict_str, finding.fingerprint), raw=response)

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

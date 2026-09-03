# SPDX-License-Identifier: Apache-2.0
"""Require an execution receipt for findings that assert library behaviour.

The falsifier is a single LLM call. Steps 6 and 9 of its protocol --
"verify against ground truth" and "does this symbol actually exist" --
are prose instructions to a model with no way to check either, and the
reviewer and falsifier share a backend family, so the check is not
independent: it is the same model class asked whether it agrees with
itself.

Measured cost of that (2026-09-02, ashare-lab R21): a finding claiming
"numpy>=1.20 registers np.bool_ in the numbers ABCs" was stamped
CONFIRMED after 4287 seconds. One line refutes it, on two numpy versions:

    >>> isinstance(numpy.bool_(True), numbers.Real)
    False

This module does not try to make the model more accurate. It makes the
ABSENCE of evidence visible: a finding whose truth turns on how a library
actually behaves is downgraded to UNCERTAIN unless the falsifier supplies
a receipt -- what it ran, and what came back. UNCERTAIN routes to human
adjudication, which is the honest destination for a claim nobody checked.

Deliberately narrow. Only claims about external library or API behaviour
need receipts; claims about the diff's own logic are what the reviewer is
for, and demanding receipts for those would downgrade nearly everything
and make the gate useless.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Phrases that mark a claim about how some library or runtime BEHAVES,
# as opposed to a claim about the code under review. Each is anchored to
# a behavioural verb so that merely naming a library does not trigger it:
# "we call numpy.asarray here" is about the diff, while "numpy returns a
# view" is a claim about numpy.
_BEHAVIOURAL_CLAIM_PATTERNS = (
    # "<lib> >= X.Y does/registers/returns/raises ..."
    r"\b[\w.]+\s*[><=]=?\s*[\d.]+\s+\w*\s*(?:does|registers|returns|raises|adds|removes|changes)",
    # "numpy registers np.bool_ in ..." / "pandas returns a copy"
    r"\b(?:numpy|pandas|scipy|torch|tensorflow|django|flask|requests|sqlalchemy|pydantic|asyncio|numbers|json|pathlib|os|sys|re|itertools|functools|collections|typing)\b[^.!?\n]{0,80}?\b(?:registers|returns|raises|inherits|implements|is registered|is a subclass|subclasses|coerces|promotes|deprecat\w+)",
    # "isinstance(x, Y) is True/False" -- an assertion with a truth value
    r"\bisinstance\s*\([^)]*\)\s+(?:is|returns|evaluates to)\s+(?:True|False)",
    # "the <lib> API guarantees/documents ..."
    r"\b(?:API|library|module|package|stdlib|runtime)\b[^.!?\n]{0,60}?\b(?:guarantees|documents|specifies|is documented)",
    # Version-gated behaviour: "since 3.11", "as of numpy 2.0"
    r"\b(?:since|as of|starting (?:in|with)|prior to|before)\s+(?:python\s+)?[\w.]*\s*\d+\.\d+",
)

_COMPILED = tuple(
    re.compile(p, re.IGNORECASE) for p in _BEHAVIOURAL_CLAIM_PATTERNS
)


@dataclass(frozen=True)
class ReceiptCheck:
    """Outcome of asking whether a finding needs, and has, a receipt."""

    needs_receipt: bool
    has_receipt: bool
    matched_claim: Optional[str] = None
    reason: str = ""

    @property
    def should_downgrade(self) -> bool:
        return self.needs_receipt and not self.has_receipt


def asserts_library_behaviour(description: str) -> Optional[str]:
    """Return the matched phrase if this claims how a library behaves.

    Returns None when the finding is about the code under review, which
    is the common case and must stay cheap to pass.
    """
    if not description:
        return None
    for pattern in _COMPILED:
        m = pattern.search(description)
        if m:
            return m.group(0)[:120]
    return None


def has_execution_receipt(response: object) -> bool:
    """Did the falsifier show what it ran and what came back?

    Both halves are required. A command with no output proves the model
    knows how to phrase a command; output with no command cannot be
    reproduced. Either alone is a story about verification rather than
    verification.
    """
    if not isinstance(response, dict):
        return False
    receipt = response.get("receipt")
    if not isinstance(receipt, dict):
        return False
    command = receipt.get("command")
    output = receipt.get("output")
    return bool(
        isinstance(command, str) and command.strip()
        and isinstance(output, str) and output.strip()
    )


def check_receipt(description: str, response: dict) -> ReceiptCheck:
    """Decide whether this verdict may stand without an execution receipt."""
    claim = asserts_library_behaviour(description)
    if claim is None:
        return ReceiptCheck(
            needs_receipt=False,
            has_receipt=has_execution_receipt(response),
            reason="claim is about the diff, not about library behaviour",
        )

    present = has_execution_receipt(response)
    return ReceiptCheck(
        needs_receipt=True,
        has_receipt=present,
        matched_claim=claim,
        reason=(
            "library-behaviour claim with a receipt"
            if present
            else "library-behaviour claim with no receipt -- unverified"
        ),
    )

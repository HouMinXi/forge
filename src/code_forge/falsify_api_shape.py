# SPDX-License-Identifier: Apache-2.0
"""Conservative confirmation guard for a bounded set of external API-shape claims.

This is a lexical safety net, not an API oracle or a complete semantic classifier.
Recognise explicit external/library API subjects and PowerShell-style cmdlet names,
then a nearby parameter/symbol availability assertion in the same sentence. Bare
project functions, arbitrary languages and paraphrases are intentionally not all
covered. A custom Verb-Noun cmdlet can be caught too: its external provenance is
not knowable from prose, so this guard trades that ambiguity for human review.

RealFalsifier currently has no host-verified API evidence channel. Model-authored
commands, outputs, URLs and claimed sources cannot establish one. A recognised
CONFIRMED claim must therefore remain UNCERTAIN, even with those fields present.
This does not change the older general library-behaviour receipt contract.
"""

from __future__ import annotations

import re

# Case-sensitive cmdlet shape is not a vendor/name allowlist. Explicit external
# qualifiers permit other ecosystems without treating every dotted local helper
# as third-party code.
_SUBJECT = re.compile(
    r"(?i:\b(?:cmdlet|(?:external|third-party|library|stdlib|runtime|SDK)\s+"
    r"(?:API|function|method|symbol))\b)"
    r"|(?:外部|第三方|标准库)\s*(?:API|接口|函数|方法|符号)"
    r"|(?<![\w-])[A-Z][a-z]+-[A-Z][A-Za-z0-9]+(?![A-Za-z0-9_-])"
)
# Bind the availability predicate to the subject (or its immediately named
# member), not a later helper/call in the sentence. "has discarded the parameter"
# and "has its arguments reversed" describe code logic, not a signature.
_SHAPE = re.compile(
    r"^\s*(?:(?!(?i:call|wrapper|helper)\b)[A-Za-z_$][\w.$:-]*\s+)?"
    r"(?:"
    r"(?i:(?:(?:does\s+not|doesn't|does)\s+)?"
    r"(?:exposes?|supports?|accepts?|provides?)\s+"
    r"(?:(?:a|an|no|the)\s+)?(?:[-\w.]+\s+){0,2}"
    r"(?:parameters?|arguments?|symbols?|methods?|attributes?|functions?)\b(?!\s+values?\b)"
    r"|(?:(?:does\s+not|doesn't)\s+)?(?:has|have)\s+"
    r"(?:a|an|no|the)\s+(?:[-\w.]+\s+){0,2}"
    r"(?:parameters?|arguments?|symbols?|methods?|attributes?|functions?)\b(?!\s+values?\b)"
    r"|(?:does\s+not|doesn't)\s+exist\b"
    r"|is\s+not\s+(?:available|defined|exported)\b)"
    r"|(?:没有|不支持|不接受|缺少|提供|支持|接受|具有|包含)\s*"
    r"(?:[-A-Za-z0-9_.]+\s*)?(?:参数|符号|方法|函数|属性)(?!的?值)"
    r"|不存在"
    r")"
)
_SENTENCE_END = re.compile(r"[。！？；]|[!?;]|\.(?=\s|$)|\n\s*\n")


def asserts_external_api_shape(text: object) -> bool:
    """Recognise limited API availability assertions without claiming their truth."""
    if not isinstance(text, str):
        return False
    # Backticks delimit names, not evidence; retain punctuation/sentence bounds.
    for sentence in _SENTENCE_END.split(text.replace("`", "")):
        for subject in _SUBJECT.finditer(sentence):
            # Keep the assertion local to the subject. This is deliberately
            # bounded, not a whole-description keyword intersection.
            if _SHAPE.match(sentence[subject.end() : subject.end() + 160]):
                return True
    return False

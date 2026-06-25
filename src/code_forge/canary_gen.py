"""Canary generation, non-equivalence verification, and dispatch orchestration.

This is the M2 core that generates semantic mutations of real diffs, verifies
they are non-equivalent via AST structural comparison, injects them into an
isolated diff copy, dispatches a fresh-context review via an injected provider
seam, and orchestrates the full gate pipeline (generate -> inject -> dispatch ->
validate -> partition -> cite-verify -> evaluate).

All LLM calls go through injected callables (CanaryProvider / ReviewProvider)
so the module is unit-testable with stub providers and zero network calls.

The template fallback uses appended hunks in the diff copy -- a degraded-quality
path compared to the LLM provider's true in-place mutation of existing hunks.
This is explicitly documented and acceptable as a fallback.
"""
from __future__ import annotations

import ast
import hashlib
import json
import random
import re
import sys
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import ceil
from typing import Protocol
from uuid import uuid4

from .canary import (
    Canary,
    CanaryGateResult,
    CanaryPartition,
    evaluate_canary_coverage,
    partition_canary_findings,
)
from .evidence import reverify_finding_cites
from .findings import finding_line  # noqa: F401 -- re-export awareness
from .state import Verdict


# ---------------------------------------------------------------------------
# Protocols (DI seams for testability)
# ---------------------------------------------------------------------------


class CanaryProvider(Protocol):
    """Injected callable that produces semantic mutations from a diff."""

    def __call__(self, diff_text: str) -> list[dict]:
        ...


class ReviewProvider(Protocol):
    """Injected callable that dispatches a fresh-context review."""

    def __call__(self, prompt: str) -> str:
        ...


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanarySkip:
    """Returned instead of a mutation list when canary generation is skipped."""

    reason: str


# ---------------------------------------------------------------------------
# Non-equivalence verification
# ---------------------------------------------------------------------------


def is_non_equivalent(original: str, mutated: str) -> bool:
    """Return True when original and mutated produce different AST structures.

    Applies textwrap.dedent() to both inputs before parsing so indented code
    snippets from templates and LLM mutations are not silently rejected with
    IndentationError. SyntaxError in either side returns False
    (invalid mutation discarded).
    """
    try:
        orig_ast = ast.dump(ast.parse(textwrap.dedent(original)), annotate_fields=False)
        mut_ast = ast.dump(ast.parse(textwrap.dedent(mutated)), annotate_fields=False)
    except SyntaxError:
        return False
    return orig_ast != mut_ast


# ---------------------------------------------------------------------------
# Lightweight canary-findings validator (separate from validate_reviewer_json)
# ---------------------------------------------------------------------------

_REQUIRED_FINDING_KEYS = frozenset({"file", "line", "severity", "description"})


def validate_canary_findings(findings: list[dict]) -> list[dict]:
    """Validate canary reviewer findings: check 4 required keys per finding.

    Unlike validate_reviewer_json, this does NOT require an envelope structure
    (no top-level "findings"/"code_excerpts" keys) and accepts ANY non-empty
    severity string (no P0-P3 enum constraint). Returns a NEW list containing
    only valid findings. The input list is NEVER mutated (immutability).
    """
    valid: list[dict] = []
    for finding in findings:
        missing = _REQUIRED_FINDING_KEYS - set(finding.keys())
        if missing:
            print(
                "canary: dropping finding missing keys %s: %r"
                % (sorted(missing), finding),
                file=sys.stderr,
            )
            continue
        if not finding.get("severity"):
            print(
                "canary: dropping finding with empty severity: %r" % finding,
                file=sys.stderr,
            )
            continue
        valid.append(finding)
    return valid


# ---------------------------------------------------------------------------
# Template library (6 SPEC-01 sec 4 categories)
# ---------------------------------------------------------------------------

_GENERIC_FILENAMES = [
    "helpers.py", "utils.py", "config.py",
    "service.py", "handler.py", "parser.py",
]


def _template_hardcoded_secret() -> dict:
    return {
        "file": "config.py",
        "line": 2,
        "code": 'import boto3\nAPI_KEY = "AKIAIOSFODNN7EXAMPLE"\ndef connect():\n    return boto3.client("s3", aws_access_key_id=API_KEY)',
        "original": 'import boto3\nAPI_KEY = os.environ["AWS_KEY"]\ndef connect():\n    return boto3.client("s3", aws_access_key_id=API_KEY)',
        "description": "Hardcoded AWS credential in source code",
    }


def _template_none_deref() -> dict:
    return {
        "file": "helpers.py",
        "line": 3,
        "code": "def get_email(users, uid):\n    user = users.get(uid)\n    return user.email",
        "original": "def get_email(users, uid):\n    user = users.get(uid)\n    return user.email if user else None",
        "description": "Unchecked None dereference on dict.get() result",
    }


def _template_off_by_one() -> dict:
    return {
        "file": "utils.py",
        "line": 3,
        "code": "def sum_first_n(items, n):\n    total = 0\n    for i in range(n + 1):\n        total += items[i]\n    return total",
        "original": "def sum_first_n(items, n):\n    total = 0\n    for i in range(n):\n        total += items[i]\n    return total",
        "description": "Off-by-one: range(n+1) processes one extra element",
    }


def _template_sql_injection() -> dict:
    return {
        "file": "service.py",
        "line": 2,
        "code": 'def find_user(db, name):\n    cursor = db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")\n    return cursor.fetchone()',
        "original": 'def find_user(db, name):\n    cursor = db.execute("SELECT * FROM users WHERE name = ?", (name,))\n    return cursor.fetchone()',
        "description": "SQL injection via string concatenation",
    }


def _template_resource_leak() -> dict:
    return {
        "file": "handler.py",
        "line": 2,
        "code": "import json\ndef read_config(path):\n    f = open(path)\n    return json.load(f)",
        "original": "import json\ndef read_config(path):\n    with open(path) as f:\n        return json.load(f)",
        "description": "File handle opened without context manager -- resource leak",
    }


def _template_silent_except() -> dict:
    return {
        "file": "parser.py",
        "line": 3,
        "code": "import json\ndef parse(raw):\n    try: return json.loads(raw)\n    except: pass\n    return {}",
        "original": "import json\ndef parse(raw):\n    try: return json.loads(raw)\n    except json.JSONDecodeError: return {}\n    return {}",
        "description": "Bare except swallows all exceptions silently",
    }


_TEMPLATE_LIBRARY = [
    _template_hardcoded_secret,
    _template_none_deref,
    _template_off_by_one,
    _template_sql_injection,
    _template_resource_leak,
    _template_silent_except,
]


# ---------------------------------------------------------------------------
# Diff introspection
# ---------------------------------------------------------------------------


def _has_python_hunks(diff_text: str) -> bool:
    """Return True if the diff contains at least one Python file hunk."""
    return bool(re.search(r'^\+\+\+ b/.*\.py$', diff_text, re.MULTILINE))


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_canaries(
    diff_text: str,
    n: int,
    *,
    provider: CanaryProvider | None = None,
) -> list[dict] | CanarySkip:
    """Generate up to n semantic mutations for canary injection.

    Returns a list of mutation dicts or CanarySkip when generation cannot
    produce enough verified mutations (fewer than 2).
    """
    if not _has_python_hunks(diff_text):
        return CanarySkip(reason="non-Python diff: canary generation requires Python hunks")

    verified: list[dict] = []

    # Try the injected provider first.
    if provider is not None:
        for mut in provider(diff_text):
            if is_non_equivalent(mut.get("original", ""), mut.get("code", "")):
                verified.append(mut)
            if len(verified) >= n:
                break

    # Fill from template library if provider yielded fewer than n.
    if len(verified) < n:
        templates = list(_TEMPLATE_LIBRARY)
        random.shuffle(templates)
        for tmpl_fn in templates:
            if len(verified) >= n:
                break
            mut = tmpl_fn()
            if is_non_equivalent(mut["original"], mut["code"]):
                verified.append(mut)

    if len(verified) < 2:
        return CanarySkip(reason="fewer than 2 verified canaries generated")

    return verified


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------


def inject_canaries_into_diff(
    diff_text: str,
    mutations: list[dict],
) -> tuple[str, list[Canary]]:
    """Inject mutations into a COPY of the diff text. Never mutates the original.

    LINE-MATCH INVARIANT: each appended hunk starts at +1 so the buggy line's
    new-file position equals mutation["line"]. The code snippet is kept short
    (K <= 5) so the +/-2 line window covers the whole snippet.

    Returns (modified_diff_copy, manifest_of_canary_objects).
    """
    parts = [diff_text]
    manifest: list[Canary] = []

    for mut in mutations:
        code = mut["code"]
        code_lines = code.split("\n")
        k = len(code_lines)
        if k > 5:
            raise ValueError(
                "canary snippet exceeds 5-line limit (K=%d); "
                "LINE-MATCH invariant requires K <= 5" % k
            )

        hunk_header = "@@ -0,0 +1,%d @@" % k
        plus_lines = "\n".join("+" + line for line in code_lines)
        hunk = (
            "--- a/%s\n"
            "+++ b/%s\n"
            "%s\n"
            "%s\n"
        ) % (mut["file"], mut["file"], hunk_header, plus_lines)
        parts.append(hunk)

        # +start is 1, so snippet-line == new-file-line by construction.
        canary_line = mut["line"]
        canary = Canary(
            canary_id=str(uuid4()),
            file=mut["file"],
            line=canary_line,
            sha256=hashlib.sha256(code.encode()).hexdigest(),
            description=mut["description"],
        )
        manifest.append(canary)

    modified = "\n".join(parts)
    return modified, manifest


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch_canary_review(
    modified_diff: str,
    *,
    provider: ReviewProvider,
) -> list[dict]:
    """Dispatch a fresh-context review of the modified diff.

    Uses validate_canary_findings (separate from validate_reviewer_json).
    The prompt has no author narrative, no prior findings, no conventions
    digest to prevent anchoring bias in the fresh-context review.
    """
    prompt = (
        "You are a code reviewer. Review this diff for bugs, security "
        "issues, and code quality problems.\n"
        "Return JSON: {\"findings\": [...]}\n"
        "Each finding needs: file, line, severity, description.\n\n"
        "Diff:\n" + modified_diff
    )
    raw = provider(prompt)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print("canary: cannot parse reviewer response as JSON", file=sys.stderr)
        return []

    findings = data.get("findings", []) if isinstance(data, dict) else []
    return validate_canary_findings(findings)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_inline_canary(
    diff_text: str,
    *,
    n: int = 5,
    threshold_ratio: float = 0.6,
    canary_provider: CanaryProvider | None = None,
    review_provider: ReviewProvider,
    source_lookup: Callable[[str], Sequence[str] | None],
) -> tuple[Verdict, list[dict]]:
    """Orchestrate the full canary pipeline: generate -> inject -> dispatch ->
    validate -> partition -> cite-verify -> evaluate.

    Returns (verdict, real_findings). On any dispatch error, degrades
    gracefully to (DELEGATED, []).
    """
    result = generate_canaries(diff_text, n, provider=canary_provider)
    if isinstance(result, CanarySkip):
        print("canary: skipped -- %s" % result.reason, file=sys.stderr)
        return Verdict.DELEGATED, []

    modified_diff, manifest = inject_canaries_into_diff(diff_text, result)

    try:
        raw_findings = dispatch_canary_review(
            modified_diff, provider=review_provider
        )

        threshold = max(1, ceil(threshold_ratio * len(manifest)))
        gate_result = evaluate_canary_coverage(
            raw_findings, manifest, threshold=threshold
        )

        if not gate_result.passed:
            missed_ids = ", ".join(gate_result.missed)
            print(
                "canary: UNRELIABLE -- missed canaries: %s" % missed_ids,
                file=sys.stderr,
            )
            return Verdict.UNRELIABLE, []

        partition = partition_canary_findings(raw_findings, manifest)
        cite_result = reverify_finding_cites(
            list(partition.real), source_lookup
        )
        return Verdict.DELEGATED, list(cite_result.verified)

    except Exception as exc:
        print(
            "canary: dispatch failed: %s, falling back to DELEGATED" % exc,
            file=sys.stderr,
        )
        return Verdict.DELEGATED, []

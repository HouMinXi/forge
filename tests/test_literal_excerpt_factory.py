"""Bounded literal-excerpt correction, exercised through the real factory.

The model is stubbed at the transport boundary only: `_invoke_openai` returns a
canned body, everything above it (factory, llm_invoke, eligibility, replacement
check, excerpt collection) is the production path.
"""

import json
import time
from pathlib import Path

from code_forge import llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n one\n-old.\n+new.\n three\n"

GOOD = "one\nnew.\nthree"
BAD = "one\nnew!\nthree"


def _envelope(content, *, findings=None, excerpts=None):
    return json.dumps(
        {
            "findings": findings if findings is not None else [],
            "code_excerpts": excerpts
            if excerpts is not None
            else [{"file": "a.py", "start_line": 1, "end_line": 3, "content": content}],
        }
    )


def _backend(monkeypatch, *, kind="api"):
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    monkeypatch.setenv("EXCERPT_TEST_KEY", "fixture-not-a-credential")
    return BackendConfig(
        name="literal-test",
        type=kind,
        model="fixture",
        format="openai",
        base_url="https://example.invalid",
        api_key_env="EXCERPT_TEST_KEY",
        timeout_s=10,
    )


def _provider(backend, diff=DIFF):
    return build_l1_provider(
        "auto",
        ResolvedReview([Path("a.py")], None, diff, "git"),
        backend=backend,
        max_attempts=1,
        initial_delay_s=0,
    )


def test_factory_recovers_literal_quote_once(monkeypatch):
    """A01: one added request repairs the quote and keeps the rest identical."""
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        return (_envelope(BAD if len(calls) % 2 else GOOD), {"prompt_tokens": 2, "completion_tokens": 3})

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch))
    findings, excerpts, usage, _duration = provider()

    assert len(calls) == 6  # three initial passes plus one correction each
    assert findings == []
    assert len(excerpts) == 3
    assert all(e["content"] == GOOD for e in excerpts)
    assert (usage.input_tokens, usage.output_tokens) == (12, 18)
    diagnostics = provider.correction_observations
    assert len(diagnostics) == 3
    assert all(d["selected"] is True for d in diagnostics)
    assert all(d["indexes"] == [0] for d in diagnostics)
    # The lane is separate so machine's purity gates still see an empty list.
    assert provider.raw_observations == []


def test_still_wrong_correction_is_rejected(monkeypatch):
    """A03: a correction that repeats the error sends nothing further."""
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        return _envelope(BAD), {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch))
    _findings, excerpts, _usage, _duration = provider()

    assert len(calls) == 6  # one correction attempt, never recursive
    # The factory itself accepts the excerpts; content rejection happens later
    # in validate_excerpts_against_diff and the receipt write. What matters here
    # is that the failed correction did not replace or repair anything.
    assert len(excerpts) == 3
    assert all(e["content"] == BAD for e in excerpts)
    diagnostics = provider.correction_observations
    assert len(diagnostics) == 3
    assert all(d["selected"] is False for d in diagnostics)
    assert all(d["reason"] == "replacement_rejected" for d in diagnostics)


def test_valid_reply_sends_no_correction(monkeypatch):
    """A05: an already-correct reply costs zero added requests."""
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        return _envelope(GOOD), {"prompt_tokens": 2, "completion_tokens": 2}

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch))
    _findings, excerpts, _usage, _duration = provider()

    assert len(calls) == 3
    assert len(excerpts) == 3
    assert provider.correction_observations == []


def test_replacement_dropping_finding_is_rejected(monkeypatch):
    """A04: a correction that loses a field is refused even if the quote is right."""
    finding = {
        "file": "a.py",
        "line": 2,
        "severity": "P2",
        "description": "literal test finding",
        "excerpt": "new.",
    }
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        if len(calls) % 2:
            return (_envelope(BAD, findings=[finding]), {"prompt_tokens": 1, "completion_tokens": 1})
        # Correct quote, but the finding vanished.
        return _envelope(GOOD), {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch))
    _findings, _excerpts, _usage, _duration = provider()

    diagnostics = provider.correction_observations
    assert len(diagnostics) == 3
    assert all(d["selected"] is False for d in diagnostics)


def test_nineteen_excerpts_keep_finding_and_unaffected_entries(monkeypatch):
    """A02: only the two bad quotes change; the finding and 17 others survive."""
    lines = [f"entry{i}." for i in range(1, 20)]
    diff = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -0,0 +1,19 @@\n" + "".join(
        f"+{line}\n" for line in lines
    )

    def envelope(contents):
        return json.dumps(
            {
                "findings": [
                    {
                        "file": "b.py",
                        "line": 3,
                        "severity": "P2",
                        "description": "controlled finding",
                        "excerpt": "entry3.",
                    }
                ],
                "code_excerpts": [
                    {"file": "b.py", "start_line": i, "end_line": i, "content": text}
                    for i, text in enumerate(contents, start=1)
                ],
            }
        )

    # A trailing character can be absorbed by the existing single-line
    # tolerance, so the positive control changes an interior character: the
    # quote is then a genuine literal mismatch rather than a short prefix.
    broken = [
        line[:3] + "x" + line[4:] if index in (2, 10) else line for index, line in enumerate(lines)
    ]
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        return (
            envelope(broken if len(calls) % 2 else lines),
            {"prompt_tokens": 4, "completion_tokens": 5},
        )

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    backend = _backend(monkeypatch)
    provider = build_l1_provider(
        "auto",
        ResolvedReview([Path("b.py")], None, diff, "git"),
        backend=backend,
        max_attempts=1,
        initial_delay_s=0,
    )
    findings, excerpts, _usage, _duration = provider()

    assert len(calls) == 6
    # One finding per pass; the correction must not drop any of them.
    assert len(findings) == 3
    assert all(f.file == "b.py" and f.line_range == [3, 3] for f in findings)
    # Three passes x 19 excerpts; every one must carry the corrected text.
    assert len(excerpts) == 57
    assert [e["content"] for e in excerpts] == lines * 3
    diagnostics = provider.correction_observations
    assert len(diagnostics) == 3
    assert all(d["selected"] is True for d in diagnostics)
    assert all(d["indexes"] == [2, 10] for d in diagnostics)


def test_fenced_raw_reply_never_corrects(monkeypatch):
    """A06: a reply that does not strictly decode is not a transcription typo."""
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        return ("```json\n" + _envelope(BAD) + "\n```", {"prompt_tokens": 1, "completion_tokens": 1})

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch))
    _findings, _excerpts, _usage, _duration = provider()

    assert len(calls) == 3
    assert provider.correction_observations == []


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self._pos = 0

    def read(self, size=65536):
        chunk = self._payload[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def test_wire_policy_accepts_exactly_the_limit():
    policy = invoke._CorrectionReadPolicy(limit=32)
    body = policy.reader(_FakeResponse(b"x" * 32))()
    assert body == b"x" * 32
    assert policy.consumed == 32


def test_wire_policy_refuses_one_byte_over():
    """A20: the bound is enforced while reading, not after buffering it all."""
    policy = invoke._CorrectionReadPolicy(limit=32)
    response = _FakeResponse(b"x" * 33)
    with __import__("pytest").raises(invoke.LLMInvokeError) as caught:
        policy.reader(response)()
    assert "exceeded 32 bytes" in str(caught.value)
    assert caught.value.retryable is False
    # The overflow lookahead stopped immediately; the rest was never pulled.
    assert response._pos == 33


def test_correction_failure_keeps_original_reply(monkeypatch):
    """A07/A08: a failed correction writes a diagnostic and changes nothing."""
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        if len(calls) % 2:
            return _envelope(BAD), {"prompt_tokens": 1, "completion_tokens": 1}
        raise invoke.LLMInvokeError(
            "correction transport failed", retryable=False, usage=invoke.Usage(7, 8, 0)
        )

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch))
    _findings, excerpts, usage, _duration = provider()

    assert len(calls) == 6
    assert [e["content"] for e in excerpts] == [BAD, BAD, BAD]
    diagnostics = provider.correction_observations
    assert len(diagnostics) == 3
    assert all(d["selected"] is False and d["reason"] == "correction_failed" for d in diagnostics)
    # The failed request's observed usage is still accounted, not zeroed:
    # three initial calls at 1 input token each, plus three failures at 7.
    assert usage.input_tokens == 3 + 21
    assert provider.raw_observations == []


def test_live_factory_pass_name_stamp_stays_eligible():
    """The live factory stamps `pass_name`; eligibility must not choke on it.

    Without this, every real reply looks ineligible and the feature never runs —
    the stubbed transports in the other cases all use bare four-key excerpts.
    """
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    content = {
        "findings": [],
        "code_excerpts": [
            {
                "file": "a.py",
                "start_line": 1,
                "end_line": 3,
                "content": BAD,
                "pass_name": "adversarial",
            }
        ],
    }
    raw = json.dumps(content, indent=2)

    assert invoke._literal_excerpt_indexes(content, raw, REVIEW_REPLY_KEYS, DIFF) == (0,)


def test_multibyte_text_does_not_shift_indexes():
    """A14: UTF-8 content is compared as text, never as bytes."""
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    lines = ["变量 = 1；", "值 = 2；", "结束 = 3；"]
    diff = "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1,3 +1,3 @@\n 上下文\n" + "".join(
        f"+{line}\n" for line in lines
    )
    broken = list(lines)
    broken[1] = "值 = 2"  # dropped the semicolon
    content = {
        "findings": [],
        "code_excerpts": [
            {"file": "c.py", "start_line": 2, "end_line": 4, "content": "\n".join(broken)}
        ],
    }
    raw = json.dumps(content, ensure_ascii=False, indent=2)

    assert invoke._literal_excerpt_indexes(content, raw, REVIEW_REPLY_KEYS, diff) == (0,)


def test_only_the_bad_index_is_selected():
    """A16: two excerpts share coordinates; only the mistyped one is selected."""
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    lines = ["alpha = 1", "beta = 2", "gamma = 3"]
    diff = "diff --git a/d.py b/d.py\n--- a/d.py\n+++ b/d.py\n@@ -1,3 +1,3 @@\n" + "".join(
        f"+{line}\n" for line in lines
    )
    content = {
        "findings": [],
        "code_excerpts": [
            {"file": "d.py", "start_line": 1, "end_line": 3, "content": "\n".join(lines)},
            {
                "file": "d.py",
                "start_line": 1,
                "end_line": 3,
                "content": "alpha = 1\nbeta = 2\ngamma = 4",
            },
        ],
    }
    raw = json.dumps(content, indent=2)

    assert invoke._literal_excerpt_indexes(content, raw, REVIEW_REPLY_KEYS, diff) == (1,)


def test_shifted_quote_is_an_offset_not_a_typo():
    """A17: a correct quote at the wrong coordinates must not be 'corrected'."""
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    lines = ["one = 1", "two = 2", "three = 3", "four = 4"]
    diff = "diff --git a/e.py b/e.py\n--- a/e.py\n+++ b/e.py\n@@ -1,4 +1,4 @@\n" + "".join(
        f"+{line}\n" for line in lines
    )
    # The text exists verbatim at lines 2-4, but the quote claims 1-3.
    content = {
        "findings": [],
        "code_excerpts": [
            {"file": "e.py", "start_line": 1, "end_line": 3, "content": "two = 2\nthree = 3\nfour = 4"},
        ],
    }
    raw = json.dumps(content, indent=2)

    assert invoke._literal_excerpt_indexes(content, raw, REVIEW_REPLY_KEYS, diff) == ()


def test_joint_scope_has_no_evidence_binding():
    """A18: only the joint branch supplies repository scope; nothing else does."""
    from code_forge.factories import _evidence_only_diff

    backend = type("B", (), {"type": "api", "format": "openai"})()
    assert _evidence_only_diff(None, None, backend) is None
    assert _evidence_only_diff(object(), None, backend) is None


def test_cli_backend_has_no_evidence_binding(monkeypatch):
    """A13: the CLI branch has no bound reader, so it is never offered a diff."""
    from code_forge.factories import _evidence_only_diff

    backend = _backend(monkeypatch, kind="cli")
    resolved = ResolvedReview([Path("a.py")], None, DIFF, "git")
    assert _evidence_only_diff(resolved, None, backend) is None
    # The api path over the same input does bind one.
    assert _evidence_only_diff(resolved, None, _backend(monkeypatch)) == DIFF


def test_llm_invoke_accepts_the_host_audit_scope():
    """The correction path passes `_audit_scope`; the public entry must take it.

    Without this parameter the whole correction disappears behind a TypeError
    that the caller swallows as `correction_failed`.
    """
    import inspect

    params = inspect.signature(invoke.llm_invoke).parameters
    assert "_audit_scope" in params
    assert params["_audit_scope"].default is None


def test_correction_call_is_booked_as_excerpt_repair_and_never_continues(monkeypatch):
    """The host books this send separately from the pass's initial request.

    Without `_audit_cause="excerpt_repair"` a correction is filed under the
    pass's initial cost; without `_no_continuation` a truncated reply can spend
    extra continuation sends on top of the one allowed correction.
    """
    seen = {}
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    content = {
        "findings": [],
        "code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 3, "content": BAD}],
    }
    raw = json.dumps(content, indent=2)
    repaired = {
        "findings": [],
        "code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 3, "content": GOOD}],
    }

    def fake_invoke(prompt, **kwargs):
        seen.update(kwargs)
        return invoke.LLMResult(
            content=repaired,
            raw_response=json.dumps(repaired, indent=2),
            usage=invoke.Usage(2, 3, 0),
            duration_s=0.0,
        )

    monkeypatch.setattr(invoke, "llm_invoke", fake_invoke)
    result = invoke.LLMResult(
        content=content, raw_response=raw, usage=invoke.Usage(1, 1, 0), duration_s=0.0
    )
    started = time.monotonic()

    out = invoke._maybe_correct_literal_excerpts(
        result,
        prompt="You are an adversarial QE: assume bugs exist.",
        backend=None,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=DIFF,
        allowance=None,
        entry_started_at=started,
        entry_deadline=started + 60,
        audit_scope=None,
        timeout_s=30,
    )

    assert seen["_audit_cause"] == "excerpt_repair"
    assert seen["_no_continuation"] is True
    assert out.excerpt_diagnostics[0]["selected"] is True


def test_correction_flags_reach_the_api_layer(monkeypatch):
    """Mock the API layer, not the public entry, so the flags are observed.

    Stubbing `llm_invoke` only proves the caller builds the right kwargs; this
    proves `llm_invoke` forwards them instead of overwriting the cause with its
    own default.
    """
    from code_forge.backend import BackendConfig
    from code_forge.reviewer_json import REVIEW_REPLY_KEYS

    seen = {}
    content = {
        "findings": [],
        "code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 3, "content": BAD}],
    }
    repaired = {
        "findings": [],
        "code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 3, "content": GOOD}],
    }

    def fake_api(*args, **kwargs):
        seen.update(kwargs)
        return invoke.LLMResult(
            content=repaired,
            raw_response=json.dumps(repaired, indent=2),
            usage=invoke.Usage(2, 3, 0),
            duration_s=0.0,
        )

    monkeypatch.setattr(invoke, "_invoke_api", fake_api)
    backend = BackendConfig(
        name="literal-test",
        type="api",
        model="fixture",
        format="openai",
        base_url="https://example.invalid",
        api_key_env="EXCERPT_TEST_KEY",
        timeout_s=10,
    )
    result = invoke.LLMResult(
        content=content,
        raw_response=json.dumps(content, indent=2),
        usage=invoke.Usage(1, 1, 0),
        duration_s=0.0,
    )
    started = time.monotonic()
    invoke._maybe_correct_literal_excerpts(
        result,
        prompt="You are an adversarial QE: assume bugs exist.",
        backend=backend,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=DIFF,
        allowance=None,
        entry_started_at=started,
        entry_deadline=started + 60,
        audit_scope=None,
        timeout_s=30,
    )

    assert seen["_audit_cause"] == "excerpt_repair"
    assert seen["_no_continuation"] is True
    assert seen["strict_response"] is True

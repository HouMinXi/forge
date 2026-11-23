"""Human clarification must not reclassify evidence or change machine output."""

import copy
import json
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import ModuleType
from urllib.error import URLError

import pytest

from code_forge.disposition import Disposition
from code_forge.receipt_provenance import receipt_finding_provenance
from code_forge.report_presentation import prepare_legacy_report, prepare_report_views
from code_forge.report_projection import classify_report_records
from code_forge.sarif import build_sarif_log, format_classification_line, format_summary
from code_forge.state import State, StateFinding, Verdict


def _receipt(index=0, **overrides):
    fields = dict(
        id="RECEIPT_INVALID",
        fingerprint=f"receipt-{index:012x}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="private diagnostic details",
        receipt_subtype="RECEIPT_EVIDENCE",
    )
    fields.update(overrides)
    return StateFinding(**fields)


def _render(findings):
    state = State(verdict=Verdict.FAIL, findings=findings)
    state_before = copy.deepcopy(state)
    sarif = build_sarif_log(state, {}, "test")
    substrate = prepare_legacy_report(sarif, stdout=json.dumps(sarif), stderr=format_summary(state))
    prepared = prepare_report_views(substrate, classify_report_records(state))
    prepared_before = copy.deepcopy(prepared)
    line = format_classification_line(prepared.report_classification)
    assert prepared == prepared_before
    assert state == state_before
    assert build_sarif_log(state, {}, "test") == sarif
    return state, prepared, line


def _legacy_classification(count):
    return (
        f"code-forge: classification product={count} "
        f"[not adjudicated: confirmed={count} dismissed=0 fixed=0 style=0; "
        "with-observations=0] unresolved=0 [uncertain; with-observations=0] "
        "quarantine=0 [unverified]"
    )


def test_five_receipt_errors_are_explained_without_reducing_counts():
    findings = [_receipt(0, receipt_subtype="RECEIPT_INVALID_GATE")]
    findings.extend(_receipt(i) for i in range(1, 5))
    state, prepared, line = _render(findings)
    assert format_summary(state) == (
        "code-forge: FAIL findings=5 confirmed=5 uncertain=0 dismissed=0 fixed=0 infra=5"
    )
    assert prepared.report_classification.product_list_count == 5
    assert line == _legacy_classification(5) + (
        " host-receipt-diagnostics=5 [included in product/confirmed counts; "
        "not source-code findings; still block review]"
    )
    assert "private diagnostic details" not in line


def test_mixed_product_and_evidence_counts_remain_unchanged():
    product = _receipt(1, id="real-bug", fingerprint="real-bug", source="L0", file="main.py")
    state, prepared, line = _render([product, _receipt()])
    assert "findings=2 confirmed=2" in format_summary(state)
    assert prepared.report_classification.product_list_count == 2
    assert line.startswith(_legacy_classification(2))
    assert "host-receipt-diagnostics=1 " in line


@pytest.mark.parametrize(
    "subtype", ["RECEIPT_SCHEMA", "RECEIPT_COVERAGE", "RECEIPT_EVIDENCE", "RECEIPT_INVALID_GATE"]
)
def test_known_host_receipt_labels_are_explained(subtype):
    _, _, line = _render([_receipt(receipt_subtype=subtype)])
    assert "host-receipt-diagnostics=1 " in line


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "UNTRUSTED"},
        {"source": "L1"},
        {"id": "unknown-infra", "fingerprint": "unknown"},
    ],
)
def test_untrusted_or_unknown_records_keep_original_human_bytes(overrides):
    _, _, line = _render([_receipt(**overrides)])
    assert line == _legacy_classification(1)


def test_unavailable_classification_keeps_original_bytes():
    class Unavailable:
        availability = "unavailable"
        unavailable_reason = "no-report"

    assert format_classification_line(Unavailable()) == (
        "code-forge: classification unavailable (no-report)"
    )


def test_falsifier_observation_is_not_relabelled_as_receipt_failure():
    _, prepared, line = _render(
        [_receipt(disposition=Disposition.UNCERTAIN, error="falsify() backend timed out: no response")]
    )
    assert len(prepared.report_classification.unresolved_product_candidates) == 1
    assert "host-receipt-diagnostics" not in line


def _invoke_failure(pass_name, error=None):
    from code_forge.state import record_provider_failure

    finding = StateFinding(
        id=f"l1-{pass_name}-invoke-fail",
        fingerprint=f"invoke-fail-{pass_name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="Review response could not be completed",
    )
    if error is not None:
        record_provider_failure(finding, error, include_private_details=False)
    return finding


def test_cli_adds_only_human_clarification(capsys, tmp_path):
    from code_forge import __version__, cli
    from code_forge.llm_invoke import InvalidJSONResponseError, _TruncatedResponse
    from code_forge.state import Mode, derive_pass_outcomes, load_state, save_state

    protocol = _invoke_failure(
        "qodo",
        InvalidJSONResponseError(
            "API response content is not valid JSON",
            raw_response="The review completed.",
            exit_code=0,
            kind="no_json",
            retryable=False,
        ),
    )
    truncated = _invoke_failure(
        "expert",
        _TruncatedResponse(
            "Review output reached its configured token limit",
            content="",
            usage_data={"prompt_tokens": 64, "completion_tokens": 32},
            resolved_cap=32,
            kind="truncated",
            retryable=False,
        ),
    )
    product = StateFinding(
        "doc-example",
        "doc-example",
        "L1",
        Disposition.CONFIRMED,
        "docs/example.md",
        [7, 7],
        "The example omits its required input.",
    )
    legacy = _invoke_failure("adversarial")
    cases = [
        ("receipt", [_receipt()], 1, 0),
        ("protocol", [protocol], 1, 1),
        ("capacity", [truncated], 0, 0),
        ("product", [product], 1, 0),
        ("legacy", [legacy], 1, 0),
        ("mixed", [protocol, truncated, product, legacy], 3, 1),
    ]
    path = tmp_path / "state.json"
    for name, findings, products, failures in cases:
        save_state(State(mode=Mode.CI, verdict=Verdict.FAIL, findings=findings), path)
        snapshot = path.read_bytes()
        state = load_state(path)
        projection = classify_report_records(state)
        outcomes = derive_pass_outcomes(state.findings)
        summary = format_summary(state)
        log = build_sarif_log(state, {}, __version__)
        prepared = prepare_report_views(
            prepare_legacy_report(
                log,
                stdout=json.dumps(log, ensure_ascii=False, allow_nan=False),
                stderr=summary,
            ),
            projection,
        )
        assert projection.product_list_count == products, name
        assert len(log["runs"][0]["results"]) == products, name

        cli._emit_ci_output(path, {})
        output = capsys.readouterr()
        assert output.out == prepared.stdout + "\n", name
        expected_stderr = [summary, format_classification_line(prepared.report_classification)]
        if failures:
            expected_stderr.append(
                f"code-forge: review service protocol failures={failures} [included in findings]"
            )
        assert output.err.splitlines() == expected_stderr, name
        assert path.read_bytes() == snapshot, name
        restored = load_state(path)
        assert restored.verdict is Verdict.FAIL and not restored.converged, name
        assert classify_report_records(restored) == projection, name
        assert derive_pass_outcomes(restored.findings) == outcomes, name


def _substitute_vertex_io(monkeypatch, response_body):
    # Keep the Vertex adapter and L1 producer real, including error typing.
    from code_forge import llm_invoke

    for name in (
        "google",
        "google.auth",
        "google.auth.exceptions",
        "google.auth.transport",
        "google.auth.transport.requests",
        "google.oauth2",
        "google.oauth2.service_account",
    ):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, module)

    class RefreshError(Exception):
        pass

    class Credentials:
        token = "fixture-token"
        refreshes = 0

        def refresh(self, request):
            self.refreshes += 1
            if self.refreshes == 1:
                raise RefreshError("fixture refresh failed")

    credentials = Credentials()
    sys.modules["google.auth"].default = lambda *, scopes: (credentials, None)
    sys.modules["google.auth.exceptions"].DefaultCredentialsError = type(
        "DefaultCredentialsError", (Exception,), {}
    )
    sys.modules["google.auth.exceptions"].RefreshError = RefreshError
    sys.modules["google.auth.transport.requests"].Request = object

    responses = iter([URLError(ConnectionRefusedError("fixture connection refused")), response_body])

    def open_url(request, timeout_s):
        response = next(responses)
        if isinstance(response, URLError):
            raise response
        return BytesIO(response)

    monkeypatch.setattr(llm_invoke, "_open_url", open_url)
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")


@pytest.fixture
def ci_provider_failure_state(tmp_path, monkeypatch, report):
    from code_forge import cli
    from code_forge.autofix import StubAutoFixer
    from code_forge.backend import BackendConfig
    from code_forge.baseline import ResolvedReview
    from code_forge.factories import build_l1_provider
    from code_forge.falsify import StubFalsifier
    from code_forge.machine import StateMachine
    from code_forge.source import compute_source_hash
    from code_forge.state import Mode, load_state
    from tests._outside_gate import outside_gate_root

    response_body = b"not JSON"
    last_failure = ("PROVIDER_PROTOCOL", "bad_body", "execution")
    if report == "empty-content":
        response_body = json.dumps(
            {
                "content": [{"type": "text", "text": " \t\n"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 8, "output_tokens": 1},
            }
        ).encode("utf-8")
        last_failure = ("PROVIDER_EMPTY", "empty", "execution")
    _substitute_vertex_io(monkeypatch, response_body)
    monkeypatch.chdir(tmp_path)
    path = Path("docs/example.md")
    path.parent.mkdir()
    path.write_text("Run the example.\n", encoding="utf-8")
    diff = (
        "diff --git a/docs/example.md b/docs/example.md\n"
        "--- a/docs/example.md\n+++ b/docs/example.md\n"
        "@@ -1 +1 @@\n-See the example.\n+Run the example.\n"
    )
    resolved = ResolvedReview([path], None, diff, "git")
    backend = BackendConfig(
        name="offline", type="api", format="vertex", model="fixture", project_id="fixture"
    )
    cli._check_backend_credentials(backend, {})
    product = StateFinding(
        "doc-example",
        "doc-example",
        "L0",
        Disposition.CONFIRMED,
        str(path),
        [1, 1],
        "The example omits its required input.",
    )
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=diff),
        baseline_spec_repr="offline CI",
        cwd=tmp_path,
        test_config_root=outside_gate_root(tmp_path),
        registry={},
        l0_runner=lambda *_: ([product], []),
        l1_provider=build_l1_provider("auto", resolved, backend=backend, max_attempts=1),
    )
    assert machine.run() is Verdict.FAIL
    state_path = tmp_path / ".code-forge" / "state.json"
    state = load_state(state_path)
    assert state.verdict is Verdict.FAIL and not state.converged
    projection = classify_report_records(state)
    assert projection.classification_complete
    assert [
        (
            record.host_failure_kind,
            finding.provider_failure["kind"],
            receipt_finding_provenance(finding)["channel"],
        )
        for record in projection.product
        if (finding := state.findings[record.index]).provider_failure is not None
    ] == [
        ("PROVIDER_AUTH", "credentials", "execution"),
        ("PROVIDER_TRANSPORT", "conn", "execution"),
        last_failure,
    ]
    assert any(state.findings[record.index].id == product.id for record in projection.product)
    return state_path, state


@pytest.mark.parametrize("report", ["produced", "product-candidates", "unavailable", "empty-content"])
def test_cli_counts_only_classified_host_provider_failures(
    report, ci_provider_failure_state, capsys, monkeypatch, tmp_path
):
    from code_forge import __version__, cli, report_presentation
    from code_forge.state import save_state

    path, state = ci_provider_failure_state
    if report == "product-candidates":
        overrides = {"credentials": {"fingerprint": "ambiguous"}, "conn": {"source": "UNTRUSTED"}}
        for i, finding in enumerate(state.findings):
            kind = (finding.provider_failure or {}).get("kind")
            if kind in overrides:
                state.findings[i] = replace(finding, **overrides[kind])
        path = tmp_path / "product-candidates.json"
        save_state(state, path)
    snapshot = path.read_bytes()
    capsys.readouterr()

    projection = classify_report_records(state)
    assert projection.classification_complete
    if report == "product-candidates":
        assert [
            (
                record.host_failure_kind,
                receipt_finding_provenance(state.findings[record.index])["channel"],
            )
            for record in projection.product
            if record.host_failure_kind in ("PROVIDER_AUTH", "PROVIDER_TRANSPORT")
        ] == [("PROVIDER_AUTH", "product"), ("PROVIDER_TRANSPORT", "product")]
    available = report != "unavailable"
    if not available:
        monkeypatch.setattr(report_presentation, "_block_fits", lambda *_: False)
    log = build_sarif_log(state, {}, __version__)
    substrate = prepare_legacy_report(
        log, stdout=json.dumps(log, ensure_ascii=False, allow_nan=False), stderr=format_summary(state)
    )
    prepared = prepare_report_views(substrate, projection)
    assert prepared.report_classification.availability == ("available" if available else "unavailable")
    expected = prepared.stderr.splitlines() + [
        format_classification_line(prepared.report_classification)
    ]
    labels = {
        "produced": ("protocol failures", "transport failures", "authentication failures"),
        "product-candidates": ("protocol failures",),
        "unavailable": (),
        "empty-content": (
            "transport failures",
            "authentication failures",
            "responses with no usable review content",
        ),
    }
    expected.extend(
        f"code-forge: review service {label}=1 [included in findings]" for label in labels[report]
    )
    expected.extend(cli._format_infra_lines(state, state_path=path).splitlines())

    cli._emit_ci_output(path, {})
    output = capsys.readouterr()
    assert output.out == prepared.stdout + "\n"
    assert path.read_bytes() == snapshot
    assert output.err.splitlines() == expected

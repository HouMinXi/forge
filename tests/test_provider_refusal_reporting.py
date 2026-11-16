"""Explicit refusals remain acquisition failures in operator-facing consumers."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from code_forge.backend import BackendConfig, probe_backend_live
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.llm_invoke import ProviderRefusalError
from code_forge.state import (
    Disposition,
    PassOutcome,
    StateFinding,
    derive_pass_outcomes,
    record_provider_failure,
)


def _backend():
    return BackendConfig(name="offline", type="api", format="openai", model="offline")


@pytest.mark.parametrize("include_private_details", [True, False])
def test_refusal_kind_survives_public_metadata_filter(include_private_details):
    finding = StateFinding(
        id="l1-qodo-invoke-fail",
        fingerprint="invoke-fail-qodo",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="acquisition failed",
    )
    error = ProviderRefusalError(_backend(), {"prompt_tokens": 5, "completion_tokens": 7})
    record_provider_failure(finding, error, include_private_details=include_private_details)
    assert finding.provider_failure["kind"] == "refusal"
    assert finding.provider_failure["usage"]["input_tokens"] == 5
    assert finding.provider_failure["usage"]["output_tokens"] == 7
    assert not finding.is_timeout and finding.diagnostic_kind is None
    assert derive_pass_outcomes([finding])["qodo"] == PassOutcome.ERROR


def test_probe_reports_refusal_without_schema_or_capacity_advice(monkeypatch):
    error = ProviderRefusalError(_backend(), {"prompt_tokens": 5, "completion_tokens": 7})
    request = Mock(side_effect=error)
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", request)
    result = probe_backend_live(_backend())
    assert request.call_count == 1
    assert not result.ok and result.error_class == "provider-refusal"
    assert result.detail == str(error)
    assert "declined" in result.suggestion


def test_l1_refusal_produces_infra_without_response_credit(monkeypatch, capsys):
    def refused(*args, **kwargs):
        raise ProviderRefusalError(_backend(), {"prompt_tokens": 5, "completion_tokens": 7})

    request = Mock(side_effect=refused)
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", request)
    resolved = ResolvedReview(
        source_files=[Path("a.py")],
        baseline_content=None,
        git_diff="diff --git a/a.py b/a.py",
        mode_hint="git",
    )
    provider = build_l1_provider("real", resolved, backend=_backend(), max_attempts=1)
    findings, excerpts, usage, _duration = provider()
    assert request.call_count == 3
    assert len(findings) == 3 and excerpts == []
    assert all(f.source == "INFRA" and f.disposition == Disposition.CONFIRMED for f in findings)
    assert all(f.provider_failure["kind"] == "refusal" for f in findings)
    assert provider.reviewer_responses is None
    assert provider.raw_observations == []
    assert provider.acquisition_failures == findings
    assert (usage.input_tokens, usage.output_tokens) == (15, 21)
    assert all(outcome == PassOutcome.ERROR for outcome in derive_pass_outcomes(findings).values())
    assert "refused the request" in capsys.readouterr().err

# SPDX-License-Identifier: Apache-2.0
"""#369: external API-shape claims cannot be verified by model-authored receipts."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.state import StateFinding

ISSUE_369 = (
    "[adversarial] `Set-NetFirewallRule` is invoked with a `-RemoteAddress` argument, "
    "but the NetSecurity cmdlet `Set-NetFirewallRule` does not expose a `-RemoteAddress` "
    "parameter (only `New-NetFirewallRule` does). This path fires whenever an existing "
    "firewall rule must be retightened ..."
)


def run_falsifier(description, verdict="CONFIRMED", **extra):
    finding = StateFinding.__new__(StateFinding)
    finding.file = "script.ps1"
    finding.line_range = [1, 1]
    finding.description = description
    finding.fingerprint = "api-shape-regression"
    response = {"verdict": verdict, "reasoning": "verified", **extra}
    with patch("code_forge.falsify_real.llm_invoke", return_value=SimpleNamespace(content=response)):
        disposition = RealFalsifier().falsify(finding)
    return disposition, finding.falsify_reasoning


@pytest.mark.parametrize(
    "description",
    [
        ISSUE_369,
        "Set-NetFirewallRule\ndoes not expose a -RemoteAddress parameter.",
        "Set-NetFirewallRule has no -RemoteAddress parameter.",
        "Set-NetFirewallRule does not have a -RemoteAddress parameter.",
        "Set-NetFirewallRule没有-RemoteAddress参数。",
        "PowerShell cmdlet Set-Example does not support the -Scope parameter.",
        "The external API client.send does not accept a timeout argument.",
        "The library API client.send exposes a retry parameter.",
        "The external API Widget.from_json does not exist.",
        "The third-party API has no symbol Widget.",
        "Set-NetFirewallRule 没有 -RemoteAddress 参数。",
        "PowerShell 命令 Set-Example 不支持 -Scope 参数。",
        "第三方 API client.send 不接受 timeout 参数。",
        "外部 API Widget.from_json 不存在。",
    ],
)
def test_api_shape_confirmation_without_trusted_evidence_is_uncertain(description):
    disposition, reason = run_falsifier(description)
    assert disposition == Disposition.UNCERTAIN
    assert "API-shape" in reason
    assert "host-verified" in reason


@pytest.mark.parametrize(
    "extra",
    [
        {"receipt": {"command": "Get-Command Set-NetFirewallRule", "output": "no parameter"}},
        {"reasoning": "Verified at https://learn.microsoft.com/powershell/"},
        {"receipt": {"command": "Get-Command X", "output": "False", "source": "host"}},
    ],
)
def test_model_authored_receipt_citation_or_source_cannot_supply_provenance(extra):
    disposition, reason = run_falsifier(ISSUE_369, **extra)
    assert disposition == Disposition.UNCERTAIN
    assert "host-verified" in reason


def test_external_shape_claim_in_verifier_reasoning_is_also_unverified():
    disposition, _ = run_falsifier("This call is broken.", reasoning=ISSUE_369)
    assert disposition == Disposition.UNCERTAIN


@pytest.mark.parametrize(
    "description",
    [
        "Our helper has no timeout parameter after this patch removes it.",
        "The internal function client.send has no timeout argument.",
        "The patch removes the timeout parameter but the caller still supplies it.",
        "Set-NetFirewallRule is invoked without the required value from our config.",
        "The external API call is skipped when the local parameter is empty.",
        "The early return leaves the lock held.",
        "Set-NetFirewallRule 没有参数值，因为配置为空。",
        "Set-NetFirewallRule 没有参数的值，因为配置为空。",
        "Set-NetFirewallRule has no argument value because our configuration is empty.",
        "The external API call has no parameter value because config is empty.",
        "Before calling Set-NetFirewallRule, our helper has no timeout parameter, so its new caller fails.",
        "调用 Set-NetFirewallRule 前，内部 helper 没有 timeout 参数，但新增调用方仍传入。",
        "The external API call is fine, but the internal helper has no timeout parameter after this diff removes it.",
        "The external API call has its arguments reversed by the new wrapper.",
        "Set-NetFirewallRule receives an empty address because our helper has discarded the parameter.",
        "Set-NetFirewallRule 的调用没有传入 RemoteAddress 参数。",
        "内部函数没有 timeout 参数，调用方仍然传入。",
        "这里没有把 RemoteAddress 参数传给 Set-NetFirewallRule。",
    ],
)
def test_internal_logic_and_missing_call_arguments_keep_existing_verdict(description):
    assert run_falsifier(description)[0] == Disposition.CONFIRMED


@pytest.mark.parametrize("verdict", ["DISMISSED", "UNCERTAIN"])
def test_new_guard_does_not_change_other_dispositions(verdict):
    assert run_falsifier(ISSUE_369, verdict)[0] == Disposition(verdict)

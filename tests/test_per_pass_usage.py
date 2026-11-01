# SPDX-License-Identifier: Apache-2.0
"""Measured role usage survives factory dispatch and state persistence."""

import json
import io
import threading
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import InvalidJSONResponseError, LLMInvokeError, LLMResult, Usage
from code_forge.machine import StateMachine
from code_forge.state import Mode, load_state


ROLES = ("qodo", "expert", "adversarial")
MEASUREMENTS = ((11, 5, 7, 1.125), (23, 13, 17, 2.25), (47, 19, 29, 4.5))
DIFF = (
    "diff --git a/sample.py b/sample.py\n"
    "--- a/sample.py\n+++ b/sample.py\n"
    "@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
)


def resolved(diff=DIFF):
    return ResolvedReview(
        source_files=[Path("sample.py")],
        baseline_content=None,
        git_diff=diff,
        mode_hint="git",
    )


def response(values):
    input_tokens, output_tokens, cached, duration = values
    return LLMResult(
        content=json.dumps(
            {
                "findings": [],
                "code_excerpts": [
                    {"file": "sample.py", "content": "VALUE = 2", "start_line": 1, "end_line": 1}
                ],
            }
        ),
        usage=Usage(input_tokens, output_tokens, cached),
        duration_s=duration,
    )


def expected_rows(measurements=MEASUREMENTS):
    return [
        {
            "pass_name": role,
            "input": values[0],
            "output": values[1],
            "cached": values[2],
            "duration_s": values[3],
            "available": True,
        }
        for role, values in zip(ROLES, measurements, strict=True)
    ]


def persisted_rows(rows, cycle):
    return [dict(row, **{"pass": ROLES.index(row["pass_name"]) + 1, "cycle": cycle}) for row in rows]


def install_responses(monkeypatch, measurements=MEASUREMENTS):
    results = iter(response(values) for values in measurements)
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", lambda *a, **k: next(results))


def make_machine(tmp_path, provider, review=None):
    (tmp_path / "sample.py").write_text("VALUE = 2\n")
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=review or resolved(),
        source_hash="usage-source",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda r, f: ([], []),
        l1_provider=provider,
        l2_runner=lambda: ([], []),
        e2e_runner=lambda *a: ([], []),
    )


@pytest.mark.parametrize("backend_type", ["cli", "api"])
def test_factory_unequal_usage_and_dispatch(monkeypatch, backend_type):
    backend = SimpleNamespace(name="controlled", type=backend_type)
    barrier = threading.Barrier(3) if backend_type == "api" else None
    active = 0
    peak = 0
    lock = threading.Lock()

    def invoke(prompt, **kwargs):
        nonlocal active, peak
        role_index = next(
            i
            for i, role in enumerate(
                (
                    "structural code reviewer",
                    "senior engineer",
                    "adversarial QE",
                )
            )
            if role in prompt
        )
        assert kwargs["backend"] is backend
        with lock:
            active += 1
            peak = max(peak, active)
        if barrier is not None:
            barrier.wait(timeout=5)
        with lock:
            active -= 1
        return response(MEASUREMENTS[role_index])

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", invoke)
    provider = build_l1_provider("real", resolved(), backend=backend)
    result = provider()
    assert len(result) == 4
    assert result[2] == Usage(81, 37, 53)
    assert provider.pass_usage == expected_rows()
    assert peak == (3 if barrier else 1)
    if barrier:
        assert 0 < result[3] < sum(values[3] for values in MEASUREMENTS)
    else:
        assert result[3] == 7.875


def test_factory_rejected_validation_retains_observed_usage(monkeypatch):
    def invoke(*args, **kwargs):
        return replace(response(MEASUREMENTS[0]), content="{}")

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", invoke)
    provider = build_l1_provider("real", resolved())
    findings, _, usage, duration = provider()
    assert findings
    assert usage == Usage(33, 15, 21)
    assert duration == 3.375
    assert provider.pass_usage == expected_rows((MEASUREMENTS[0],) * 3)


@pytest.mark.parametrize(
    "error", [LLMInvokeError("unavailable", duration_s=3.0), RuntimeError("unavailable")]
)
def test_factory_failure_has_unavailable_numbers(monkeypatch, tmp_path, error):
    def invoke(*args, **kwargs):
        raise error

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", invoke)
    provider = build_l1_provider("real", resolved())
    findings, _, usage, _ = provider()
    assert len(findings) == 3
    assert usage == Usage()
    assert provider.pass_usage == [
        {
            "pass_name": role,
            "input": None,
            "output": None,
            "cached": None,
            "duration_s": None,
            "available": False,
        }
        for role in ROLES
    ]
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert cost["per_pass"] == persisted_rows(provider.pass_usage, 0)
    assert all(
        row["available"] is False
        and row["input"] is None
        and row["output"] is None
        and row["cached"] is None
        and row["duration_s"] is None
        for row in cost["per_pass"]
    )


def test_factory_reset_prevents_stale_usage(monkeypatch):
    review = resolved()
    install_responses(monkeypatch, MEASUREMENTS + ((0, 0, 0, 0.0),) * 3)
    provider = build_l1_provider("real", review)
    provider()
    first = provider.pass_usage
    assert provider()[2:] == (Usage(), 0.0)
    assert provider.pass_usage == expected_rows(((0, 0, 0, 0.0),) * 3)
    assert first == expected_rows()


def test_factory_observed_zero_is_available(monkeypatch):
    measurements = ((0, 0, 0, 0.0),) * 3
    install_responses(monkeypatch, measurements)
    provider = build_l1_provider("real", resolved())
    assert provider()[2:] == (Usage(), 0.0)
    assert provider.pass_usage == expected_rows(measurements)


def test_grouped_usage_preserves_each_group_and_role(monkeypatch):
    second = tuple((i + 1, o + 2, c + 3, d + 0.5) for i, o, c, d in MEASUREMENTS)
    install_responses(monkeypatch, MEASUREMENTS + second)
    provider = build_grouped_l1_provider(
        "real",
        [
            {"name": "first", "resolved": resolved()},
            {"name": "second", "resolved": resolved()},
        ],
    )
    _, _, usage, duration = provider()
    assert usage == Usage(165, 80, 115)
    assert duration == 17.25
    assert provider.pass_usage == (
        [dict(row, group="first") for row in expected_rows()]
        + [dict(row, group="second") for row in expected_rows(second)]
    )


@pytest.mark.parametrize("kind", ["empty", "stub", "grouped-empty", "grouped-stub"])
def test_factory_known_empty_has_no_pass_rows(monkeypatch, tmp_path, kind):
    def unexpected(*args, **kwargs):
        pytest.fail("empty and stub providers must not invoke a backend")

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", unexpected)
    if kind.startswith("grouped"):
        provider = build_grouped_l1_provider(
            "stub" if kind == "grouped-stub" else "real",
            [{"name": "first", "resolved": resolved()}] if kind == "grouped-stub" else [],
        )
    else:
        provider = build_l1_provider("stub" if kind == "stub" else "real", resolved(""))
    assert provider() == ([], [], Usage(), 0.0)
    assert provider.pass_usage == []
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    assert machine._state.cost_per_pass == []
    assert machine._state.cost_passes == 0


def test_machine_factory_rows_persist_and_reload(monkeypatch, tmp_path):
    install_responses(monkeypatch)
    provider = build_l1_provider("real", resolved())
    machine = make_machine(tmp_path, provider)
    machine._execute_round(4)
    expected = persisted_rows(expected_rows(), 4)
    assert machine._state.cost_per_pass == expected
    path = tmp_path / ".code-forge/state.json"
    cost = json.loads(path.read_text())["cost"]
    assert cost == {
        "total_input_tokens": 81,
        "total_output_tokens": 37,
        "total_cached_tokens": 53,
        "total_duration_s": 7.875,
        "passes": 3,
        "per_pass": expected,
    }
    assert load_state(path).cost_per_pass == expected
    provider.pass_usage[0]["input"] = 999
    assert machine._state.cost_per_pass == expected


def test_machine_grouped_rows_keep_role_identity(monkeypatch, tmp_path):
    install_responses(monkeypatch, MEASUREMENTS * 2)
    provider = build_grouped_l1_provider(
        "real", [{"name": name, "resolved": resolved()} for name in ("first", "second")]
    )
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    rows = [dict(row, group=name) for name in ("first", "second") for row in expected_rows()]
    assert machine._state.cost_per_pass == persisted_rows(rows, 0)
    assert machine._state.cost_passes == 6
    assert [row["pass"] for row in machine._state.cost_per_pass] == [1, 2, 3, 1, 2, 3]


def test_machine_legacy_provider_has_explicit_unavailable_slots(tmp_path):
    machine = make_machine(tmp_path, lambda: ([], [], Usage(81, 37, 53), 7.875))
    machine._execute_round(0)
    rows = [
        {
            "pass_name": role,
            "input": None,
            "output": None,
            "cached": None,
            "duration_s": None,
            "available": False,
        }
        for role in ROLES
    ]
    assert machine._state.cost_per_pass == persisted_rows(rows, 0)
    assert machine._state.cost_total_input == 81
    assert machine._state.cost_total_output == 37
    assert machine._state.cost_total_cached == 53
    assert machine._state.cost_total_duration == 7.875


def test_machine_reset_drops_prior_round_metadata(monkeypatch, tmp_path):
    review = resolved()
    install_responses(monkeypatch)
    provider = build_l1_provider("real", review)
    machine = make_machine(tmp_path, provider, review)
    machine._execute_round(0)
    assert machine._round_pass_usage is None
    machine.l1_provider = build_l1_provider("real", resolved(""))
    machine._execute_round(1)
    assert machine._round_pass_usage is None
    assert machine._state.cost_per_pass == persisted_rows(expected_rows(), 0)
    assert machine._state.cost_passes == 3
    assert machine._state.cost_total_input == 81
    assert machine._state.cost_total_output == 37
    assert machine._state.cost_total_cached == 53
    assert machine._state.cost_total_duration == 7.875


def test_machine_api_latency_preserves_parallel_wall_time(monkeypatch, tmp_path):
    barrier = threading.Barrier(3)

    def invoke(prompt, **kwargs):
        role_index = next(
            i
            for i, role in enumerate(
                (
                    "structural code reviewer",
                    "senior engineer",
                    "adversarial QE",
                )
            )
            if role in prompt
        )
        barrier.wait(timeout=5)
        return response(MEASUREMENTS[role_index])

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", invoke)
    provider = build_l1_provider(
        "real",
        resolved(),
        backend=SimpleNamespace(type="api", name="controlled"),
    )
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert cost["per_pass"] == persisted_rows(expected_rows(), 0)
    assert cost["total_input_tokens"] == 81
    assert cost["total_output_tokens"] == 37
    assert cost["total_cached_tokens"] == 53
    assert 0 < cost["total_duration_s"] < 7.875
    assert sum(row["duration_s"] for row in cost["per_pass"]) == 7.875


@pytest.mark.parametrize("error_type", [LLMInvokeError, InvalidJSONResponseError])
@pytest.mark.parametrize("known_zero", [False, True])
@pytest.mark.parametrize("backend_type", ["cli", "api"])
def test_factory_acquired_error_usage_persists(
    monkeypatch,
    tmp_path,
    error_type,
    known_zero,
    backend_type,
):
    measurements = list(MEASUREMENTS)
    if known_zero:
        measurements[1] = (0, 0, 0, MEASUREMENTS[1][3])
    error_kwargs = {"raw_response": "{}"} if error_type is InvalidJSONResponseError else {}
    error = error_type(
        "acquired response rejected",
        usage=Usage(*measurements[1][:3]),
        duration_s=measurements[1][3],
        **error_kwargs,
    )
    barrier = threading.Barrier(3) if backend_type == "api" else None

    def invoke(prompt, **kwargs):
        role_index = next(
            i
            for i, role in enumerate(
                (
                    "structural code reviewer",
                    "senior engineer",
                    "adversarial QE",
                )
            )
            if role in prompt
        )
        if barrier is not None:
            barrier.wait(timeout=5)
        if role_index == 1:
            raise error
        return response(measurements[role_index])

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", invoke)
    provider = build_l1_provider(
        "real",
        resolved(),
        backend=SimpleNamespace(type=backend_type, name="controlled"),
    )
    _, _, usage, duration = provider()
    assert provider.pass_usage == expected_rows(measurements)
    assert usage == Usage(*(sum(values[i] for values in measurements) for i in range(3)))
    if backend_type == "cli":
        assert duration == 7.875
    else:
        assert 0 < duration < 7.875
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert cost["per_pass"] == persisted_rows(expected_rows(measurements), 0)
    assert cost["total_input_tokens"] == usage.input_tokens
    assert cost["total_output_tokens"] == usage.output_tokens
    assert cost["total_cached_tokens"] == usage.cached_input_tokens
    if backend_type == "cli":
        assert cost["total_duration_s"] == 7.875
    else:
        assert 0 < cost["total_duration_s"] < 7.875


def test_factory_default_usage_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke",
        lambda *a, **k: LLMResult(
            content=response(MEASUREMENTS[0]).content,
        ),
    )
    machine = make_machine(tmp_path, build_l1_provider("real", resolved()))
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert all(
        row["available"] is False
        and row["input"] is None
        and row["output"] is None
        and row["cached"] is None
        for row in cost["per_pass"]
    )


@pytest.mark.parametrize("kind", ["stub", "empty", "nonempty", "grouped"])
def test_machine_same_source_resume_retains_cost(monkeypatch, tmp_path, capsys, kind):
    install_responses(monkeypatch, MEASUREMENTS * 3)
    first = make_machine(tmp_path, build_l1_provider("real", resolved()))
    first._state.source_hash = first.source_hash
    first._execute_round(0)
    original = list(first._state.cost_per_pass)
    if kind == "grouped":
        provider = build_grouped_l1_provider(
            "real", [{"name": name, "resolved": resolved()} for name in ("first", "second")]
        )
    else:
        provider = build_l1_provider(
            "stub" if kind == "stub" else "real", resolved("" if kind == "empty" else DIFF)
        )
    resumed = make_machine(tmp_path, provider)
    resumed._maybe_load_prior_state()
    resumed._execute_round(1)
    attempted = 6 if kind == "grouped" else 3 if kind == "nonempty" else 0
    state = load_state(tmp_path / ".code-forge/state.json")
    assert state.cost_per_pass[:3] == original
    assert state.cost_passes == len(state.cost_per_pass) == 3 + attempted
    assert state.cost_total_input == 81 * (1 + attempted // 3)
    from code_forge.sarif import build_sarif_log
    from code_forge.state import Verdict
    from code_forge import cli

    state.verdict = Verdict.PASS
    sarif = build_sarif_log(state, {}, "test", backend_name="controlled")
    assert sarif["runs"][0]["properties"]["tokenCost"]["passes"] == state.cost_passes
    monkeypatch.setattr(cli, "StateMachine", lambda **kwargs: SimpleNamespace(run=lambda: Verdict.PASS))
    monkeypatch.setattr(cli, "build_l2_runner", lambda **kwargs: None)
    monkeypatch.setattr(cli, "build_e2e_checker", lambda: None)
    cli._run_hold_loop(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        l1_provider=provider,
        resolved=resolved(),
        source_hash="usage-source",
        baseline_repr="",
        cwd=tmp_path,
        registry={},
        max_rounds=1,
        max_fix_attempts=1,
        state_path=tmp_path / ".code-forge/state.json",
        coverage_l1_active=False,
    )
    assert ", %d passes, " % (3 + attempted) in capsys.readouterr().err


def native_transport(monkeypatch, api_format, usage_case, rejected=False, content=None):
    """Replace only subprocess/HTTP transport; preserve production parsing."""
    from code_forge import llm_invoke as invoke
    from code_forge.backend import BackendConfig

    if content is None:
        content = response(MEASUREMENTS[0]).content
    if rejected:
        content = "{invalid json}"
    openai = api_format == "openai"
    in_key, out_key = (
        ("prompt_tokens", "completion_tokens") if openai else ("input_tokens", "output_tokens")
    )
    cache = {"prompt_tokens_details": {"cached_tokens": 0}} if openai else {"cache_read_input_tokens": 0}
    reports = {
        "absent": None,
        "null": None,
        "invalid": {in_key: -1, out_key: False, **{key: None for key in cache}},
        "partial": {in_key: 11},
        "output-only": {out_key: 5},
        "primary": {in_key: 11, out_key: 5},
        "malformed": {in_key: 11, out_key: None, **cache},
        "zero": {in_key: 0, out_key: 0, **cache},
        "full": {in_key: 11, out_key: 5, **cache},
    }
    usage = reports[usage_case]
    if api_format == "cli":
        body = (
            json.loads(content)
            if usage_case == "absent"
            else {
                "type": "result",
                "result": content,
                "usage": usage,
            }
        )
        proc = SimpleNamespace(returncode=0, pid=0, communicate=lambda **kwargs: (json.dumps(body), ""))
        monkeypatch.setattr(invoke.shutil, "which", lambda command: "/controlled/cli")
        monkeypatch.setattr(invoke.subprocess, "Popen", lambda *a, **k: proc)
        return BackendConfig(name="controlled", type="cli", model="", command="controlled")
    if openai:
        body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    else:
        body = {"content": [{"type": "text", "text": content}], "stop_reason": "end_turn"}
    if usage_case != "absent":
        body["usage"] = usage
    monkeypatch.setattr(
        invoke.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(body).encode())
    )
    monkeypatch.setenv("FORGE_TEST_USAGE_KEY", "fixture")
    if api_format == "vertex":
        modules = {
            name: ModuleType(name)
            for name in (
                "google",
                "google.auth",
                "google.auth.transport",
                "google.auth.transport.requests",
                "google.auth.exceptions",
                "google.oauth2",
                "google.oauth2.service_account",
            )
        }
        for name, module in modules.items():
            module.__path__ = []
            monkeypatch.setitem(sys.modules, name, module)
            if "." in name:
                parent, attribute = name.rsplit(".", 1)
                setattr(modules[parent], attribute, module)
        modules["google.auth"].default = lambda **kwargs: (
            SimpleNamespace(token="fixture", refresh=lambda request: None),
            "fixture",
        )
        modules["google.auth.transport.requests"].Request = lambda: None
        modules["google.auth.exceptions"].DefaultCredentialsError = RuntimeError
        modules["google.auth.exceptions"].RefreshError = RuntimeError
    return BackendConfig(
        name="controlled",
        type="api",
        model="fixture",
        format=api_format,
        base_url="https://fixture.invalid",
        api_key_env="FORGE_TEST_USAGE_KEY",
        project_id="fixture" if api_format == "vertex" else None,
    )


@pytest.mark.parametrize("api_format", ["cli", "openai", "anthropic", "vertex"])
@pytest.mark.parametrize(
    "usage_case", ["absent", "null", "partial", "output-only", "primary", "malformed", "zero", "full"]
)
def test_factory_native_parser_usage(monkeypatch, tmp_path, api_format, usage_case):
    backend = native_transport(monkeypatch, api_format, usage_case)
    machine = make_machine(
        tmp_path, build_l1_provider("real", resolved(), backend=backend, max_attempts=1)
    )
    machine._execute_round(0)
    expected_input = (
        0
        if usage_case == "zero"
        else 11
        if usage_case
        in {
            "partial",
            "primary",
            "malformed",
            "full",
        }
        else None
    )
    expected_output = (
        0
        if usage_case == "zero"
        else 5
        if usage_case
        in {
            "output-only",
            "primary",
            "full",
        }
        else None
    )
    expected_cached = 0 if usage_case in {"malformed", "zero", "full"} else None
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert len(cost["per_pass"]) == 3
    for row in cost["per_pass"]:
        assert (row["input"], row["output"], row["cached"]) == (
            expected_input,
            expected_output,
            expected_cached,
        )
        assert row["available"] is (usage_case in {"zero", "full"})
    assert cost["total_input_tokens"] == 3 * (expected_input or 0)
    assert cost["total_output_tokens"] == 3 * (expected_output or 0)
    assert cost["total_cached_tokens"] == 0


@pytest.mark.parametrize("api_format", ["cli", "openai", "anthropic", "vertex"])
@pytest.mark.parametrize("usage_case", ["absent", "null", "invalid"])
@pytest.mark.parametrize("measured_zero", [False, True])
@pytest.mark.parametrize("grouped", [False, True])
def test_factory_native_duration_without_tokens(
    monkeypatch,
    tmp_path,
    api_format,
    usage_case,
    measured_zero,
    grouped,
):
    from code_forge import llm_invoke as invoke
    import time

    backend = native_transport(monkeypatch, api_format, usage_case)
    if measured_zero:
        monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 1234.0))
    else:
        transport_owner = invoke.subprocess if api_format == "cli" else invoke.urllib.request
        transport_name = "Popen" if api_format == "cli" else "urlopen"
        original_transport = getattr(transport_owner, transport_name)

        def delayed_transport(*args, **kwargs):
            time.sleep(0.001)
            return original_transport(*args, **kwargs)

        monkeypatch.setattr(transport_owner, transport_name, delayed_transport)

    real_invoke = invoke.llm_invoke
    measurements = {role: [] for role in ROLES}
    lock = threading.Lock()

    def observe_result(prompt, **kwargs):
        result = real_invoke(prompt, **kwargs)
        assert result.usage.known_fields == frozenset()
        assert result.duration_s == 0.0 if measured_zero else result.duration_s > 0.0
        index = next(
            i
            for i, role in enumerate(
                (
                    "structural code reviewer",
                    "senior engineer",
                    "adversarial QE",
                )
            )
            if role in prompt
        )
        with lock:
            measurements[ROLES[index]].append(result.duration_s)
        return result

    monkeypatch.setattr(invoke, "llm_invoke", observe_result)
    if grouped:
        provider = build_grouped_l1_provider(
            "real",
            [{"name": name, "resolved": resolved()} for name in ("first", "second")],
            backend=backend,
            max_attempts=1,
        )
    else:
        provider = build_l1_provider("real", resolved(), backend=backend, max_attempts=1)
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    path = tmp_path / ".code-forge/state.json"
    cost = json.loads(path.read_text())["cost"]
    expected_count = 6 if grouped else 3
    assert len(cost["per_pass"]) == expected_count
    assert all(len(values) == (2 if grouped else 1) for values in measurements.values())
    for index, row in enumerate(cost["per_pass"]):
        role = ROLES[index % 3]
        expected_duration = measurements[role][index // 3]
        assert row["pass_name"] == role
        assert row["duration_s"] == expected_duration
        assert row["input"] is None and row["output"] is None and row["cached"] is None
        assert row["available"] is False
        if grouped:
            assert row["group"] == ("first", "second")[index // 3]
    assert load_state(path).cost_per_pass == cost["per_pass"]
    assert (cost["total_input_tokens"], cost["total_output_tokens"], cost["total_cached_tokens"]) == (
        0,
        0,
        0,
    )
    if api_format == "cli":
        assert cost["total_duration_s"] == sum(sum(values) for values in measurements.values())
    else:
        assert cost["total_duration_s"] > 0.0


@pytest.mark.parametrize("api_format", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("usage_case", ["partial", "output-only", "primary", "zero", "full"])
def test_factory_native_error_preserves_known_siblings(monkeypatch, tmp_path, api_format, usage_case):
    from code_forge.llm_invoke import _response_error_usage, llm_invoke

    backend = native_transport(monkeypatch, api_format, usage_case, rejected=True)
    with pytest.raises(LLMInvokeError) as caught:
        llm_invoke("fixture", backend=backend, max_attempts=1)
    error = caught.value
    if usage_case in {"partial", "output-only"}:
        assert error.usage is None
    else:
        assert error.usage is not None
    assert _response_error_usage({"prompt_tokens": 11}, "openai") is None
    machine = make_machine(
        tmp_path, build_l1_provider("real", resolved(), backend=backend, max_attempts=1)
    )
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    expected_input = 0 if usage_case == "zero" else 11 if usage_case != "output-only" else None
    expected_output = 0 if usage_case == "zero" else 5 if usage_case != "partial" else None
    expected_cached = 0 if usage_case in {"zero", "full"} else None
    for row in cost["per_pass"]:
        assert (row["input"], row["output"], row["cached"]) == (
            expected_input,
            expected_output,
            expected_cached,
        )
        assert row["available"] is (usage_case in {"zero", "full"})
    assert cost["total_input_tokens"] == 3 * (expected_input or 0)
    assert cost["total_output_tokens"] == 3 * (expected_output or 0)


@pytest.mark.parametrize("api_format", ["openai", "anthropic", "vertex"])
def test_factory_recovery_producers_preserve_unknown_usage(monkeypatch, api_format):
    from code_forge.llm_invoke import _continue_truncated, _retry_with_more_headroom, _TruncatedResponse

    # A length cut is always continued, so the continuation sends the rest.
    reply = response(MEASUREMENTS[0]).content
    backend = native_transport(monkeypatch, api_format, "partial", content=reply[12:])
    truncated = _TruncatedResponse("fixture", content=reply[:12], usage_data={}, resolved_cap=10)
    parsed, usage = _continue_truncated("fixture", backend, "fixture", 5, truncated, None)
    assert parsed["findings"] == []
    assert usage == Usage(11, 0, 0)
    assert usage.known_fields == frozenset()
    backend = native_transport(monkeypatch, api_format, "partial")
    backend = replace(backend, output_token_limit=32768)
    parsed, usage = _retry_with_more_headroom("fixture", backend, "fixture", 5, None)
    assert parsed["findings"] == []
    assert usage.input_tokens == 11 and usage.output_tokens == 0 and usage.cached_input_tokens == 0
    assert usage.known_fields == frozenset({"input_tokens"})


def test_factory_continuation_combines_provenance(monkeypatch):
    from code_forge.llm_invoke import _continue_truncated, _TruncatedResponse
    from code_forge.backend import BackendConfig

    tail = '[], "code_excerpts": []}'
    body = {
        "choices": [{"message": {"content": tail}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 5},
    }
    from code_forge import llm_invoke as invoke

    monkeypatch.setattr(
        invoke.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(body).encode())
    )
    backend = BackendConfig(
        name="controlled",
        type="api",
        model="fixture",
        format="openai",
        base_url="https://fixture.invalid",
    )
    truncated = _TruncatedResponse(
        "fixture", content='{"findings": ', usage_data={"prompt_tokens": 11}, resolved_cap=10
    )
    _, usage = _continue_truncated("fixture", backend, "fixture", 5, truncated, None)
    assert usage == Usage(11, 5, 0)
    assert usage.known_fields == frozenset()


def test_factory_excerpt_repair_combines_provenance(monkeypatch):
    from code_forge import llm_invoke as invoke

    backend = native_transport(monkeypatch, "cli", "partial")
    messages = iter(
        [
            json.dumps(
                {
                    "type": "result",
                    "result": '{"findings": []}',
                    "usage": {"input_tokens": 11, "output_tokens": 5, "cache_read_input_tokens": 0},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "result": response(MEASUREMENTS[0]).content,
                    "usage": {"input_tokens": 13},
                }
            ),
        ]
    )
    monkeypatch.setattr(
        invoke.subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            pid=0,
            communicate=lambda **kwargs: (next(messages), ""),
        ),
    )
    result = invoke.llm_invoke("fixture", backend=backend, l1_evidence_required=True)
    assert result.usage == Usage(24, 5, 0)
    assert result.usage.known_fields == frozenset({"input_tokens"})


def test_factory_syntax_correction_combines_provenance(monkeypatch, tmp_path):
    from code_forge import llm_invoke as invoke
    from code_forge.backend import BackendConfig

    counts = {role: 0 for role in ROLES}
    lock = threading.Lock()

    def transport(request, **kwargs):
        prompt = json.loads(request.data)["messages"][0]["content"]
        index = next(
            i
            for i, role in enumerate(
                (
                    "structural code reviewer",
                    "senior engineer",
                    "adversarial QE",
                )
            )
            if role in prompt
        )
        with lock:
            counts[ROLES[index]] += 1
            first = counts[ROLES[index]] == 1
        text = '{"findings":[true,,false]}' if first else response(MEASUREMENTS[0]).content
        usage = (
            {"prompt_tokens": 11}
            if first
            else {
                "prompt_tokens": 13,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        )
        body = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": usage}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(invoke.urllib.request, "urlopen", transport)
    monkeypatch.setenv("FORGE_TEST_USAGE_KEY", "fixture")
    backend = BackendConfig(
        name="controlled",
        type="api",
        model="fixture",
        format="openai",
        base_url="https://fixture.invalid",
        api_key_env="FORGE_TEST_USAGE_KEY",
    )
    machine = make_machine(
        tmp_path, build_l1_provider("real", resolved(), backend=backend, max_attempts=2)
    )
    machine._execute_round(0)
    assert counts == dict.fromkeys(ROLES, 2)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert cost["total_input_tokens"] == 72
    assert cost["total_output_tokens"] == 15
    for row in cost["per_pass"]:
        assert row["input"] == 24 and row["output"] is None and row["cached"] is None
        assert row["available"] is False


@pytest.mark.parametrize("content", ["", "{invalid json}"])
@pytest.mark.parametrize(
    "nested,flat,expected",
    [
        (-1, 7, 7),
        ("invalid", 7, 7),
        (True, 7, 7),
        (None, 7, 7),
        (0, 7, 7),
        (5, 7, 5),
        (0, 0, 0),
        (0, None, 0),
        (None, 0, 0),
        (-1, -1, None),
        (True, False, None),
        ("invalid", None, None),
    ],
)
def test_factory_cache_candidate_error_fallback(monkeypatch, tmp_path, content, nested, flat, expected):
    from code_forge import llm_invoke as invoke

    backend = native_transport(monkeypatch, "openai", "full")
    usage = {
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": nested},
        "prompt_cache_hit_tokens": flat,
    }
    body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": usage}
    monkeypatch.setattr(
        invoke.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(body).encode())
    )
    with pytest.raises(LLMInvokeError) as caught:
        invoke.llm_invoke("fixture", backend=backend, max_attempts=1)
    assert caught.value.usage.cached_input_tokens == (expected or 0)
    assert ("cached_input_tokens" in caught.value.observed_usage.known_fields) is (expected is not None)
    provider = build_l1_provider("real", resolved(), backend=backend, max_attempts=1)
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert cost["total_input_tokens"] == 33 and cost["total_output_tokens"] == 15
    assert cost["total_cached_tokens"] == 3 * (expected or 0)
    for row in cost["per_pass"]:
        assert row["input"] == 11 and row["output"] == 5 and row["cached"] == expected
        assert row["available"] is (expected is not None)


@pytest.mark.parametrize("details", [7, "invalid", [1]])
def test_factory_cache_malformed_details_preserve_flat(monkeypatch, tmp_path, details):
    from code_forge import llm_invoke as invoke

    backend = native_transport(monkeypatch, "openai", "full")
    usage = {"prompt_tokens": 11, "prompt_tokens_details": details, "prompt_cache_hit_tokens": 7}
    body = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": usage}
    monkeypatch.setattr(
        invoke.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(body).encode())
    )
    provider = build_l1_provider("real", resolved(), backend=backend, max_attempts=1)
    machine = make_machine(tmp_path, provider)
    machine._execute_round(0)
    cost = json.loads((tmp_path / ".code-forge/state.json").read_text())["cost"]
    assert cost["total_cached_tokens"] == 21
    for row in cost["per_pass"]:
        assert row["input"] == 11 and row["output"] is None and row["cached"] == 7
        assert row["available"] is False


@pytest.mark.parametrize("chunked", [False, True])
def test_factory_outlet_c_wrapper_keeps_unreported_slots(monkeypatch, tmp_path, chunked):
    from code_forge import outlet_c

    calls = []
    (tmp_path / "sample.py").write_text("VALUE = 2\n")
    monkeypatch.setattr(outlet_c, "_read_chunk_threshold_kb", lambda: 0 if chunked else 1024)

    def spawn(role, diff):
        calls.append(role)
        return response(MEASUREMENTS[0]).content

    outlet_c.run_outlet_c(
        resolved(),
        "usage-source",
        tmp_path,
        spawn,
        falsifier=StubFalsifier(),
        max_total_rounds=1,
        clean_round_threshold=1,
        registry={},
        engine="stub",
    )
    assert calls == list(ROLES)
    state = load_state(tmp_path / ".code-forge/state.json")
    assert state.cost_passes == 3 and len(state.cost_per_pass) == 3
    assert all(
        row["available"] is False
        and row["input"] is None
        and row["output"] is None
        and row["cached"] is None
        for row in state.cost_per_pass[-3:]
    )


def test_factory_shared_wrapper_resets_to_unreported(monkeypatch, tmp_path):
    from code_forge.factories import _L1Call

    provider = _L1Call(lambda call: ([], [], Usage(11, 5, 7), 1.0))
    assert provider.pass_usage is None
    provider.pass_usage = expected_rows()
    machine = make_machine(tmp_path, provider)
    machine._state.source_hash = machine.source_hash
    machine._execute_round(0)
    assert provider.pass_usage is None
    assert machine._state.cost_passes == 3
    assert all(
        row["input"] is None and row["available"] is False for row in machine._state.cost_per_pass
    )
    assert machine._state.cost_total_input == 11
    resumed = make_machine(tmp_path, _L1Call(lambda call: ([], [], Usage(11, 5, 7), 1.0)))
    resumed._maybe_load_prior_state()
    resumed._execute_round(1)
    assert resumed._state.cost_passes == 6 and len(resumed._state.cost_per_pass) == 6
    assert resumed._state.cost_total_input == 22
    assert all(
        row["available"] is False and row["input"] is None for row in resumed._state.cost_per_pass
    )

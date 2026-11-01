# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Wave 2b additive host-row contracts, independent tamper and legacy controls."""

from copy import deepcopy

import pytest

from code_forge.errors import CorruptedStateError
from code_forge.machine import _FixpointResult
from code_forge.state import load_state, save_state, validate_round_history
from tests.test_phase6_schema_coverage_credit import (
    _C3_FP,
    _c3_machine,
    _finish,
    _host_rows,
    _new_product,
)


def _preserved(tmp_path, monkeypatch, *, l2=False):
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    _host_rows(sm)
    if l2:
        sm._l2_infra_error = "test infrastructure failure"
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET)
    row = _finish(sm, "preserved")
    return sm, row


@pytest.mark.parametrize("l2", [False, True])
def test_writer_basis_roundtrip(tmp_path, monkeypatch, l2):
    sm, row = _preserved(tmp_path, monkeypatch, l2=l2)
    assert row["preserve_basis"] == {
        "version": 1,
        "causes": ["schema-fail-adversarial"],
        "companions": [_C3_FP],
        "counterfactual": "INCOMPLETE" if l2 else "CLEAN",
        "recurring": {},
        "density_passed": True,
    }
    assert row["untrusted_fingerprints"] == [_C3_FP]
    save_state(sm._state, tmp_path / "state.json")
    assert load_state(tmp_path / "state.json").round_history == sm._state.round_history


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", True),
        ("version", 2),
        ("causes", []),
        ("causes", ["schema-fail-adversarial", "schema-fail-adversarial"]),
        ("companions", []),
        ("companions", ["unobserved"]),
        ("counterfactual", "RESET"),
        ("counterfactual", "INCOMPLETE"),
        ("recurring", {"new": "P3"}),
        ("density_passed", False),
        ("density_passed", 1),
    ],
)
def test_loader_refuses_inconsistent_basis(tmp_path, monkeypatch, key, value):
    sm, row = _preserved(tmp_path, monkeypatch)
    row["preserve_basis"][key] = value
    with pytest.raises(CorruptedStateError):
        validate_round_history(sm._state.round_history)


@pytest.mark.parametrize("value", [None, [], {}, {"version": 1}])
def test_present_basis_never_falls_back_to_legacy(tmp_path, monkeypatch, value):
    sm, row = _preserved(tmp_path, monkeypatch)
    row["preserve_basis"] = value
    with pytest.raises(CorruptedStateError):
        validate_round_history(sm._state.round_history)


def test_legacy_preserve_warns_and_keeps_same_history(tmp_path, monkeypatch):
    sm, row = _preserved(tmp_path, monkeypatch)
    del row["preserve_basis"]
    del row["untrusted_fingerprints"]
    save_state(sm._state, tmp_path / "state.json")
    with pytest.warns(UserWarning, match="legacy preserved rounds lack preserve_basis") as warnings:
        loaded = load_state(tmp_path / "state.json")
    assert len(warnings) == 1
    assert loaded.round_history == sm._state.round_history
    assert loaded.consecutive_clean_rounds == sm._state.consecutive_clean_rounds


@pytest.mark.parametrize("value", [None, "fp", [None], [_C3_FP, _C3_FP], ["missing"]])
def test_loader_refuses_bad_untrusted_snapshot(tmp_path, monkeypatch, value):
    sm, row = _preserved(tmp_path, monkeypatch)
    row["untrusted_fingerprints"] = value
    with pytest.raises(CorruptedStateError):
        validate_round_history(sm._state.round_history)


def test_demoted_preserve_drops_basis_not_untrusted_origin(tmp_path, monkeypatch):
    sm, row = _preserved(tmp_path, monkeypatch)
    sm._state.earned_clean_window = {"reviewed_repositories": None}
    sm._demote_settled_preserve(row)
    assert "preserve_basis" not in row
    assert row["untrusted_fingerprints"] == [_C3_FP]
    assert row["dispositions"][_C3_FP] == "UNCERTAIN"
    validate_round_history(sm._state.round_history)


def test_trusted_collision_is_not_marked_untrusted(tmp_path, monkeypatch):
    sm, schema, companions = _c3_machine(tmp_path, monkeypatch)
    trusted = _new_product(_C3_FP, "P3: trusted nit")
    sm._state.findings = [schema, *companions, trusted]
    sm._phase_findings["l1"] = list(sm._state.findings)
    _host_rows(sm, {_C3_FP: "CONFIRMED"})
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET)
    row = _finish(sm, "preserved")
    assert row["untrusted_fingerprints"] == []
    assert row["preserve_basis"]["recurring"] == {_C3_FP: "P3"}
    validate_round_history(sm._state.round_history)
    for tier in ("P0", "P1", "P2", None):
        altered = deepcopy(sm._state.round_history)
        altered[-1]["preserve_basis"]["recurring"][_C3_FP] = tier
        with pytest.raises(CorruptedStateError):
            validate_round_history(altered)


@pytest.mark.parametrize(
    "l1", [None, ["schema-fail-adversarial", _C3_FP, []], ["schema-fail-adversarial", _C3_FP, {}]]
)
def test_malformed_l1_basis_observation_is_a_corruption_not_type_error(tmp_path, monkeypatch, l1):
    sm, row = _preserved(tmp_path, monkeypatch)
    row["l1_fingerprints"] = l1
    with pytest.raises(CorruptedStateError):
        validate_round_history(sm._state.round_history)

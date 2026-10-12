# SPDX-License-Identifier: Apache-2.0
"""Exact latest ledger identity is necessary for a new mutation dismissal."""
from dataclasses import asdict, replace
import json

import pytest

from code_forge.disposition import Disposition
from code_forge.ledger import (
    LedgerRow, TerminalState, append_row, finding_suppression_key, iter_rows,
    known_terminal_fingerprints, resolve_ledger_root,
)
from code_forge.mutation_findings import apply_mutation_ledger_dispositions
from code_forge.state import StateFinding

BASE = "a" * 40
HEAD = "b" * 40
DIFF = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-old\n+new\n"


def make_survivor(**changes):
    values = dict(id="mutant-example", fingerprint="actual:example", source="MUTANT",
                  disposition=Disposition.CONFIRMED, file="", line_range=[0, 0],
                  description="actual native claim")
    values.update(changes)
    return StateFinding(**values)


def matching_row(root, finding, state=TerminalState.DISPROVED, **changes):
    key = finding_suppression_key(base_sha=BASE, head_sha=HEAD, git_diff=DIFF,
                                  file=finding.file, line=finding.line_range[0] if finding.line_range else 0,
                                  source=finding.source, description=finding.description)
    values = dict(fingerprint=finding.fingerprint, repo_root=str(resolve_ledger_root(root).resolve()),
                  base_sha=BASE, head_sha=HEAD, file=finding.file,
                  line=finding.line_range[0] if finding.line_range else 0,
                  axis_claim="mutation", pass_provenance="adjudicated", terminal_state=state,
                  evidence_class="exact ruling", ts="2026-10-11T00:00:00Z", suppression_key=key)
    values.update(changes)
    return LedgerRow(**values)


def apply(root, findings, **changes):
    values = dict(source_root=root, base_sha=BASE, head_sha=HEAD, git_diff=DIFF,
                  eligible_measurement=True)
    values.update(changes)
    return apply_mutation_ledger_dispositions(findings, **values)


@pytest.mark.parametrize("state", [TerminalState.FIXED, TerminalState.DISPROVED, TerminalState.DUPLICATE])
def test_exact_terminal_states_require_every_binding(tmp_path, state):
    finding = make_survivor()
    append_row(tmp_path, matching_row(tmp_path, finding, state))
    notes = apply(tmp_path, [finding])
    assert finding.disposition is Disposition.DISMISSED
    assert notes and finding.fingerprint in notes[0]
    assert finding.id == "mutant-example" and finding.description == "actual native claim"


@pytest.mark.parametrize("field,value", [
    ("fingerprint", "derived-from-id"), ("base_sha", "c"*40), ("head_sha", "c"*40),
    ("repo_root", "/wrong-source"), ("file", "calc.py"), ("line", 1),
    ("suppression_key", ""), ("suppression_key", "c"*64),
])
def test_each_row_binding_is_required(tmp_path, field, value):
    finding = make_survivor()
    append_row(tmp_path, matching_row(tmp_path, finding, **{field: value}))
    assert not apply(tmp_path, [finding])
    assert finding.disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("changes", [
    {"git_diff": DIFF + " "}, {"base_sha": None}, {"head_sha": None}, {"git_diff": None},
    {"eligible_measurement": False}, {"eligible_measurement": 1},
])
def test_unbound_or_unmeasured_claim_stays_active(tmp_path, changes):
    finding = make_survivor()
    append_row(tmp_path, matching_row(tmp_path, finding))
    assert not apply(tmp_path, [finding], **changes)
    assert finding.disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("changes", [
    {"suppression_key": "changed"}, {"suppression_key": ""}, {"repo_root": "/other"},
    {"terminal_state": TerminalState.ESCAPED}, {"terminal_state": TerminalState.UNADJUDICATED},
])
def test_latest_fingerprint_row_revokes_old_key(tmp_path, changes):
    finding = make_survivor()
    original = matching_row(tmp_path, finding, ts="2099-01-01T00:00:00Z")
    append_row(tmp_path, original)
    append_row(tmp_path, replace(original, ts="2000-01-01T00:00:00Z", **changes))
    assert not apply(tmp_path, [finding])
    assert finding.disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("changes", [
    {"id": "MUTATION_SKIPPED"}, {"source": "L0"}, {"fingerprint": ""},
    {"description": ""}, {"line_range": [False, 0]}, {"line_range": [0.0, 0]},
    {"line_range": [float("nan"), 0]},
    {"line_range": [-1, 0]}, {"line_range": [2, 1]}, {"line_range": None},
    {"is_timeout": True},
])
def test_non_native_or_invalid_claim_cannot_dismiss(tmp_path, changes):
    valid = make_survivor()
    append_row(tmp_path, matching_row(tmp_path, valid))
    finding = make_survivor(**changes)
    assert not apply(tmp_path, [finding])
    assert finding.disposition is Disposition.CONFIRMED


def test_duplicate_id_or_actual_fingerprint_blocks_both(tmp_path):
    finding = make_survivor()
    append_row(tmp_path, matching_row(tmp_path, finding))
    for other in [make_survivor(id="mutant-other"), make_survivor(fingerprint="other")]:
        assert not apply(tmp_path, [finding, other])
        assert finding.disposition is other.disposition is Disposition.CONFIRMED


def test_mixed_unknown_preserves_unknown_and_legacy_lookup(tmp_path):
    known, unknown = make_survivor(), make_survivor(id="mutant-unknown", fingerprint="actual:unknown")
    append_row(tmp_path, matching_row(tmp_path, known, TerminalState.FIXED))
    assert known.fingerprint in known_terminal_fingerprints(tmp_path)
    assert apply(tmp_path, [known, unknown])
    assert known.disposition is Disposition.DISMISSED
    assert unknown.disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("tail", ["{", "[]", " ", '{"fingerprint":"a","fingerprint":"b"}'])
def test_strict_tail_revokes_all_suppression(tmp_path, tail):
    finding = make_survivor()
    append_row(tmp_path, matching_row(tmp_path, finding))
    path = tmp_path/".code-forge/ledger.jsonl"
    with path.open("a") as stream:
        stream.write(tail + "\n")
    notes = apply(tmp_path, [finding])
    assert notes and "unavailable" in notes[0]
    assert finding.disposition is Disposition.CONFIRMED
    with pytest.raises(ValueError):
        list(iter_rows(tmp_path, strict=True))


@pytest.mark.parametrize("changes", [
    {"unknown": True}, {"line": True}, {"line": "0"}, {"line": 0.5},
    {"line": float("nan")},
    {"terminal_state": "ALIEN"}, {"backend": None}, {"ctx_canary": 1}, {"suppression_key": False},
])
def test_strict_unknown_schema_tail_is_not_skipped(tmp_path, changes):
    finding = make_survivor()
    row = matching_row(tmp_path, finding)
    append_row(tmp_path, row)
    malformed = asdict(row) | changes
    with (tmp_path/".code-forge/ledger.jsonl").open("a") as stream:
        stream.write(json.dumps(malformed) + "\n")
    assert apply(tmp_path, [finding]) and finding.disposition is Disposition.CONFIRMED


def test_tolerant_reader_and_global_policy_unchanged(tmp_path, capsys):
    row = matching_row(tmp_path, make_survivor(), TerminalState.FIXED)
    append_row(tmp_path, row)
    path = tmp_path/".code-forge/ledger.jsonl"
    with path.open("a") as stream:
        stream.write("broken\n")
    assert list(iter_rows(tmp_path)) == [row]
    assert known_terminal_fingerprints(tmp_path) == {row.fingerprint}
    assert "skipping malformed" in capsys.readouterr().err
    data = asdict(row)
    data.pop("suppression_key")
    path.write_text(json.dumps(data) + "\n")
    assert list(iter_rows(tmp_path, strict=True))[0].suppression_key == ""
    assert not apply(tmp_path, [make_survivor()])


def test_missing_and_unreadable_ledgers_do_not_suppress(tmp_path):
    finding = make_survivor()
    assert not apply(tmp_path, [finding])
    path = tmp_path/".code-forge/ledger.jsonl"
    path.parent.mkdir()
    path.mkdir()
    assert apply(tmp_path, [finding])
    assert finding.disposition is Disposition.CONFIRMED


def test_key_binds_verbatim_diff_claim_and_source():
    values = dict(base_sha=BASE, head_sha=HEAD, git_diff=DIFF, file="", line=0,
                  source="MUTANT", description="claim")
    key = finding_suppression_key(**values)
    for field, value in [("git_diff", DIFF + " "), ("description", "other claim"),
                         ("source", "L1"), ("line", 1), ("file", "calc.py")]:
        assert finding_suppression_key(**(values | {field: value})) != key
    assert finding_suppression_key(**(values | {"git_diff": None})) == ""

"""Offline C1/C2 tests; the decision table is not replayed by a projector yet."""

import hashlib
import json

import pytest

from code_forge.eval.review_followup.fixtures import InputError
from code_forge.eval.review_followup.revision_codec import decode_record
from code_forge.eval.review_followup.revision_import import import_records

GOLDEN = (
    rb'{"body":{"claim":"r\u00e9sum\u00e9\n\ud83d\udc09","constraint_ref":{"id":"size","version":1},'
    rb'"evidence_refs":[],"suggested_status":"still_present","supersedes_proposal_id":null},'
    rb'"id":"p","item":{"candidate_sha":"synthetic:candidate","finding_id":"f"},'
    rb'"kind":"proposal","schema":1}'
)


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


def test_golden_bytes_and_digest_from_different_member_order_and_utf8():
    value = dict(reversed(list(json.loads(GOLDEN).items())))
    raw = encode(value)
    assert raw != GOLDEN
    record = decode_record(raw)
    assert record.canonical == GOLDEN
    assert record.sha256 == "947558a3be4bb0215e1d820e12a5b38ab0fd00c8d4148b7d719f566ed71b5c8a"


def test_identical_reimports_and_reverse_order():
    q = encode(dict(json.loads(GOLDEN), id="q"))
    inputs = [GOLDEN, q, encode(json.loads(GOLDEN))]
    stable = import_records(inputs)
    assert stable == import_records(reversed(inputs))
    assert [r.record_id for r in stable] == ["p", "q"]


def test_reusing_id_cannot_replace_content_even_with_the_same_candidate():
    changed = json.loads(GOLDEN)
    changed["body"]["claim"] = "same ID, different claim"
    for inputs in ([GOLDEN, encode(changed)], [encode(changed), GOLDEN]):
        with pytest.raises(InputError, match=r"^record-id-conflict: proposal:p$"):
            import_records(inputs)
    assert decode_record(GOLDEN).sha256 != decode_record(encode(changed)).sha256


def test_kinds_are_separate_and_digests_bind_the_kind():
    validation = dict(
        json.loads(GOLDEN),
        kind="host_validation",
        body={
            "proposal_id": "p",
            "proposal_digest": hashlib.sha256(GOLDEN).hexdigest(),
            "contract_version": 1,
            "result": "passed",
            "reason": None,
            "reference_checks": [],
        },
    )
    pair = import_records([GOLDEN, encode(validation)])
    assert [r.kind for r in pair] == ["host_validation", "proposal"]
    assert len({r.sha256 for r in pair}) == 2


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        (b'"schema":1', b'"schema":true', "invalid-schema"),
        (b'"schema":1', b'"schema":1,"schema":1', "duplicate-key"),
        (b'"version":1', b'"version":1.0', "invalid-schema"),
        (b'"version":1', b'"version":9007199254740992', "invalid-schema"),
        (b'"version":1', rb'"version":"\ud800"', "invalid-unicode-scalar"),
        (b'"version":1', b'"version":"\xff"', "invalid-json"),
        (b'"version":1', b'"version":' + b"[" * 40 + b"0" + b"]" * 40, "record-too-deep"),
        (b'"version":1', b'"version":1,"version":2', "duplicate-key"),
        (b'"schema":1', b'"schema":1,"sha256":"self-report"', "invalid-schema"),
    ],
)
def test_reject_before_assigning_identity(old, new, error):
    with pytest.raises(InputError, match=rf"^{error}:"):
        decode_record(GOLDEN.replace(old, new))


def test_identical_repeated_records_still_consume_byte_budget(monkeypatch):
    from code_forge.eval.review_followup import revision_import

    monkeypatch.setattr(revision_import, "MAX_BYTES", len(GOLDEN))
    assert len(import_records([GOLDEN] * 32)) == 1
    with pytest.raises(InputError, match=r"^batch-too-large: revision_records$"):
        import_records([GOLDEN] * 33)


def test_decision_vectors_supply_original_and_active_bytes_without_using_projection():
    from pathlib import Path

    vectors = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "tests/fixtures/review_followup/revision/decisions.json"
        ).read_bytes()
    )
    cases = {case["id"]: case for case in vectors["cases"]}
    assert len(cases) == len(vectors["cases"])
    for case in cases.values():
        active = vectors["snapshots"][case["snapshot"]]
        assert active.keys() == vectors["artifacts"].keys()
        assert set(active.values()) <= vectors["observations"].keys()
        assert set(case["rulings"]) <= vectors["rulings"].keys()
        assert set(case["fresh_validation_for"]) <= set(case["rulings"])
    original = vectors["snapshots"]["original"]
    changed = vectors["snapshots"][cases["same-path-replaced-evidence"]["snapshot"]]
    assert [key for key in original if original[key] != changed[key]] == ["X"]
    observation = vectors["observations"]
    assert (
        hashlib.sha256(observation[changed["X"]].encode()).digest()
        != hashlib.sha256(observation[original["X"]].encode()).digest()
    )
    assert cases["replacement-fresh-validation-no-ruling"]["fresh_validation_for"] == ["A"]


def test_canonical_unicode_expansion_must_fit_single_record():
    from code_forge.eval.review_followup.fixtures import MAX_BYTES

    value = json.loads(GOLDEN)
    value["body"]["claim"] = ""
    # An ASCII escape for U+6D4B costs six bytes; UTF-8 costs three.
    overhead = len(GOLDEN) - len(rb"r\u00e9sum\u00e9\n\ud83d\udc09")
    repeats, padding = divmod(MAX_BYTES - overhead, 6)
    value["body"]["claim"] = "测" * repeats + "a" * padding
    raw = encode(value)
    assert len(raw) < MAX_BYTES
    record = decode_record(raw)
    assert len(record.canonical) == MAX_BYTES
    assert decode_record(record.canonical) == record

    value["body"]["claim"] += "a"
    oversized = encode(value)
    assert len(oversized) < MAX_BYTES
    with pytest.raises(InputError, match=r"^canonical-record-too-large: revision_record$"):
        decode_record(oversized)


def test_canonical_distinct_records_must_fit_batch_budget(monkeypatch):
    from code_forge.eval.review_followup import revision_import

    monkeypatch.setattr(revision_import, "MAX_BYTES", 512)
    value = json.loads(GOLDEN)
    value["body"]["claim"] = "测" * 40
    raw = [encode(dict(value, id=f"record-{i:02d}")) for i in range(40)]
    canonical = [decode_record(blob).canonical for blob in raw]
    budget = 32 * 512
    assert sum(map(len, raw)) < budget < sum(map(len, canonical))
    with pytest.raises(InputError, match=r"^canonical-batch-too-large: revision_records$"):
        import_records(raw)

    batch = import_records(raw[:30])
    assert import_records(record.canonical for record in batch) == batch

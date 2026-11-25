"""Independent controls for the offline context preview contract."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from code_forge.eval.review_followup import context_plan
from code_forge.eval.review_followup.fixtures import InputError
from code_forge.eval.review_followup.replay import validate

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/review_followup/context"
CASE = Path("inputs/example")


def _wire(group, arm):
    return json.dumps(
        {"required": group["required"], "optional": group[arm]["optional"]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


@pytest.fixture
def request_data():
    value = json.loads((FIXTURE / CASE / "context.json").read_bytes())
    return validate(FIXTURE)[0], value


def test_preview_and_accounting(request_data):
    review, value = request_data
    plan = context_plan.plan_context(review, value)
    storage, tests = plan["groups"]
    assert storage["required"]["members"] == ["store"]
    assert storage["required"]["lenses"] == value["groups"]["storage"]["lenses"]
    assert storage["required"]["diff"] == value["groups"]["storage"]["diff"]
    assert storage["required"]["contract"] == value["groups"]["storage"]["contract"]
    assert storage["baseline"]["bytes"] == storage["limit_bytes"] == 469
    assert storage["targeted"]["bytes"] == 447
    assert tests["targeted"]["bytes"] == tests["limit_bytes"] == 712
    assert tests["targeted"]["optional"][2]["text"] == "cleanup: reset the store\n"
    assert storage["targeted"]["optional"][0]["text"] == "    if value is None:\n        return False\n"
    assert {v["id"]: (v["baseline"], v["targeted"], v["displaced"]) for v in storage["candidates"]} == {
        "overview": ("selected", "over_budget", True),
        "lookup": ("not_in_baseline", "over_budget", False),
        "error-exit": ("not_in_baseline", "selected", False),
        "error-exit-copy": ("not_in_baseline", "duplicate", False),
        "writer-not-captured": ("not_in_baseline", "missing", False),
    }
    for group in plan["groups"]:
        assert group["truncated"] == []
        for arm in ("baseline", "targeted"):
            wire = _wire(group, arm)
            assert group[arm]["bytes"] == len(wire) <= group["limit_bytes"]
            assert group[arm]["payload_sha256"] == hashlib.sha256(wire).hexdigest()


def test_shuffled_catalog_is_byte_identical(request_data):
    review, value = request_data
    first = context_plan.plan_context(review, value)
    value["candidates"].reverse()
    value["groups"] = dict(reversed(list(value["groups"].items())))
    assert json.dumps(context_plan.plan_context(review, value), sort_keys=True) == json.dumps(
        first, sort_keys=True
    )


def test_alias_selection_is_not_displacement(request_data):
    review, value = request_data
    value["groups"]["storage"]["baseline"] = ["error-exit-copy", "error-exit"]
    group = context_plan.plan_context(review, value)["groups"][0]
    rows = {v["id"]: v for v in group["candidates"]}
    assert rows["error-exit-copy"]["baseline"] == "selected"
    assert rows["error-exit"]["baseline"] == "duplicate"
    assert not any(v["displaced"] for v in group["candidates"])
    assert group["baseline"] == group["targeted"]


def test_one_byte_over_skips_whole_span(request_data):
    review, value = request_data
    value["groups"]["tests"]["limit_bytes"] = 711
    group = context_plan.plan_context(review, value)["groups"][1]
    assert [v["targeted"] for v in group["candidates"]] == ["selected", "selected", "over_budget"]
    assert len(group["targeted"]["optional"]) == 2
    assert group["truncated"] == []


def test_counts_utf8_including_escaping(request_data):
    review, value = request_data
    value["groups"]["storage"]["limit_bytes"] = 4000
    group = context_plan.plan_context(review, value)["groups"][0]
    wire = _wire(group, "targeted")
    assert b"caf\xc3\xa9" in wire
    assert b"\\n" in wire
    assert group["targeted"]["bytes"] == len(wire) > len(wire.decode())


def test_required_only_at_limit_then_reject(request_data):
    review, value = request_data
    plan = context_plan.plan_context(review, value)
    group = plan["groups"][0]
    group["targeted"]["optional"] = []
    limit = len(_wire(group, "targeted"))
    value["groups"]["storage"]["limit_bytes"] = limit
    exact = context_plan.plan_context(review, value)["groups"][0]
    assert exact["targeted"]["bytes"] == limit
    assert exact["targeted"]["optional"] == []
    value["groups"]["storage"]["limit_bytes"] -= 1
    with pytest.raises(InputError, match="required-over-budget"):
        context_plan.plan_context(review, value)


@pytest.mark.parametrize(
    ("key", "value", "field"),
    [
        ("group", "unknown", "candidates/0"),
        ("group", "tests", "overview/artifact"),
        ("artifact", "unknown", "overview/artifact"),
        ("revision", "synthetic:context-v0", "candidates/0/revision"),
        ("end", 999, "overview/span"),
        ("start", True, "candidates/0/span"),
        ("id", "lookup", "candidates/1"),
        ("relationship", "answer_key", "candidates/0/relationship"),
    ],
)
def test_refuses_bad_rows(request_data, key, value, field):
    review, doc = request_data
    doc["candidates"][0][key] = value
    if key == "group" and value == "tests":
        doc["groups"]["storage"]["baseline"] = []
    with pytest.raises(InputError, match=field):
        context_plan.plan_context(review, doc)


def test_rejects_span_splitting_multibyte_character(request_data):
    review, value = request_data
    raw = (FIXTURE / CASE / "store.txt").read_bytes()
    row = value["candidates"][1]
    row["end"] = raw.index("é".encode()) + 1
    with pytest.raises(InputError, match="invalid-span-encoding: example/context/lookup/span"):
        context_plan.plan_context(review, value)


def test_refuses_stale_input_binding_and_wrong_baseline_group(request_data):
    review, value = request_data
    value["review_input_sha256"] = "0" * 64
    with pytest.raises(InputError, match="invalid-schema: example/context"):
        context_plan.plan_context(review, value)
    value["review_input_sha256"] = review.sha256
    value["groups"]["storage"]["baseline"] = ["setup"]
    with pytest.raises(InputError, match="storage/baseline"):
        context_plan.plan_context(review, value)


def test_refuses_base_artifact_even_when_present_in_group(request_data):
    review, value = request_data
    store, tests = review.evidence
    key, _, _, checksum, raw = store
    review = replace(review, evidence=((key, "base", review.base, checksum, raw), tests))
    with pytest.raises(InputError, match="example/context/error-exit/revision"):
        context_plan.plan_context(review, value)


def test_baseline_order_and_continuation_after_skips(request_data):
    review, value = request_data
    config = value["groups"]["storage"]
    config["baseline"] = ["overview", "error-exit"]
    config["limit_bytes"] = 447
    group = context_plan.plan_context(review, value)["groups"][0]
    assert group["baseline"] == group["targeted"]
    config["limit_bytes"] = 650
    first = context_plan.plan_context(review, value)["groups"][0]["baseline"]
    config["baseline"].reverse()
    second = context_plan.plan_context(review, value)["groups"][0]["baseline"]
    assert [item["text"].splitlines()[0] for item in first["optional"]] == [
        "Notes: storage fixtures use an in-memory dictionary for the example.",
        "    if value is None:",
    ]
    assert second["optional"] == first["optional"][::-1]


def _cli(root, output, seed, locale, *, case="example"):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "code_forge.eval.review_followup",
            "preview-context",
            str(root),
            "--case",
            case,
            "--output",
            str(output),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": seed, "LC_ALL": locale},
        capture_output=True,
        check=False,
    )


def test_cli_read_only_tree_and_reproducible_bytes(tmp_path):
    root = tmp_path / "fixture"
    shutil.copytree(FIXTURE, root)
    # Poison paths a coupled consumer would try to parse.
    for name in ("scoring.json", "runs.json", "answer_keys", "adjudications"):
        (root / name).write_bytes(b"not JSON")
    a, b = (tmp_path / name for name in ("a.json", "b.json"))
    for index, (output, locale) in enumerate(((a, "C"), (b, "C.UTF-8"))):
        result = _cli(root, output, str(index), locale)
        assert result.returncode == 0, result.stderr
    assert a.read_bytes() == b.read_bytes()
    assert _cli(root, a, "11", "C").returncode == 1
    assert a.read_bytes() == b.read_bytes()
    assert not list(tmp_path.glob(".offline-replay-*"))


def test_cli_rejection_never_publishes(tmp_path):
    root = tmp_path / "fixture"
    shutil.copytree(FIXTURE, root)
    output = tmp_path / "result.json"
    plan = root / CASE / "context.json"
    doc = json.loads(plan.read_bytes())
    doc["groups"]["tests"]["limit_bytes"] = 0
    plan.write_text(json.dumps(doc))
    result = _cli(root, output, "0", "C")
    assert result.returncode == 2
    assert b"required-over-budget" in result.stderr
    assert not output.exists()
    (root / CASE / "store.txt").unlink()
    result = _cli(root, output, "0", "C")
    assert result.returncode == 2 and b"missing-file" in result.stderr
    assert not output.exists()

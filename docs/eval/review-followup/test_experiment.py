"""Controls for the offline factory capture, not provider or scoring tests."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from code_forge import factories, lenses, reviewer_json
from code_forge.diff import annotated_diff_prompt_block
from code_forge.eval.review_followup import experiment, experiment_adapter
from code_forge.eval.review_followup.context_plan import preview
from code_forge.eval.review_followup.experiment_adapter import PASSES, Capture
from code_forge.eval.review_followup.fixtures import InputError
from code_forge.lenses import LENS_BLOCK, selection_line
from code_forge.reviewer_json import L1_JSON_ENCODING_GUIDANCE, REVIEW_JSON_CONTRACT

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/review_followup/context"


def test_actual_factory_inputs_match_independent_reconstruction(monkeypatch):
    def no_io(*args, **kwargs):
        pytest.fail("dry run must not dispatch a process or connect")

    monkeypatch.setattr(socket.socket, "connect", no_io)
    monkeypatch.setattr(subprocess, "Popen", no_io)

    plan = preview(FIXTURE, "example")
    by_group = {g["required"]["group"]: g for g in plan["groups"]}
    report = experiment.dry_run(FIXTURE, "example", 16000)
    expected_order = [
        (path, group, name)
        for path in ("ordinary", "grouped")
        for group in ("storage", "tests")
        for name in ("qodo", "expert", "adversarial")
    ]
    assert [(r["path"], r["group"], r["pass"]) for r in report["rows"]] == expected_order
    assert report["sent_requests"] == 0
    assert report["provider_usage"] is None
    assert report["captured_slots_per_arm_per_path"] == 6
    prefix = (REVIEW_JSON_CONTRACT + LENS_BLOCK + L1_JSON_ENCODING_GUIDANCE).encode()
    for row in report["rows"]:
        group = by_group[row["group"]]
        req = group["required"]
        role = dict(PASSES)[row["pass"]]
        tail = (
            f"\n## Design Intent\n{req['contract']}\n"
            + annotated_diff_prompt_block(req["diff"])
            + f"\nYou are a {role}. Review this diff.\n"
            + selection_line(row["pass"])
        ).encode()
        assert not row["byte_identical_arms"]
        for arm in ("baseline", "targeted"):
            optional = json.dumps(
                group[arm]["optional"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
            block = b"\n## Context Sources\n" + optional + b"\n"
            full = prefix + block + tail
            result = row[arm]
            assert result["context_span"] == [len(prefix), len(prefix) + len(block)]
            assert result["prompt_bytes"] == len(full)
            assert result["prompt_sha256"] == hashlib.sha256(full).hexdigest()


def test_identical_arms_remain_in_report(monkeypatch):
    plan = preview(FIXTURE, "example")
    plan["groups"][0]["targeted"] = plan["groups"][0]["baseline"]
    monkeypatch.setattr(experiment, "preview", lambda *_: plan)
    result = experiment.dry_run(FIXTURE, "example", 16000)
    assert len(result["rows"]) == 12
    assert all(row["byte_identical_arms"] == (row["group"] == "storage") for row in result["rows"])


@pytest.mark.parametrize("mode", ["shared", "dropped", "reversed"])
def test_grouped_topology_drift_refused(monkeypatch, mode):
    real = factories.build_grouped_l1_provider

    def mutated(engine, specs, **kwargs):
        if mode == "shared":
            kwargs["context_sources_text"] = "broadcast"
        elif mode == "dropped":
            specs = specs[:-1]
        else:
            specs = specs[::-1]
        return real(engine, specs, **kwargs)

    monkeypatch.setattr(factories, "build_grouped_l1_provider", mutated)
    with pytest.raises(InputError, match="capture-(topology-drift|incomplete): grouped"):
        experiment.dry_run(FIXTURE, "example", 16000)


@pytest.mark.parametrize("defect", ["suffix", "wrong-group", "duplicate", "moved", "pass"])
def test_exact_context_span_negative_controls(defect):
    group = experiment.Group("sample", ("member",), "diff", "contract")
    header = b"\n## Context Sources\n"
    block = header + b"sample\n"
    tail = b"\n## Design Intent\ncontract\nfixed-tail\n"
    # The same bytes in fixed material must not be stripped or re-located.
    control = Capture("sample", "expert", b"prefix" + block + tail, True)
    correct = replace(control, prompt=b"prefix" + block + block + tail)
    ok = experiment.compare(control, correct, "sample", group, len(correct.prompt), "field")
    start, stop = ok["context_span"]
    assert correct.prompt[start:stop] == block
    invalid = {
        "suffix": replace(correct, prompt=correct.prompt + b"!"),
        "wrong-group": replace(correct, prompt=b"prefix" + block + header + b"other\n" + tail),
        "duplicate": replace(correct, prompt=b"prefix" + block * 3 + tail),
        "moved": replace(correct, prompt=b"prefix" + block + tail + block),
        "pass": replace(correct, pass_name="qodo"),
    }[defect]
    with pytest.raises(InputError, match="non-context-prompt-drift: field"):
        experiment.compare(control, invalid, "sample", group, len(correct.prompt) + 100, "field")


def _cli(output, limit, seed):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "code_forge.eval.review_followup",
            "dry-run-context",
            str(FIXTURE),
            "--case",
            "example",
            "--max-prompt-bytes",
            str(limit),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": str(seed)},
        capture_output=True,
    )


def test_cli_exact_cap_and_cross_process_replay(tmp_path):
    report = experiment.dry_run(FIXTURE, "example", 16000)
    limit = max(row[arm]["prompt_bytes"] for row in report["rows"] for arm in ("baseline", "targeted"))
    first, second, refused = (tmp_path / item for item in ("a.json", "b.json", "refused.json"))
    for seed, path in enumerate((first, second), start=1):
        result = _cli(path, limit, seed)
        assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    assert first.read_bytes() == second.read_bytes()
    result = _cli(refused, limit - 1, 1)
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr.startswith(b"input rejected: prompt-over-budget:")
    assert not refused.exists()


def test_total_capture_cap_refused(monkeypatch):
    monkeypatch.setattr(experiment_adapter, "MAX_CAPTURE_BYTES", 4000)
    with pytest.raises(InputError, match="capture-over-budget: ordinary"):
        experiment.dry_run(FIXTURE, "example", 16000)


def test_report_records_selection_in_the_rendered_order(tmp_path):
    import shutil

    fixture = tmp_path / "context"
    shutil.copytree(FIXTURE, fixture)
    request = fixture / "inputs/example/context.json"
    value = json.loads(request.read_bytes())
    value["groups"]["tests"]["baseline"] = ["cleanup", "skip", "setup"]
    request.write_text(json.dumps(value), encoding="utf-8")
    report = experiment.dry_run(fixture, "example", 16000)
    group = next(item for item in report["planned_context"] if item["group"] == "tests")
    assert group["baseline"]["selected"] == ["cleanup", "skip", "setup"]
    assert group["targeted"]["selected"] == ["setup", "skip", "cleanup"]


@pytest.mark.parametrize("path", ["ordinary", "grouped"])
def test_reply_keys_drift_in_targeted_arm_is_refused(monkeypatch, path):
    normal = experiment.dry_run(FIXTURE, "example", 16000)
    group = preview(FIXTURE, "example")["groups"][0]
    targeted_storage = experiment._encoded(group["targeted"]["optional"]).decode()
    capture = experiment.capture
    keys = reviewer_json.REVIEW_REPLY_KEYS
    changed = replace(keys, items={**keys.items, "findings": keys.items["findings"] - {"excerpt"}})
    mutated = []

    def inject_reply_drift(active_path, groups, texts, limit):
        if active_path == path and texts["storage"] == targeted_storage:
            mutated.append(active_path)
            with monkeypatch.context() as patch:
                patch.setattr(reviewer_json, "REVIEW_REPLY_KEYS", changed)
                return capture(active_path, groups, texts, limit)
        return capture(active_path, groups, texts, limit)

    monkeypatch.setattr(experiment, "capture", inject_reply_drift)
    assert normal["sent_requests"] == 0
    with pytest.raises(InputError, match=f"capture-invocation-drift: {path}/storage/qodo"):
        experiment.dry_run(FIXTURE, "example", 16000)
    assert mutated == [path]


def test_both_arms_using_the_same_wrong_lens_are_refused(monkeypatch):
    normal = lenses.selection_line

    def wrong_lens(pass_name):
        return normal("expert" if pass_name == "qodo" else pass_name)

    # Corrupt factory output in every render, including the no-context
    # control. Relative prompt equality alone cannot catch this.
    monkeypatch.setattr(lenses, "selection_line", wrong_lens)
    with pytest.raises(InputError, match="capture-topology-drift: ordinary"):
        experiment.dry_run(FIXTURE, "example", 16000)

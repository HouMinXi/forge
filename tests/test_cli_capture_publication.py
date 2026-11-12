"""Synthetic publication controls; no capture or product qualification."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "capture_publication", Path(__file__).resolve().parents[1] / "scripts/cli_capture_publication.py"
)
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


def capability():
    return dict(
        schema="forge-cli-capture-capability-v1",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(publication.FACTS, True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
    )


@pytest.fixture
def envelope(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o700)
    source = root / "capability.json"
    source.write_text(json.dumps(capability()) + "\n")
    source.chmod(0o600)
    return root, source, root / "published"


def test_closed_projection(envelope):
    root, source, dest = envelope
    publication.publish(root, dest)
    assert json.loads((dest / source.name).read_text()) == capability()
    assert (dest / source.name).read_bytes() != source.read_bytes()
    assert (dest / source.name).stat().st_mode & 0o777 == 0o600
    assert dest.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("mutation", ["extra", "secret", "bool", "status", "qualification", "raw"])
def test_reject_untrusted_fields(envelope, mutation):
    root, source, dest = envelope
    data = capability()
    if mutation == "extra":
        data["secret"] = "CANARY-CREDENTIAL"
    elif mutation == "secret":
        data["facts"]["strace"] = "CANARY-CREDENTIAL"
    elif mutation == "bool":
        data["facts"]["strace"] = 1
    elif mutation == "status":
        data["status"] = "UNSUPPORTED"
    elif mutation == "qualification":
        data["qualification"] = "PASS"
    else:
        data["raw_evidence"] = "UPLOADED"
    source.write_text(json.dumps(data) + "\n")
    assert publication.main([str(root), str(dest)]) == 2
    assert not dest.exists()


@pytest.mark.parametrize(
    "kind",
    ["symlink", "fifo", "hardlink", "mode", "oversize", "duplicate", "float", "missing", "no_newline"],
)
def test_private_input_boundary(envelope, kind):
    root, source, dest = envelope
    if kind == "symlink":
        source.rename(root / "other")
        source.symlink_to(root / "other")
    elif kind == "fifo":
        source.unlink()
        os.mkfifo(source, 0o600)
    elif kind == "hardlink":
        os.link(source, root / "other")
    elif kind == "mode":
        source.chmod(0o644)
    elif kind == "oversize":
        source.write_text("x" * 4097)
    elif kind == "duplicate":
        source.write_text('{"schema":1,"schema":2}\n')
    elif kind == "float":
        source.write_text('{"schema":1e999}\n')
    elif kind == "missing":
        source.unlink()
    else:
        source.write_text(json.dumps(capability()))
    assert publication.main([str(root), str(dest)]) == 2
    assert not dest.exists()


def test_refuse_existing_destination(envelope):
    root, _, dest = envelope
    dest.mkdir(mode=0o700)
    marker = dest / "keep"
    marker.write_text("untouched")
    assert publication.main([str(root), str(dest)]) == 2
    assert marker.read_text() == "untouched"


def test_failure_message_never_contains_input(envelope, capsys):
    root, source, dest = envelope
    source.write_text("CANARY-CREDENTIAL\n")
    assert publication.main([str(root), str(dest)]) == 2
    assert capsys.readouterr().err == "public diagnostic summary unavailable\n"


def summary(mode="diagnostic"):
    return dict(
        schema="forge-public-cli-capture-summary-v4",
        mode=mode,
        receipt_status="DELIVERED",
        harness_shell_exit=7,
        worker_returncode=0,
        cli_returncode=7,
        cli_returncode_before_cleanup=7,
        cleanup_induced_termination=False,
        collector_delivery="HEALTHY" if mode == "diagnostic" else "NOT_APPLICABLE",
        cleanup="COMPLETE",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
        controller_stage="UNKNOWN",
        controller_error_category="UNKNOWN",
        failure_observation="NOT_OBSERVED",
        launch_stderr_observation="NOT_ASSESSED",
        startup_evidence="NOT_OBSERVED",
        startup_stage="NOT_OBSERVED",
        startup_error_category="NOT_OBSERVED",
    )


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
def test_preserves_independent_nonzero_status(envelope, mode):
    root, _, dest = envelope
    path = root / (mode + ".json")
    path.write_text(json.dumps(summary(mode)) + "\n")
    path.chmod(0o600)
    publication.publish(root, dest)
    assert json.loads((dest / path.name).read_text()) == summary(mode)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "UNKNOWN"),
        ("schema", "secret"),
        ("receipt_status", "PASS"),
        ("harness_shell_exit", True),
        ("harness_shell_exit", 256),
        ("worker_returncode", -128),
        ("cli_returncode", "secret"),
        ("cli_returncode_before_cleanup", 256),
        ("cleanup_induced_termination", 0),
        ("collector_delivery", "NOT_APPLICABLE"),
        ("cleanup", "secret"),
        ("qualification", "PASS"),
        ("raw_evidence", "secret"),
    ],
)
def test_summary_closed_domains(field, value):
    data = summary()
    data[field] = value
    with pytest.raises((ValueError, TypeError)):
        publication.validate(data, "diagnostic.json")


def test_undelivered_cannot_claim_observations():
    data = summary()
    data["receipt_status"] = "UNAVAILABLE"
    with pytest.raises(ValueError):
        publication.validate(data, "diagnostic.json")
    for key in (
        "worker_returncode",
        "cli_returncode",
        "cli_returncode_before_cleanup",
        "cleanup_induced_termination",
    ):
        data[key] = None
    data["collector_delivery"] = data["cleanup"] = "NOT_OBSERVED"
    assert publication.validate(data, "diagnostic.json") == data


def test_baseline_cannot_claim_collector_health():
    data = summary("baseline")
    data["collector_delivery"] = "HEALTHY"
    with pytest.raises(ValueError):
        publication.validate(data, "baseline.json")


def test_summary_rejects_unknown_field():
    data = summary()
    data["private_message"] = "CANARY-CREDENTIAL"
    with pytest.raises(ValueError):
        publication.validate(data, "diagnostic.json")


def test_exact_legacy_v1_upgrade_does_not_invent_stage():
    old = summary()
    for key in publication.STARTUP_KEYS:
        del old[key]
    for field in (
        "controller_stage",
        "controller_error_category",
        "failure_observation",
        "launch_stderr_observation",
    ):
        del old[field]
    old["schema"] = "forge-public-cli-capture-summary-v1"
    upgraded = publication.validate(old, "diagnostic.json")
    assert upgraded == summary()
    assert old["schema"] == "forge-public-cli-capture-summary-v1"
    old["private_message"] = "CANARY"
    with pytest.raises(ValueError):
        publication.validate(old, "diagnostic.json")


@pytest.mark.parametrize(
    "field", ["controller_stage", "controller_error_category", "failure_observation"]
)
@pytest.mark.parametrize(
    "bad", [None, True, 1, [], {}, "CANARY"], ids=["null", "bool", "int", "list", "dict", "canary"]
)
def test_new_public_fields_are_closed(field, bad):
    data = summary()
    data[field] = bad
    with pytest.raises((ValueError, TypeError)):
        publication.validate(data, "diagnostic.json")


@pytest.mark.parametrize(
    "stage,category",
    [("FINALIZE", "NONE"), ("WORKER_RECEIPT", "NOT_FOUND"), ("WORKER_EXECUTION", "PERMISSION_DENIED")],
)
def test_public_stage_pair_preserves_original_status(stage, category):
    data = summary()
    data.update(controller_stage=stage, controller_error_category=category)
    assert publication.validate(data, "diagnostic.json") == data
    assert data["cli_returncode"] == data["harness_shell_exit"] == 7


@pytest.mark.parametrize(
    "stage,category",
    [
        ("UNKNOWN", "NONE"),
        ("FINALIZE", "UNKNOWN"),
        ("FINALIZE", "NOT_FOUND"),
        ("WORKER_RECEIPT", "NONE"),
    ],
)
def test_public_contradictory_pair_rejected(stage, category):
    data = summary()
    data.update(controller_stage=stage, controller_error_category=category)
    with pytest.raises(ValueError):
        publication.validate(data, "diagnostic.json")


def test_worker_receipt_observation_requires_closed_supporting_facts():
    data = summary()
    data["failure_observation"] = "WORKER_NONZERO_NO_RECEIPT"
    with pytest.raises(ValueError):
        publication.validate(data, "diagnostic.json")
    data.update(
        worker_returncode=1,
        cli_returncode=None,
        cli_returncode_before_cleanup=None,
        cleanup_induced_termination=None,
        collector_delivery="NOT_OBSERVED",
    )
    assert publication.validate(data, "diagnostic.json") == data
    data["receipt_status"] = "UNAVAILABLE"
    with pytest.raises(ValueError):
        publication.validate(data, "diagnostic.json")


def test_exact_v2_upgrade_does_not_invent_stderr_observation():
    old = summary()
    for key in publication.STARTUP_KEYS:
        del old[key]
    del old["launch_stderr_observation"]
    old["schema"] = "forge-public-cli-capture-summary-v2"
    assert publication.validate(old, "diagnostic.json") == summary()
    old["private_stderr"] = "CANARY"
    with pytest.raises(ValueError):
        publication.validate(old, "diagnostic.json")


@pytest.mark.parametrize(
    "bad", [None, True, 1, [], {}, "CANARY"], ids=["null", "bool", "int", "list", "dict", "canary"]
)
def test_launch_public_enum_is_closed(bad):
    value = summary()
    value["launch_stderr_observation"] = bad
    with pytest.raises((ValueError, TypeError)):
        publication.validate(value, "diagnostic.json")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("worker_returncode", 0),
        ("worker_returncode", True),
        ("cli_returncode", 7),
        ("cli_returncode_before_cleanup", 7),
        ("cleanup_induced_termination", False),
        ("collector_delivery", "HEALTHY"),
        ("receipt_status", "UNAVAILABLE"),
        ("failure_observation", "NOT_OBSERVED"),
    ],
)
def test_launch_public_observation_requires_consistent_facts(field, bad):
    value = summary()
    value.update(
        worker_returncode=1,
        cli_returncode=None,
        cli_returncode_before_cleanup=None,
        cleanup_induced_termination=None,
        collector_delivery="NOT_OBSERVED",
        failure_observation="WORKER_NONZERO_NO_RECEIPT",
        launch_stderr_observation="STDERR_EXACT_UNSHARE_PERMISSION_DENIED",
    )
    assert publication.validate(value, "diagnostic.json") == value
    value[field] = bad
    with pytest.raises(ValueError):
        publication.validate(value, "diagnostic.json")


def test_exact_v3_upgrade_has_no_startup_evidence():
    old = summary()
    for key in publication.STARTUP_KEYS:
        del old[key]
    old["schema"] = "forge-public-cli-capture-summary-v3"
    assert publication.validate(old, "diagnostic.json") == summary()
    old["private_startup_error"] = "PRIVATE"
    with pytest.raises(ValueError):
        publication.validate(old, "diagnostic.json")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("startup_evidence", True),
        ("startup_evidence", "PASS"),
        ("startup_stage", "PRIVATE"),
        ("startup_error_category", []),
        ("startup_error_category", "NONE"),
    ],
)
def test_closed_startup_public_fields(field, bad):
    value = summary()
    value[field] = bad
    with pytest.raises((ValueError, TypeError)):
        publication.validate(value, "diagnostic.json")

"""Offline public-identity transport tests, not native Linux CI qualification.

Every identity below is an explicit synthetic public fixture. No live collection,
test service, installation, or manifest-generation command is run by this file.
"""

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from scripts import ci_private_transport as transport
from scripts import ci_test_shards as ci


SHA = "a" * 40
CANARY = "PRIVATE-/home/private-user/work-PID-7654321--secret-parameter"
COLLECTION_PHASES = ("full", "ownership", "cgroup", "local")
PUBLIC_MODULES = [
    "tests/test_ci_test_shards.py",
    "tests/test_cli_banner.py",
    "tests/test_cli_config_errors.py",
    "tests/test_cli_detect.py",
    "tests/test_cli_entrypoint.py",
    "tests/test_cli_init.py",
]
SKIPPED_MODULE = "tests/test_dead_code.py"
REASON = "synthetic optional dependency absent"


def collection(phase, nodes, skips=()):
    return {
        "sha": SHA,
        "phase": phase,
        "exitstatus": 0,
        "collected": list(nodes),
        "collection_skips": list(skips),
        "collection_skip_reasons": {module: REASON for module in skips},
        "collection_errors": [],
        "reports": [],
        "subtests": [],
    }


def evidence(cases_per_module=2):
    ownership = [f"{module}::test_public_owner" for module in ci.OWNERSHIP]
    ordinary = [
        f"{module}::test_public_case[public-{index:05d}]"
        for module in PUBLIC_MODULES
        for index in range(cases_per_module)
    ]
    collections = {
        "full": collection("full", [*ownership, *ordinary], [SKIPPED_MODULE]),
        "ownership": collection("ownership", ownership),
        "cgroup": collection("cgroup", [f"{ci.CGROUP[0]}::test_public_cgroup"]),
        "local": collection("local", [f"{ci.LOCAL[0]}::test_public_local"]),
    }
    policy = {
        "module_seconds": {PUBLIC_MODULES[0]: 100},
        "runtime_skips": {ordinary[0]: REASON},
        "collection_skips": [SKIPPED_MODULE],
        "collection_skip_reasons": {SKIPPED_MODULE: REASON},
    }
    spare = f"{PUBLIC_MODULES[0]}::test_public_deselected"
    nodes = sorted({spare, *(node for item in collections.values() for node in item["collected"])})
    manifest = {
        "schema": "forge-public-test-identities-v1",
        "modules": sorted({SKIPPED_MODULE, *(node.split("::", 1)[0] for node in nodes)}),
        "nodes": nodes,
    }
    return SimpleNamespace(
        policy=policy,
        collections=collections,
        plan=ci.build_plan(collections, SHA, policy),
        manifest=manifest,
        spare=spare,
    )


@pytest.fixture
def example():
    return evidence()


def execution(plan, phase):
    part = plan["phases"][phase]
    receipt = collection(phase, part["nodes"], part["collection_skips"])
    receipt["reports"] = [
        {"nodeid": node, "when": when, "outcome": "passed", "skip_reason": None, "wasxfail": False}
        for node in part["nodes"]
        for when in ("setup", "call", "teardown")
    ]
    return receipt


def public_node(node, manifest):
    # Independent expected value: the entire reviewed identity selects one index.
    return f"{node.split('::', 1)[0]}::public-{manifest['nodes'].index(node)}"


def expected_policy(example):
    return {
        "module_seconds": example.policy["module_seconds"],
        "runtime_skips": {
            public_node(node, example.manifest): "public-reason-0"
            for node in example.policy["runtime_skips"]
        },
        "collection_skips": [SKIPPED_MODULE],
        "collection_skip_reasons": {SKIPPED_MODULE: "public-reason-0"},
        "source_policy_sha256": ci.digest(example.policy),
        "public_manifest_sha256": ci.digest(example.manifest),
    }


def expected_collection(value, manifest):
    result = copy.deepcopy(value)
    result["collected"] = [public_node(node, manifest) for node in value["collected"]]
    result["collection_skip_reasons"] = {
        module: "public-reason-0" for module in value["collection_skips"]
    }
    for kind in ("reports", "subtests"):
        for report in result[kind]:
            report["nodeid"] = public_node(report["nodeid"], manifest)
            if report["skip_reason"] is not None:
                report["skip_reason"] = "public-reason-0"
    return result


def approved_plan(example):
    policy = expected_policy(example)
    collections = {
        phase: expected_collection(value, example.manifest)
        for phase, value in example.collections.items()
    }
    return ci.build_plan(collections, SHA, policy)


def jobs():
    return {name: {"result": "success"} for name in ("plan", "process-contracts", "service-free")}


def public_text(directory):
    return "\n".join(path.read_text() for path in directory.rglob("*") if path.is_file())


def assert_private(value):
    text = json.dumps(value) if not isinstance(value, str) else value
    assert CANARY not in text
    assert "7654321" not in text
    assert hashlib.sha256(CANARY.encode()).hexdigest() not in text


def materialize_public(directory, example):
    for phase, value in example.collections.items():
        ci.write(directory / f"collection-{phase}.json", expected_collection(value, example.manifest))
    plan = approved_plan(example)
    ci.write(directory / "plan.json", plan)
    for phase in ci.PHASES:
        ci.write(
            directory / f"receipt-{phase}.json",
            expected_collection(execution(example.plan, phase), example.manifest),
        )
    return plan


def cli_args(tmp_path, example, command="plan", phase=None):
    policy_path = tmp_path / "inputs/policy.json"
    manifest_path = tmp_path / "inputs/manifest.json"
    ci.write(policy_path, example.policy)
    ci.write(manifest_path, example.manifest)
    args = [
        command,
        "--sha",
        SHA,
        "--directory",
        str(tmp_path / "private"),
        "--manifest",
        str(manifest_path),
    ]
    if command in ("plan", "run"):
        args.extend(["--public-directory", str(tmp_path / "public")])
    if command in ("run", "verify"):
        plan_path = tmp_path / "inputs/plan.json"
        ci.write(plan_path, approved_plan(example))
        args.extend(["--plan", str(plan_path)])
    if phase is not None:
        args.extend(["--phase", phase])
    return args, policy_path


def fake_collector(monkeypatch, example, mutate=None):
    def collect(directory, sha, policy):
        assert sha == SHA and policy == example.policy
        values = copy.deepcopy(example.collections)
        if mutate:
            mutate(values)
        for phase, value in values.items():
            ci.write(directory / f"collection-{phase}.json", value)
        ci.write(directory / "plan.json", ci.build_plan(values, sha, policy))

    monkeypatch.setattr(ci, "collect_plan", collect)


def fake_execution(monkeypatch, receipt, returncode=0):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] is False
        assert "ACTIONS_RUNTIME_TOKEN" not in kwargs["env"]
        assert kwargs.get("stdout") is not None and kwargs.get("stderr") is not None
        kwargs["stdout"].write(CANARY + "\n")
        print(CANARY)
        report = Path(command[command.index("--ci-report") + 1])
        ci.write(report, receipt)
        junit = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        junit.write_text(CANARY)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(transport.subprocess, "run", run)
    return commands


def test_projection_uses_complete_public_allowlist_indices(example):
    assert transport.validate_manifest(example.manifest) == example.manifest
    assert transport.project_policy(example.policy, example.manifest) == expected_policy(example)
    before = copy.deepcopy(example.collections)
    for value in example.collections.values():
        actual = transport.project_receipt(value, example.manifest, example.policy)
        assert actual == expected_collection(value, example.manifest)
        assert len(actual["collected"]) == len(value["collected"])
        assert not set(actual["collected"]) & set(example.manifest["nodes"])
    assert example.collections == before


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "extra",
        "duplicate-module",
        "duplicate-node",
        "unsorted-module",
        "unsorted-node",
        "empty",
        "nonstring",
        "unknown-module",
        "absolute",
        "traversal",
        "missing-node-separator",
    ],
)
def test_manifest_is_closed_and_rejects_ambiguous_identity_lists(example, mutation):
    manifest = example.manifest
    if mutation == "schema":
        manifest["schema"] = "unreviewed-v2"
    elif mutation == "extra":
        manifest[CANARY] = CANARY
    elif mutation == "duplicate-module":
        manifest["modules"].append(manifest["modules"][0])
    elif mutation == "duplicate-node":
        manifest["nodes"].append(manifest["nodes"][0])
    elif mutation == "unsorted-module":
        manifest["modules"].reverse()
    elif mutation == "unsorted-node":
        manifest["nodes"].reverse()
    elif mutation == "empty":
        manifest["nodes"] = []
    elif mutation == "nonstring":
        manifest["nodes"] = [True]
    elif mutation == "unknown-module":
        manifest["nodes"].append("tests/not_reviewed.py::test_public")
        manifest["nodes"].sort()
    elif mutation in ("absolute", "traversal"):
        manifest["modules"].append(
            "/home/private/test_x.py" if mutation == "absolute" else "tests/../test_x.py"
        )
        manifest["modules"].sort()
    elif mutation == "missing-node-separator":
        manifest["nodes"] = [manifest["modules"][0]]
    with pytest.raises((ValueError, TypeError)):
        transport.validate_manifest(manifest)


@pytest.mark.parametrize("location", ["collected", "report", "subtest", "module", "reason"])
def test_unknown_raw_values_cannot_be_projected_even_with_a_known_module(example, location):
    receipt = execution(example.plan, "shard-0")
    unknown = f"{receipt['collected'][0].split('::', 1)[0]}::test_public_case[{CANARY}]"
    if location == "collected":
        receipt["collected"][0] = unknown
    elif location == "report":
        receipt["reports"][0]["nodeid"] = unknown
    elif location == "subtest":
        receipt["subtests"] = [dict(receipt["reports"][1], nodeid=unknown)]
    elif location == "module":
        receipt["collection_skips"].append(f"tests/{CANARY}.py")
        receipt["collection_skip_reasons"][f"tests/{CANARY}.py"] = REASON
    else:
        receipt["reports"][0].update(outcome="skipped", skip_reason=CANARY)
    with pytest.raises((ValueError, TypeError)):
        transport.project_receipt(receipt, example.manifest, example.policy)


def test_plan_keeps_four_exact_partitions_and_original_collections(monkeypatch, tmp_path, example):
    fake_collector(monkeypatch, example)
    private = tmp_path / "private"
    public = tmp_path / "public"
    transport.collect_plan(private, public, SHA, example.policy, example.manifest)
    plan = ci.read(public / "plan.json")
    assert plan == approved_plan(example)
    assert {p for p in plan["phases"] if p.startswith("shard-")} == {f"shard-{i}" for i in range(4)}
    for phase in COLLECTION_PHASES:
        assert ci.read(public / f"collection-{phase}.json") == expected_collection(
            example.collections[phase], example.manifest
        )
    public_nodes = [
        node
        for phase in ("ownership", "shard-0", "shard-1", "shard-2", "shard-3")
        for node in plan["phases"][phase]["nodes"]
    ]
    assert len(public_nodes) == len(set(public_nodes)) == len(example.plan["full_nodes"])
    assert set(public_nodes) == set(plan["full_nodes"])


@pytest.mark.parametrize("mutation", ["new", "changed", "duplicate", "missing", "reason", "module"])
def test_plan_failure_is_fixed_stop_without_canary_publication(
    monkeypatch, tmp_path, capsys, example, mutation
):
    def mutate(values):
        full = values["full"]
        node = full["collected"][-1]
        if mutation == "new":
            full["collected"].append(node + CANARY)
        elif mutation == "changed":
            full["collected"][-1] = node + CANARY
        elif mutation == "duplicate":
            full["collected"].append(node)
        elif mutation == "missing":
            full["collected"].remove(values["ownership"]["collected"][0])
        elif mutation == "reason":
            full["collection_skip_reasons"][SKIPPED_MODULE] = CANARY
        else:
            full["collected"][-1] = f"tests/{CANARY}.py::test_public"

    fake_collector(monkeypatch, example, mutate)
    args, policy_path = cli_args(tmp_path, example)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    assert transport.main(args) != 0
    captured = capsys.readouterr()
    exposed = captured.out + captured.err + public_text(tmp_path / "public")
    assert_private(exposed)
    assert "STOP" in exposed
    assert not (tmp_path / "public/plan.json").exists()


@pytest.mark.parametrize("phase", ci.PHASES)
def test_run_preserves_original_selection_durations_junit_and_private_raw_files(
    monkeypatch, tmp_path, capsys, example, phase
):
    receipt = execution(example.plan, phase)
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "runtime-secret-canary")
    commands = fake_execution(monkeypatch, receipt)
    private, public = tmp_path / "private", tmp_path / "public"
    plan = approved_plan(example)
    transport.run_phase(phase, plan, private, public, example.policy, example.manifest)
    assert len(commands) == 1
    command = commands[0]
    expected_args = ci.selection(phase)
    if phase.startswith("shard-"):
        expected_args = [*example.plan["phases"][phase]["modules"], *expected_args]
    report = Path(command[command.index("--ci-report") + 1])
    assert report.is_relative_to(private)
    assert command == ci.command_for_pytest(
        phase,
        SHA,
        report,
        [*expected_args, "--durations=20", f"--junitxml={private / (phase + '.xml')}"],
    )
    assert ci.read(public / f"receipt-{phase}.json") == expected_collection(receipt, example.manifest)
    assert (private / f"{phase}.xml").read_text() == CANARY
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(public))
    assert CANARY in (private / f"{phase}.log").read_text()
    assert not list(public.glob("*.xml"))


@pytest.mark.parametrize("returncode", [-15, -9, 1, 2, 5, 17, 124, 137, 143, 255])
def test_nonzero_pytest_status_cannot_be_overridden_by_success_receipt(
    monkeypatch, tmp_path, capsys, example, returncode
):
    fake_execution(monkeypatch, execution(example.plan, "shard-0"), returncode)
    args, policy_path = cli_args(tmp_path, example, "run", "shard-0")
    monkeypatch.setattr(ci, "POLICY", policy_path)
    assert transport.main(args) == returncode
    captured = capsys.readouterr()
    exposed = captured.out + captured.err + public_text(tmp_path / "public")
    assert_private(exposed)
    assert "STOP" in exposed
    assert not (tmp_path / "public/receipt-shard-0.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing",
        "same-count-known-substitution",
        "same-count-unknown-substitution",
        "failed",
        "xfail",
        "skip",
        "missing-call",
        "subtest-failure",
        "subtest-skip",
    ],
)
def test_run_rejects_false_green_against_independent_plan(
    monkeypatch, tmp_path, capsys, example, mutation
):
    phase = "shard-0"
    receipt = execution(example.plan, phase)
    node = receipt["collected"][0]
    if mutation == "duplicate":
        receipt["reports"].append(dict(receipt["reports"][0]))
    elif mutation == "missing":
        receipt["collected"].remove(node)
        receipt["reports"] = [item for item in receipt["reports"] if item["nodeid"] != node]
    elif mutation.startswith("same-count"):
        replacement = example.spare if mutation == "same-count-known-substitution" else node + CANARY
        receipt["collected"][0] = replacement
        for item in receipt["reports"]:
            if item["nodeid"] == node:
                item["nodeid"] = replacement
    elif mutation == "failed":
        receipt["reports"][1]["outcome"] = "failed"
    elif mutation == "xfail":
        receipt["reports"][1]["wasxfail"] = True
    elif mutation == "skip":
        receipt["reports"][0].update(outcome="skipped", skip_reason=CANARY)
        receipt["reports"].pop(1)
    elif mutation == "missing-call":
        receipt["reports"].pop(1)
    else:
        receipt["subtests"] = [
            dict(receipt["reports"][1], outcome="failed" if mutation.endswith("failure") else "skipped")
        ]
    fake_execution(monkeypatch, receipt)
    args, policy_path = cli_args(tmp_path, example, "run", phase)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    assert transport.main(args) != 0
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(tmp_path / "public"))
    assert not (tmp_path / "public/receipt-shard-0.json").exists()


def test_known_skip_is_exact_before_reason_projection(monkeypatch, tmp_path, example):
    node = next(iter(example.policy["runtime_skips"]))
    phase = next(phase for phase in ci.PHASES if node in example.plan["phases"][phase]["nodes"])
    receipt = execution(example.plan, phase)
    receipt["reports"] = [
        item for item in receipt["reports"] if not (item["nodeid"] == node and item["when"] == "call")
    ]
    next(
        item for item in receipt["reports"] if item["nodeid"] == node and item["when"] == "setup"
    ).update(outcome="skipped", skip_reason=REASON)
    fake_execution(monkeypatch, receipt)
    public = tmp_path / "public"
    transport.run_phase(
        phase, approved_plan(example), tmp_path / "private", public, example.policy, example.manifest
    )
    projected = ci.read(public / f"receipt-{phase}.json")
    assert any(item["skip_reason"] == "public-reason-0" for item in projected["reports"])
    assert REASON not in public_text(public)
    ci.validate_execution(projected, phase, approved_plan(example), expected_policy(example))


def test_other_nodes_allowlisted_reason_does_not_authorize_a_changed_skip(example):
    node = next(iter(example.policy["runtime_skips"]))
    phase = next(phase for phase in ci.PHASES if node in example.plan["phases"][phase]["nodes"])
    receipt = execution(example.plan, phase)
    other_reason = "another synthetic allowed reason"
    example.policy["runtime_skips"][example.spare] = other_reason
    next(item for item in receipt["reports"] if item["nodeid"] == node).update(
        outcome="skipped", skip_reason=other_reason
    )
    with pytest.raises((ValueError, TypeError)):
        transport.project_receipt(receipt, example.manifest, example.policy)
    receipt = example.collections["full"]
    receipt["collection_skip_reasons"][SKIPPED_MODULE] = other_reason
    with pytest.raises((ValueError, TypeError)):
        transport.project_receipt(receipt, example.manifest, example.policy)


def test_repeated_passing_subtests_remain_valid_without_weakening_ordinary_reports(
    monkeypatch, tmp_path, example
):
    receipt = execution(example.plan, "shard-0")
    receipt["subtests"] = [dict(receipt["reports"][1]), dict(receipt["reports"][1])]
    fake_execution(monkeypatch, receipt)
    public = tmp_path / "public"
    transport.run_phase(
        "shard-0",
        approved_plan(example),
        tmp_path / "private",
        public,
        example.policy,
        example.manifest,
    )
    result = ci.read(public / "receipt-shard-0.json")
    assert len(result["subtests"]) == 2
    assert len(result["reports"]) == len(receipt["reports"])
    ci.validate_execution(result, "shard-0", approved_plan(example), expected_policy(example))


def test_ownership_skip_is_rejected_even_when_the_raw_policy_allows_its_reason(
    monkeypatch, tmp_path, example
):
    receipt = execution(example.plan, "ownership")
    node = receipt["collected"][0]
    example.policy["runtime_skips"][node] = REASON
    receipt["reports"][0].update(outcome="skipped", skip_reason=REASON)
    receipt["reports"].pop(1)
    fake_execution(monkeypatch, receipt)
    with pytest.raises((ValueError, TypeError)):
        transport.run_phase(
            "ownership",
            approved_plan(example),
            tmp_path / "private",
            tmp_path / "public",
            example.policy,
            example.manifest,
        )
    assert not (tmp_path / "public/receipt-ownership.json").exists()


@pytest.mark.parametrize("drift", ["policy-weight", "policy-reason", "manifest-node", "manifest-module"])
def test_run_refuses_policy_and_manifest_drift_before_launch(monkeypatch, tmp_path, example, drift):
    plan = approved_plan(example)
    if drift == "policy-weight":
        example.policy["module_seconds"][PUBLIC_MODULES[0]] += 1
    elif drift == "policy-reason":
        example.policy["runtime_skips"][next(iter(example.policy["runtime_skips"]))] = "changed reason"
    elif drift == "manifest-node":
        example.manifest["nodes"].append(f"{PUBLIC_MODULES[-1]}::test_another_public_case")
        example.manifest["nodes"].sort()
    else:
        example.manifest["modules"].append("tests/test_another_public_module.py")
        example.manifest["modules"].sort()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Inputs drifting from the independent plan must stop before pytest")

    monkeypatch.setattr(transport.subprocess, "run", forbidden)
    with pytest.raises((ValueError, TypeError)):
        transport.run_phase(
            "shard-0",
            plan,
            tmp_path / "private",
            tmp_path / "public",
            example.policy,
            example.manifest,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "bool-status",
        "error",
        "extra-report-key",
        "wrong-when",
        "wrong-outcome",
        "nonbool-xfail",
        "passed-reason",
        "wrong-phase",
        "wrong-sha",
        "skip-map-missing",
    ],
)
def test_receipt_schema_has_no_unvalidated_free_text_projection(example, mutation):
    receipt = execution(example.plan, "shard-0")
    if mutation == "extra":
        receipt["diagnostics"] = CANARY
    elif mutation == "missing":
        del receipt["subtests"]
    elif mutation == "bool-status":
        receipt["exitstatus"] = False
    elif mutation == "error":
        receipt["collection_errors"] = [CANARY]
    elif mutation == "extra-report-key":
        receipt["reports"][0]["duration"] = CANARY
    elif mutation == "wrong-when":
        receipt["reports"][0]["when"] = CANARY
    elif mutation == "wrong-outcome":
        receipt["reports"][0]["outcome"] = CANARY
    elif mutation == "nonbool-xfail":
        receipt["reports"][0]["wasxfail"] = 0
    elif mutation == "passed-reason":
        receipt["reports"][0]["skip_reason"] = CANARY
    elif mutation == "wrong-phase":
        receipt["phase"] = CANARY
    elif mutation == "wrong-sha":
        receipt["sha"] = CANARY
    elif mutation == "skip-map-missing":
        receipt["collection_skips"] = [SKIPPED_MODULE]
        receipt["collection_skip_reasons"] = {}
    with pytest.raises((ValueError, TypeError)):
        transport.project_receipt(receipt, example.manifest, example.policy)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "unknown-node",
        "unknown-module",
        "reason-map",
        "nan",
        "infinity",
        "negative",
        "boolean",
        "source-run",
        "artifact-digest",
    ],
)
def test_policy_is_closed_and_cannot_add_unreviewed_identity_or_metadata(example, mutation):
    policy = example.policy
    if mutation == "extra":
        policy[CANARY] = CANARY
    elif mutation == "missing":
        del policy["runtime_skips"]
    elif mutation == "unknown-node":
        policy["runtime_skips"][f"{PUBLIC_MODULES[0]}::test_public[{CANARY}]"] = REASON
    elif mutation == "unknown-module":
        policy["module_seconds"][f"tests/{CANARY}.py"] = 1
    elif mutation == "reason-map":
        policy["collection_skip_reasons"] = {}
    elif mutation in ("nan", "infinity", "negative", "boolean"):
        policy["module_seconds"][PUBLIC_MODULES[0]] = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "negative": -1,
            "boolean": True,
        }[mutation]
    elif mutation == "source-run":
        policy["source_run"] = CANARY
    else:
        policy["artifact_sha256"] = CANARY
    with pytest.raises((ValueError, TypeError)):
        transport.project_policy(policy, example.manifest)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate-key",
        "nested-duplicate",
        "trailing",
        "nan",
        "overflow",
        "deep",
        "oversized",
        "invalid-utf8",
        "symlink",
        "parent-symlink",
        "fifo",
        "directory",
    ],
)
def test_hostile_manifest_file_stops_before_collection_without_path_or_content_disclosure(
    monkeypatch, tmp_path, capsys, example, kind
):
    args, policy_path = cli_args(tmp_path, example)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    manifest = tmp_path / "inputs/manifest.json"
    original = manifest.read_bytes()
    if kind == "missing":
        manifest.unlink()
    elif kind == "duplicate-key":
        manifest.write_bytes(original.rstrip()[:-1] + b',"schema":"' + CANARY.encode() + b'"}')
    elif kind == "nested-duplicate":
        manifest.write_text('{"nested":{"x":0,"x":"' + CANARY + '"}}')
    elif kind == "trailing":
        manifest.write_bytes(original + CANARY.encode())
    elif kind == "nan":
        manifest.write_text('{"private":NaN,"canary":"' + CANARY + '"}')
    elif kind == "overflow":
        manifest.write_text('{"private":1e999,"canary":"' + CANARY + '"}')
    elif kind == "deep":
        manifest.write_text("[" * 100 + '"' + CANARY + '"' + "]" * 100)
    elif kind == "oversized":
        monkeypatch.setattr(transport, "MAX_BYTES", 4096)
        manifest.write_bytes(b" " * 4097 + CANARY.encode())
    elif kind == "invalid-utf8":
        manifest.write_bytes(b"\xff" + CANARY.encode())
    elif kind == "symlink":
        target = tmp_path / "real-manifest.json"
        target.write_bytes(original)
        manifest.unlink()
        manifest.symlink_to(target)
    elif kind == "parent-symlink":
        link = tmp_path / "linked-inputs"
        link.symlink_to(manifest.parent, target_is_directory=True)
        args[args.index("--manifest") + 1] = str(link / manifest.name)
    elif kind == "fifo":
        manifest.unlink()
        os.mkfifo(manifest)
    else:
        manifest.unlink()
        manifest.mkdir()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Malformed or missing manifest must stop before any collection")

    monkeypatch.setattr(ci, "collect_plan", forbidden)
    assert transport.main(args) != 0
    captured = capsys.readouterr()
    exposed = captured.out + captured.err + public_text(tmp_path / "public")
    assert_private(exposed)
    assert str(tmp_path) not in exposed
    assert captured.out == ""
    assert captured.err == "STOP: Private CI validation failed.\n"


@pytest.mark.parametrize("encoded", [False, True], ids=["plain", "escaped"])
def test_megabyte_string_is_rejected_before_json_decoding(monkeypatch, tmp_path, encoded):
    # A modest hostile fixture exercises tokenizer bounds without constructing
    # a MAX_BYTES-sized allocation or running any collection/test subprocess.
    body = b"\\u0061" * (transport.MAX_NODE + 1) if encoded else b"a" * (1024 * 1024)
    path = tmp_path / "hostile-string.json"
    path.write_bytes(b'{"value":"' + body + b'"}')

    def forbidden(*_args, **_kwargs):
        pytest.fail("Oversized JSON string must be rejected before decoding/copying it")

    monkeypatch.setattr(transport.json, "loads", forbidden)
    with pytest.raises(ValueError):
        transport.read_json(path)


@pytest.mark.parametrize("repetitions", [1000, 2000, 4000])
def test_unterminated_escaped_quote_sequence_stops_before_json_decoding(
    monkeypatch, tmp_path, repetitions
):
    path = tmp_path / "unterminated.json"
    path.write_bytes(b'"' + b'\\"' * repetitions)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Unterminated string must fail the bounded tokenizer before JSON decoding")

    monkeypatch.setattr(transport.json, "loads", forbidden)
    with pytest.raises(ValueError):
        transport.read_json(path)


@pytest.mark.parametrize("character", ["x", "\U0001f680"], ids=["ascii-limit", "unicode-limit"])
def test_bounded_json_reader_keeps_valid_nested_escaped_and_unicode_values(tmp_path, character):
    value = {
        "nested": [{"value": 'literal [{]} \\" quoted text, 公开, \U0001f680', "weight": 0.5}],
        "literal_escape": r"\u007b is text, not a structure delimiter",
        "limit": character * transport.MAX_NODE,
    }
    path = tmp_path / "valid.json"
    path.write_text(json.dumps(value, ensure_ascii=True))
    assert transport.read_json(path) == value


def test_original_collection_command_output_and_selection_remain_private(
    monkeypatch, tmp_path, capsys, example
):
    commands = []

    def run(command, **kwargs):
        phase = command[command.index("--ci-phase") + 1]
        report = Path(command[command.index("--ci-report") + 1])
        commands.append(command)
        assert kwargs["check"] is True
        assert kwargs.get("stdout") is not None and kwargs.get("stderr") is not None
        kwargs["stdout"].write(CANARY + "\n")
        print(CANARY)
        ci.write(report, example.collections[phase])
        assert command == ci.command_for_pytest(
            phase, SHA, report, ["--collect-only", "-q", *ci.selection(phase)]
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(transport.subprocess, "run", run)
    private, public = tmp_path / "private", tmp_path / "public"
    transport.collect_plan(private, public, SHA, example.policy, example.manifest)
    assert len(commands) == 4
    assert ci.read(public / "plan.json") == approved_plan(example)
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(public))
    assert CANARY in (private / "collection.log").read_text()


@pytest.mark.parametrize("returncode", [2, 5, 137])
def test_failed_original_collection_exit_is_preserved_and_stays_private(
    monkeypatch, tmp_path, capsys, example, returncode
):
    def run(command, **kwargs):
        kwargs["stdout"].write(CANARY + "\n")
        raise transport.subprocess.CalledProcessError(returncode, command, output=CANARY, stderr=CANARY)

    monkeypatch.setattr(transport.subprocess, "run", run)
    args, policy_path = cli_args(tmp_path, example)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    assert transport.main(args) == returncode
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(tmp_path / "public"))
    assert not (tmp_path / "public/plan.json").exists()


@pytest.mark.parametrize("job", ["plan", "process-contracts", "service-free", "linux-tests"])
@pytest.mark.parametrize("status", ["success", "failure", "cancelled", "skipped"])
def test_summary_accepts_only_fixed_public_job_and_status_labels(tmp_path, capsys, job, status):
    result = transport.main(
        [
            "summary",
            "--job",
            job,
            "--status",
            status,
            "--public-directory",
            str(tmp_path),
        ]
    )
    assert result == 0
    assert ci.read(tmp_path / "summary.json") == {
        "schema": "forge-public-linux-ci-summary-v1",
        "job": job,
        "status": "PASS" if status == "success" else "STOP",
        "job_result": status,
        "raw_evidence": "NOT_PUBLISHED",
    }
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("option", ["--job", "--status", "--unknown"])
def test_cli_never_echoes_invalid_untrusted_arguments(tmp_path, capsys, option):
    args = ["summary", "--job", "plan", "--status", "failure", "--public-directory", str(tmp_path)]
    args.extend([option, CANARY])
    assert transport.main(args) != 0
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(tmp_path))
    assert captured.err == "STOP: Private CI validation failed.\n"


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "missing-collection",
        "duplicate-collection",
        "missing-receipt",
        "duplicate-receipt",
        "extra-receipt",
        "wrong-phase",
        "altered-plan",
        "self-consistent-receipt",
        "failed-job",
        "skipped-job",
        "cancelled-job",
        "missing-job",
        "policy-drift",
        "manifest-drift",
    ],
)
def test_aggregate_rebuilds_four_manifests_and_checks_seven_exact_receipts(tmp_path, example, mutation):
    plan = materialize_public(tmp_path, example)
    needs = jobs()
    if mutation == "missing-collection":
        (tmp_path / "collection-full.json").unlink()
    elif mutation == "duplicate-collection":
        ci.write(tmp_path / "duplicate/collection-full.json", ci.read(tmp_path / "collection-full.json"))
    elif mutation == "missing-receipt":
        (tmp_path / "receipt-shard-0.json").unlink()
    elif mutation == "duplicate-receipt":
        ci.write(tmp_path / "duplicate/receipt-shard-0.json", ci.read(tmp_path / "receipt-shard-0.json"))
    elif mutation == "extra-receipt":
        ci.write(tmp_path / "receipt-shard-4.json", ci.read(tmp_path / "receipt-shard-0.json"))
    elif mutation == "wrong-phase":
        ci.write(tmp_path / "receipt-shard-0.json", ci.read(tmp_path / "receipt-shard-1.json"))
    elif mutation == "altered-plan":
        removed = plan["phases"]["shard-0"]["nodes"].pop()
        plan["full_nodes"].remove(removed)
    elif mutation == "self-consistent-receipt":
        path = tmp_path / "receipt-shard-0.json"
        receipt = ci.read(path)
        old, new = receipt["collected"][0], public_node(example.spare, example.manifest)
        receipt["collected"][0] = new
        for item in receipt["reports"]:
            if item["nodeid"] == old:
                item["nodeid"] = new
        ci.write(path, receipt)
    elif mutation in ("failed-job", "skipped-job", "cancelled-job"):
        needs["service-free"]["result"] = (
            "failure" if mutation == "failed-job" else mutation.removesuffix("-job")
        )
    elif mutation == "missing-job":
        del needs["service-free"]
    elif mutation == "policy-drift":
        example.policy["module_seconds"][PUBLIC_MODULES[0]] += 1
    elif mutation == "manifest-drift":
        example.manifest["nodes"].append(f"{PUBLIC_MODULES[-1]}::test_unused_public_case")
        example.manifest["nodes"].sort()
    if mutation is None:
        transport.verify(plan, tmp_path, SHA, example.policy, example.manifest, needs)
    else:
        with pytest.raises((ValueError, TypeError)):
            transport.verify(plan, tmp_path, SHA, example.policy, example.manifest, needs)


@pytest.mark.parametrize(
    "extra",
    [
        "private-log",
        "private-junit",
        "symlink-file",
        "symlink-directory",
        "duplicate-plan",
        "missing-plan",
        "raw-receipt",
        "summary-with-secret",
        "summary-pass-with-missing-receipt",
    ],
)
def test_public_aggregate_has_no_raw_file_or_summary_only_escape_hatch(tmp_path, example, extra):
    public = tmp_path / "public"
    plan = materialize_public(public, example)
    if extra in ("private-log", "private-junit"):
        (public / ("raw.log" if extra == "private-log" else "shard-0.xml")).write_text(CANARY)
    elif extra == "symlink-file":
        target = tmp_path / "private.json"
        target.write_text(CANARY)
        (public / "unexpected.json").symlink_to(target)
    elif extra == "symlink-directory":
        target = tmp_path / "private"
        target.mkdir()
        (target / "raw.log").write_text(CANARY)
        (public / "indirect").symlink_to(target, target_is_directory=True)
    elif extra == "duplicate-plan":
        ci.write(public / "duplicate/plan.json", plan)
    elif extra == "missing-plan":
        (public / "plan.json").unlink()
    elif extra == "raw-receipt":
        ci.write(public / "receipt-shard-0.json", execution(example.plan, "shard-0"))
    else:
        summary = {
            "schema": "forge-public-linux-ci-summary-v1",
            "job": "service-free",
            "status": "PASS",
            "job_result": "success",
            "raw_evidence": "NOT_PUBLISHED",
        }
        if extra == "summary-with-secret":
            summary["reason"] = CANARY
        else:
            (public / "receipt-shard-0.json").unlink()
        ci.write(public / "summary.json", summary)
    with pytest.raises((ValueError, TypeError, OSError)):
        transport.verify(plan, public, SHA, example.policy, example.manifest, jobs())


@pytest.mark.parametrize("relationship", ["same", "private-under-public", "public-under-private"])
def test_private_output_cannot_overlap_public_artifact_tree(
    monkeypatch, tmp_path, example, relationship
):
    private, public = tmp_path / "private", tmp_path / "public"
    if relationship == "same":
        private = public
    elif relationship == "private-under-public":
        private = public / "nested"
    else:
        public = private / "nested"

    def forbidden(*_args, **_kwargs):
        pytest.fail("Unsafe output overlap must stop before execution")

    monkeypatch.setattr(ci, "collect_plan", forbidden)
    monkeypatch.setattr(transport.subprocess, "run", forbidden)
    with pytest.raises((ValueError, TypeError, OSError)):
        transport.collect_plan(private, public, SHA, example.policy, example.manifest)
    with pytest.raises((ValueError, TypeError, OSError)):
        transport.run_phase(
            "shard-0", approved_plan(example), private, public, example.policy, example.manifest
        )


@pytest.mark.parametrize("command", ["plan", "run"])
def test_successful_exit_without_new_raw_evidence_cannot_reuse_an_old_receipt(
    monkeypatch, tmp_path, capsys, example, command
):
    private, public = tmp_path / "private", tmp_path / "public"
    for phase, value in example.collections.items():
        ci.write(private / f"collection-{phase}.json", value)
    ci.write(private / "plan.json", example.plan)
    ci.write(private / "receipt-shard-0.json", execution(example.plan, "shard-0"))
    (private / "shard-0.xml").write_text(CANARY)
    if command == "plan":
        ci.write(public / "plan.json", approved_plan(example))
        for phase, value in example.collections.items():
            ci.write(public / f"collection-{phase}.json", expected_collection(value, example.manifest))
    else:
        ci.write(
            public / "receipt-shard-0.json",
            expected_collection(execution(example.plan, "shard-0"), example.manifest),
        )
    args, policy_path = cli_args(tmp_path, example, command, "shard-0" if command == "run" else None)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    calls = []

    def collect(*_args):
        calls.append(
            (
                "collect_plan",
                (private / "plan.json").exists(),
                any(path.read_bytes() for path in private.glob("collection-*.json")),
            )
        )

    def run(*_args, **_kwargs):
        calls.append(
            (
                "run",
                bool((private / "receipt-shard-0.json").read_bytes()),
                bool((private / "shard-0.xml").read_bytes()),
            )
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ci, "collect_plan", collect)
    monkeypatch.setattr(transport.subprocess, "run", run)
    assert transport.main(args) != 0
    assert calls == [("collect_plan" if command == "plan" else "run", False, False)]
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(public))
    assert not (public / ("plan.json" if command == "plan" else "receipt-shard-0.json")).exists()


@pytest.mark.parametrize("length", [200108, 262144])
def test_reviewed_large_node_ids_round_trip_and_keep_exact_projection(tmp_path, example, length):
    assert transport.MAX_NODE == 262144
    old = next(iter(example.policy["runtime_skips"]))
    prefix = f"{old.split('::', 1)[0]}::test_public_case["
    node = prefix + "x" * (length - len(prefix) - 1) + "]"
    assert len(node) == length
    for receipt in example.collections.values():
        receipt["collected"] = [node if item == old else item for item in receipt["collected"]]
    example.policy["runtime_skips"][node] = example.policy["runtime_skips"].pop(old)
    example.manifest["nodes"] = sorted(
        node if item == old else item for item in example.manifest["nodes"]
    )
    example.plan = ci.build_plan(example.collections, SHA, example.policy)
    manifest_path = tmp_path / "reviewed-manifest.json"
    ci.write(manifest_path, example.manifest)
    manifest = transport.validate_manifest(transport.read_json(manifest_path))
    assert manifest == example.manifest
    phase = next(phase for phase in ci.PHASES if node in example.plan["phases"][phase]["nodes"])
    raw = execution(example.plan, phase)
    receipt_path = tmp_path / "raw-receipt.json"
    ci.write(receipt_path, raw)
    projected = transport.project_receipt(transport.read_json(receipt_path), manifest, example.policy)
    assert public_node(node, manifest) in projected["collected"]
    assert node not in json.dumps(projected)
    ci.validate_execution(projected, phase, approved_plan(example), expected_policy(example))


def test_node_above_dedicated_limit_is_rejected_before_decoding(monkeypatch, tmp_path, example):
    prefix = PUBLIC_MODULES[0] + "::test_public_case["
    node = prefix + "x" * (transport.MAX_NODE + 1 - len(prefix) - 1) + "]"
    assert len(node) == 262145
    example.manifest["nodes"].append(node)
    example.manifest["nodes"].sort()
    with pytest.raises(ValueError):
        transport.validate_manifest(example.manifest)
    path = tmp_path / "oversized-node.json"
    path.write_text(json.dumps(example.manifest))

    def forbidden(*_args, **_kwargs):
        pytest.fail("Node above the dedicated limit must stop before JSON decoding")

    monkeypatch.setattr(transport.json, "loads", forbidden)
    with pytest.raises(ValueError):
        transport.read_json(path)


@pytest.mark.parametrize("where", ["runtime", "collection"])
@pytest.mark.parametrize("length", [16384, 16385])
def test_long_node_support_does_not_expand_skip_reason_limit(example, where, length):
    assert transport.MAX_REASON == transport.MAX_STRING == 16384
    reason = "x" * length
    if where == "runtime":
        example.policy["runtime_skips"][next(iter(example.policy["runtime_skips"]))] = reason
    else:
        example.policy["collection_skip_reasons"][SKIPPED_MODULE] = reason
    if length == 16384:
        projected = transport.project_policy(example.policy, example.manifest)
        assert reason not in json.dumps(projected)
    else:
        with pytest.raises(ValueError):
            transport.project_policy(example.policy, example.manifest)


@pytest.mark.parametrize("command", ["plan", "run"])
def test_invalid_manifest_clears_earlier_public_success_before_returning_stop(
    monkeypatch, tmp_path, capsys, example, command
):
    args, policy_path = cli_args(tmp_path, example, command, "shard-0" if command == "run" else None)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    public = tmp_path / "public"
    if command == "plan":
        ci.write(public / "plan.json", approved_plan(example))
        for phase, value in example.collections.items():
            ci.write(public / f"collection-{phase}.json", expected_collection(value, example.manifest))
    else:
        ci.write(
            public / "receipt-shard-0.json",
            expected_collection(execution(example.plan, "shard-0"), example.manifest),
        )
    (tmp_path / "inputs/manifest.json").write_text(CANARY)
    assert transport.main(args) != 0
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(public))
    assert not list(public.glob("*.json"))


def test_synthetic_34000_node_transport_has_real_success_and_measured_overhead(
    tmp_path, record_property
):
    # This measures offline Python projection/JSON/validation only. It is not a
    # native runner benchmark, service readiness, or full-suite execution claim.
    # Shared-executor load can affect wall time. Record CPU separately and do
    # not infer native CI performance improvements or change phase timeouts.
    example = evidence(cases_per_module=5666)
    assert len(example.plan["full_nodes"]) == 34000
    baseline_directory = tmp_path / "baseline"
    baseline_started = time.perf_counter()
    baseline_cpu_started = time.process_time()
    baseline_plan = ci.build_plan(example.collections, SHA, example.policy)
    ci.write(baseline_directory / "plan.json", baseline_plan)
    for phase, value in example.collections.items():
        ci.write(baseline_directory / f"collection-{phase}.json", value)
    for phase in ci.PHASES:
        receipt = execution(baseline_plan, phase)
        ci.validate_execution(receipt, phase, baseline_plan, example.policy)
        ci.write(baseline_directory / f"receipt-{phase}.json", receipt)
    ci.aggregate(baseline_plan, baseline_directory, SHA, example.policy, jobs())
    baseline_elapsed = time.perf_counter() - baseline_started
    baseline_cpu = time.process_time() - baseline_cpu_started
    baseline_size = sum(path.stat().st_size for path in baseline_directory.rglob("*.json"))

    public = tmp_path / "public"
    started = time.perf_counter()
    cpu_started = time.process_time()
    policy = transport.project_policy(example.policy, example.manifest)
    collections = {
        phase: transport.project_receipt(value, example.manifest, example.policy)
        for phase, value in example.collections.items()
    }
    plan = ci.build_plan(collections, SHA, policy)
    ci.write(public / "plan.json", plan)
    for phase, value in collections.items():
        ci.write(public / f"collection-{phase}.json", value)
    for phase in ci.PHASES:
        receipt = transport.project_receipt(
            execution(example.plan, phase), example.manifest, example.policy
        )
        ci.validate_execution(receipt, phase, plan, policy)
        ci.write(public / f"receipt-{phase}.json", receipt)
    transport.verify(plan, public, SHA, example.policy, example.manifest, jobs())
    elapsed = time.perf_counter() - started
    cpu_elapsed = time.process_time() - cpu_started
    size = sum(path.stat().st_size for path in public.rglob("*.json"))
    record_property("offline_synthetic_nodes", 34000)
    record_property("offline_synthetic_original_seconds", round(baseline_elapsed, 3))
    record_property("offline_synthetic_original_cpu_seconds", round(baseline_cpu, 3))
    record_property("offline_synthetic_original_bytes", baseline_size)
    record_property("offline_synthetic_transport_seconds", round(elapsed, 3))
    record_property("offline_synthetic_transport_cpu_seconds", round(cpu_elapsed, 3))
    record_property("offline_synthetic_extra_seconds", round(elapsed - baseline_elapsed, 3))
    record_property("offline_synthetic_extra_cpu_seconds", round(cpu_elapsed - baseline_cpu, 3))
    record_property("offline_synthetic_transport_bytes", size)
    assert len(plan["full_nodes"]) == 34000
    # Record actual overhead without inventing a native CI SLA for this host.
    assert size > 1_000_000


@pytest.mark.parametrize("mask", [0o022, 0o027, 0o077], ids=["022", "027", "077"])
@pytest.mark.parametrize("command", ["plan", "run"])
@pytest.mark.parametrize("outcome", ["success", "nonzero", "exception", "interrupted", "signalled"])
def test_private_raw_targets_do_not_change_original_pytest_umask(
    monkeypatch, tmp_path, capsys, example, mask, command, outcome
):
    args, policy_path = cli_args(tmp_path, example, command, "shard-0" if command == "run" else None)
    monkeypatch.setattr(ci, "POLICY", policy_path)
    private, public = tmp_path / "private", tmp_path / "public"
    calls = []

    def runner(argv, **kwargs):
        observed = os.umask(mask)
        assert observed == mask
        phase = argv[argv.index("--ci-phase") + 1]
        calls.append(phase)
        report = Path(argv[argv.index("--ci-report") + 1])
        assert private.stat().st_mode & 0o777 == 0o700
        assert report.is_file() and report.read_bytes() == b""
        assert report.stat().st_mode & 0o777 == 0o600
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in private.iterdir())
        fixture = tmp_path / ("pytest-fixture-" + phase)
        fixture.mkdir(mode=0o750)
        assert fixture.stat().st_mode & 0o777 == 0o750 & ~mask
        value = example.collections[phase] if command == "plan" else execution(example.plan, phase)
        # The real recorder and JUnit writer truncate existing targets too.
        report.write_text(json.dumps(value))
        for argument in argv:
            if argument.startswith("--junitxml="):
                junit = Path(argument.split("=", 1)[1])
                assert junit.read_bytes() == b"" and junit.stat().st_mode & 0o777 == 0o600
                junit.write_text(CANARY)
        kwargs["stdout"].write(CANARY + "\n")
        print(CANARY)
        assert report.stat().st_mode & 0o777 == 0o600
        if outcome == "exception":
            raise OSError(CANARY)
        if outcome == "interrupted":
            raise KeyboardInterrupt
        code = {"success": 0, "nonzero": 1, "signalled": -9}[outcome]
        if code and kwargs["check"]:
            raise transport.subprocess.CalledProcessError(code, argv)
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(transport.subprocess, "run", runner)
    original = os.umask(mask)
    try:
        result = transport.main(args)
        assert os.umask(mask) == mask
    finally:
        os.umask(original)
    assert calls
    assert (
        result
        == {"success": 0, "nonzero": 1, "exception": 1, "interrupted": 130, "signalled": -9}[outcome]
    )
    assert private.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in private.iterdir())
    published = public / ("plan.json" if command == "plan" else "receipt-shard-0.json")
    assert published.exists() == (outcome == "success")
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(public))


@pytest.mark.parametrize("kind", ["regular", "symlink", "fifo", "directory"])
def test_private_output_reservation_refuses_existing_or_nonregular_targets(tmp_path, kind):
    private = tmp_path / "private"
    private.mkdir()
    target = private / "receipt-shard-0.json"
    external = tmp_path / "untouched"
    external.write_text(CANARY)
    if kind == "regular":
        target.write_text(CANARY)
    elif kind == "symlink":
        target.symlink_to(external)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.mkdir()
    before = target.lstat()
    with pytest.raises(OSError):
        transport._prepare_private_outputs(private, [target.name])
    after = target.lstat()
    assert (before.st_dev, before.st_ino, before.st_mode) == (
        after.st_dev,
        after.st_ino,
        after.st_mode,
    )
    assert external.read_text() == CANARY
    if kind == "regular":
        assert target.read_text() == CANARY
    assert private.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("mask", [0o022, 0o027, 0o077], ids=["022", "027", "077"])
@pytest.mark.parametrize("outcome", ["success", "exception", "kill"])
def test_actual_child_keeps_umask_and_precreated_raw_permissions(tmp_path, capsys, mask, outcome):
    import sys

    private = tmp_path / "private"
    names = ["receipt-shard-0.json", "shard-0.xml"]
    fixture = tmp_path / "pytest-created-directory"
    script = """
import os, pathlib, signal, sys
root, fixture = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
mask, outcome = int(sys.argv[3]), sys.argv[4]
observed = os.umask(mask)
assert observed == mask
fixture.mkdir(mode=0o750)
assert fixture.stat().st_mode & 0o777 == 0o750 & ~mask
assert root.stat().st_mode & 0o777 == 0o700
for name in ('receipt-shard-0.json', 'shard-0.xml'):
    path = root / name
    assert path.read_bytes() == b'' and path.stat().st_mode & 0o777 == 0o600
    path.write_text(sys.argv[5])
    assert path.stat().st_mode & 0o777 == 0o600
print(sys.argv[5], flush=True)
if outcome == 'exception':
    raise RuntimeError(sys.argv[5])
if outcome == 'kill':
    os.kill(os.getpid(), signal.SIGKILL)
"""
    original = os.umask(mask)
    try:
        transport._prepare_private_outputs(private, names)
        with transport._log(private, "shard-0.log") as stream:
            result = transport.subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    script,
                    str(private),
                    str(fixture),
                    str(mask),
                    outcome,
                    CANARY,
                ],
                stdout=stream,
                stderr=transport.subprocess.STDOUT,
                timeout=5,
                check=False,
            )
        assert os.umask(mask) == mask
    finally:
        os.umask(original)
    assert result.returncode == {"success": 0, "exception": 1, "kill": -9}[outcome]
    assert private.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in private.iterdir())
    assert all((private / name).read_text() == CANARY for name in names)
    assert CANARY in (private / "shard-0.log").read_text()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("target", ["directory", "file"])
def test_raw_output_permission_failure_stops_before_pytest(
    monkeypatch, tmp_path, capsys, example, target
):
    import stat

    args, policy_path = cli_args(tmp_path, example, "run", "shard-0")
    monkeypatch.setattr(ci, "POLICY", policy_path)
    chmod = os.fchmod
    calls = []

    def fault(fd, mode):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if is_directory == (target == "directory"):
            raise OSError(CANARY)
        return chmod(fd, mode)

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        pytest.fail("Permission setup must succeed before pytest can start")

    monkeypatch.setattr(os, "fchmod", fault)
    monkeypatch.setattr(transport.subprocess, "run", forbidden)
    assert transport.main(args) == 1
    assert not calls
    public = tmp_path / "public"
    assert not (public / "receipt-shard-0.json").exists()
    private = tmp_path / "private"
    assert private.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in private.iterdir())
    assert all(path.read_bytes() == b"" for path in private.iterdir())
    captured = capsys.readouterr()
    assert_private(captured.out + captured.err + public_text(public))


def test_a_report_carrying_the_recorder_failure_kind_key_validates(monkeypatch, tmp_path, example):
    """The recorder always writes failure_kind, even when the label is None.

    Without REPORT_OPTIONAL_KEYS in the receipt validator, every real run would
    fail to publish -- and no other test feeds a recorder-shaped row in here.
    """
    receipt = execution(example.plan, "shard-0")
    for row in receipt["reports"]:
        row["failure_kind"] = None
    fake_execution(monkeypatch, receipt)
    public = tmp_path / "public"
    transport.run_phase(
        "shard-0",
        approved_plan(example),
        tmp_path / "private",
        public,
        example.policy,
        example.manifest,
    )
    result = ci.read(public / "receipt-shard-0.json")
    assert result["reports"]
    # The public projection is a fixed key list; the label must not ride along.
    assert all("failure_kind" not in row for row in result["reports"])


def test_failed_real_producer_replays_without_credentials_or_success_receipt(
    monkeypatch, tmp_path, example, capsys
):
    from scripts import ci_publish_failure_log as replay

    test = tmp_path / "test_race.py"
    test.write_text(
        'import os\ndef test_race():\n    assert "ACTIONS_RUNTIME_TOKEN" not in os.environ\n    raise OSError(5, "race pid=731995 fd=14")\n'
    )
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "live-secret-canary")
    monkeypatch.setattr(
        ci, "command_for_pytest", lambda *_: [sys.executable, "-m", "pytest", "-q", str(test)]
    )
    private, public = tmp_path / "private", tmp_path / "public"
    with pytest.raises(SystemExit) as error:
        transport.run_phase(
            "local", approved_plan(example), private, public, example.policy, example.manifest
        )
    assert error.value.code == 1
    assert not (public / "receipt-local.json").exists()
    assert "race pid=731995 fd=14" not in capsys.readouterr().out
    replay.publish(private, "local")
    output = capsys.readouterr().out
    assert "OSError: [Errno 5] race pid=731995 fd=14" in output
    assert "live-secret-canary" not in output
    assert not (public / "receipt-local.json").exists()

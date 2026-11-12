"""Mock-only checks for the stock runner; never run bwrap or alter host settings."""

import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
import yaml


from scripts import ci_stock_runner as probe
from scripts import ci_test_shards as ci

ROOT = Path(__file__).resolve().parents[1]


def child_observation():
    return {
        "cache_tag": "cpython-314",
        "profile": "bwrap//&unpriv_bwrap (enforce)",
        "status": {"CapEff": "0" * 16, "CapPrm": "0" * 16, "CapAmb": "0" * 16, "NoNewPrivs": "1"},
        "namespaces": {name: name + ":[2]" for name in probe.NAMESPACES},
    }


def parent_namespaces():
    return {name: name + ":[1]" for name in probe.NAMESPACES}


def test_request_requires_event_binding_and_the_same_full_commit():
    sha = "a" * 40
    probe.verify_request(sha, sha, "workflow_dispatch", sha, ref="refs/heads/pilot")
    probe.verify_request("", sha, "push", sha, ref="refs/heads/main")
    probe.verify_request("", sha, "pull_request", sha, ref="refs/pull/1/merge", base_ref="main")
    for expected, actual, event, checkout, ref, base in (
        ("", sha, "workflow_dispatch", sha, "refs/heads/pilot", ""),
        ("a" * 7, sha, "workflow_dispatch", sha, "refs/heads/pilot", ""),
        (sha, "b" * 40, "workflow_dispatch", sha, "refs/heads/pilot", ""),
        (sha, sha, "pull_request", sha, "refs/pull/1/merge", "main"),
        (sha, sha, "workflow_dispatch", "b" * 40, "refs/heads/pilot", ""),
        ("", sha, "push", sha, "refs/heads/topic", ""),
        ("", sha, "pull_request", sha, "refs/pull/1/head", "main"),
        ("", sha, "pull_request", sha, "refs/pull/1/merge", "topic"),
        ("", "0" * 40, "push", "0" * 40, "refs/heads/main", ""),
    ):
        with pytest.raises(ValueError):
            probe.verify_request(expected, actual, event, checkout, ref=ref, base_ref=base)


def test_runtime_requires_the_exact_new_pair():
    probe.verify_runtime({"cache_tag": "cpython-314", "pytest": "9.2.0"})
    for info in (
        {"cache_tag": "cpython-312", "pytest": "9.2.0"},
        {"cache_tag": "cpython-314", "pytest": "9.1.0"},
        {"cache_tag": "cpython-314", "pytest": "9.1.1"},
        {"cache_tag": "cpython-314", "pytest": "9.2.0", "extra": True},
    ):
        with pytest.raises(ValueError):
            probe.verify_runtime(info)


def test_child_requires_enforcement_no_capabilities_and_distinct_namespaces():
    probe.verify_child(child_observation(), parent_namespaces())
    for profile in ("unconfined", "bwrap (enforce)", "bwrap//&unpriv_bwrap (complain)"):
        value = child_observation()
        value["profile"] = profile
        with pytest.raises(ValueError):
            probe.verify_child(value, parent_namespaces())
    for field in ("CapEff", "CapPrm", "CapAmb", "NoNewPrivs"):
        value = child_observation()
        value["status"][field] = "0" if field == "NoNewPrivs" else "0000000000000001"
        with pytest.raises(ValueError):
            probe.verify_child(value, parent_namespaces())
    for namespace in probe.NAMESPACES:
        value = child_observation()
        value["namespaces"][namespace] = parent_namespaces()[namespace]
        with pytest.raises(ValueError):
            probe.verify_child(value, parent_namespaces())


def test_child_rejects_missing_observations_and_wrong_interpreter():
    for field in child_observation():
        value = child_observation()
        del value[field]
        with pytest.raises(ValueError):
            probe.verify_child(value, parent_namespaces())
    value = child_observation()
    value["cache_tag"] = "cpython-312"
    with pytest.raises(ValueError):
        probe.verify_child(value, parent_namespaces())
    value = child_observation()
    value["namespaces"]["user"] = "net:[2]"
    with pytest.raises(ValueError):
        probe.verify_child(value, parent_namespaces())


def test_prerequisite_files_must_be_root_owned_regular_and_not_setid_or_writable(monkeypatch):
    monkeypatch.setattr(probe, "read_bounded", lambda path, maximum: b"fixture bytes")
    good = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0))
    assert probe.root_file(good) == b"fixture bytes"
    for mode, uid in (
        (stat.S_IFLNK | 0o777, 0),
        (stat.S_IFDIR | 0o755, 0),
        (stat.S_IFREG | 0o775, 0),
        (stat.S_IFREG | 0o757, 0),
        (stat.S_IFREG | 0o4755, 0),
        (stat.S_IFREG | 0o2755, 0),
        (stat.S_IFREG | 0o755, 1000),
    ):
        bad = SimpleNamespace(lstat=lambda mode=mode, uid=uid: SimpleNamespace(st_mode=mode, st_uid=uid))
        with pytest.raises(ValueError):
            probe.root_file(bad)


def test_security_state_requires_enabled_policy_without_writes(monkeypatch):
    reads = {probe.ENABLED: b"Y\n", probe.RESTRICTION: b"1\n"}
    monkeypatch.setattr(probe, "read_bounded", lambda path: reads[path])
    monkeypatch.setattr(probe, "root_file", lambda path: b"fixture profile")
    assert probe.security_state()["restrict_userns"] == 1
    for path, denied in ((probe.ENABLED, b"N\n"), (probe.RESTRICTION, b"0\n")):
        original = reads[path]
        reads[path] = denied
        with pytest.raises(ValueError):
            probe.security_state()
        reads[path] = original


def mock_host(monkeypatch, states):
    values = iter(states)
    monkeypatch.setattr(probe, "security_state", lambda: next(values))
    monkeypatch.setattr(probe, "root_file", lambda path: b"fixture executable")
    monkeypatch.setattr(probe.os, "readlink", lambda path: parent_namespaces()[path.rsplit("/", 1)[1]])
    calls = []

    def execute(argv):
        calls.append(argv)
        return SimpleNamespace(stdout=json.dumps(child_observation()))

    monkeypatch.setattr(probe, "execute", execute)
    return calls


def test_host_uses_fixed_unprivileged_probe_and_retains_security(monkeypatch):
    state = {"apparmor": "enabled", "restrict_userns": 1, "profile_sha256": "a" * 64}
    calls = mock_host(monkeypatch, [state, state])
    probe.verify_host()
    assert calls == [
        [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            probe.CHILD,
        ]
    ]


def test_host_rejects_security_change_during_probe(monkeypatch):
    mock_host(monkeypatch, [{"profile_sha256": "a" * 64}, {"profile_sha256": "b" * 64}])
    with pytest.raises(ValueError, match="changed"):
        probe.verify_host()


def test_host_does_not_launch_after_prerequisite_refusal(monkeypatch):
    def unavailable():
        raise ValueError("fixture restriction unavailable")

    monkeypatch.setattr(probe, "security_state", unavailable)
    monkeypatch.setattr(probe, "execute", lambda argv: pytest.fail("unexpected payload launch"))
    with pytest.raises(ValueError, match="unavailable"):
        probe.verify_host()


def test_both_outer_and_system_interpreters_are_measured(monkeypatch):
    calls = []

    def execute(argv):
        calls.append(argv[0])
        return SimpleNamespace(stdout=json.dumps({"cache_tag": "cpython-314", "pytest": "9.2.0"}))

    monkeypatch.setattr(probe, "execute", execute)
    probe.verify_interpreters()
    assert calls == [probe.sys.executable, "/usr/bin/python3"]


@pytest.mark.parametrize("fault", [None, "skip", "xfail", "missing", "wrong-sha", "extra-node"])
def test_system_qualification_uses_real_interpreter_and_rejects_incomplete_receipts(
    monkeypatch, tmp_path, fault
):
    sha = "a" * 40
    nodes = [
        f"tests/test_gate_pytest_evidence.py::test_case[cpython-314-pytest9.2.0-{n}]" for n in range(27)
    ]
    original_read = ci.read
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.setenv("FORGE_QUALIFICATION_PYTHON", "/fixture/other/python")
    monkeypatch.setattr(
        ci,
        "read",
        lambda path: (
            {"nodes": nodes}
            if Path(path).name == "ci_public_test_identities.json"
            else original_read(path)
        ),
    )
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[0] == "/usr/bin/python3"
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin"
        assert "FORGE_QUALIFICATION_PYTHON" not in kwargs["env"]
        report = Path(argv[argv.index("--ci-report") + 1])
        receipt = {
            "sha": sha,
            "phase": "system-runtime",
            "exitstatus": 0,
            "collected": nodes,
            "collection_skips": [],
            "collection_skip_reasons": {},
            "collection_errors": [],
            "reports": [],
            "subtests": [],
        }
        if "--collect-only" not in argv:
            receipt["reports"] = [
                {"nodeid": node, "when": when, "outcome": "passed", "wasxfail": False}
                for node in nodes
                for when in ("setup", "call", "teardown")
            ]
            if fault == "skip":
                receipt["reports"][1]["outcome"] = "skipped"
            elif fault == "xfail":
                receipt["reports"][1]["wasxfail"] = True
            elif fault == "missing":
                receipt["reports"].pop()
            elif fault == "wrong-sha":
                receipt["sha"] = "b" * 40
            elif fault == "extra-node":
                receipt["collected"] = [*nodes, "tests/test_extra.py::test_unreviewed"]
        ci.write(report, receipt)

    monkeypatch.setattr(probe.subprocess, "run", run)
    if fault:
        with pytest.raises(ValueError):
            probe.verify_system_runtime(tmp_path / "private")
    else:
        probe.verify_system_runtime(tmp_path / "private")
    assert len(calls) == 2


def test_formal_workflow_keeps_stock_runtime_host_and_complete_coverage():
    workflow = yaml.safe_load((ROOT / ".github/workflows/linux-tests.yml").read_text())
    jobs = workflow["jobs"]
    assert set(jobs) == {"plan", "process-contracts", "service-free", "linux-tests"}
    assert jobs["linux-tests"]["name"] == "linux-tests"
    assert jobs["linux-tests"]["if"] == "${{ always() }}"
    assert jobs["service-free"]["strategy"] == {"fail-fast": False, "matrix": {"shard": [0, 1, 2, 3]}}
    for name, job in jobs.items():
        assert job["runs-on"] == "ubuntu-26.04"
        if name == "linux-tests":
            continue
        steps = {s.get("id", s["name"]): s for s in job["steps"]}
        assert steps["Set up qualified Python"]["with"]["python-version"] == "3.12\n3.14"
        assert "sys.version_info[:2] == (3, 14)" in steps["preflight"]["run"]
        assert (
            "setup_start stock-security\npython scripts/ci_stock_runner.py host" in steps["bwrap"]["run"]
        )
        assert "python3.12 -m pip install 'pytest==9.2.0'" in steps["install"]["run"]
        assert 'python3.12 -m venv "$RUNNER_TEMP/forge-pytest-9.1.1"' in steps["install"]["run"]
        assert steps["install"]["run"].endswith("\npython scripts/ci_stock_runner.py interpreters\n")
    assert "Qualify the actual system Python build" in {
        s["name"] for s in jobs["process-contracts"]["steps"]
    }
    assert jobs["linux-tests"]["needs"] == ["plan", "process-contracts", "service-free"]


def test_candidate_has_no_security_relaxation_or_skip_substitution():
    text = (ROOT / ".github/workflows/linux-tests.yml").read_text()
    for forbidden in (
        "sysctl",
        "apparmor_parser",
        "aa-disable",
        "aa-complain",
        "--privileged",
        "--cap-add",
        "--security-opt",
        "sudo bwrap",
        "continue-on-error",
        "pytest.skip",
    ):
        assert forbidden not in text
    assert "scripts/ci_private_transport.py verify" in text
    assert "scripts/ci_private_transport.py plan" in text

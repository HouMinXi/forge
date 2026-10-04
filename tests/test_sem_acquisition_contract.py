"""Semantic acquisition identity through real subprocess.run and advisory cache."""

import json
import subprocess
from pathlib import Path

import pytest

from code_forge import graph_triage as gt
from code_forge.context_sources import GraphTriageSource
from code_forge.diff_grouping import group_diff
from code_forge.gate_check import load_gate_config as actual_gate_loader


DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n def measured():\n-    return 0\n+    return 1\n"
ENTITY = {
    "filePath": "a.py",
    "changeType": "modified",
    "entityName": "measured",
    "entityType": "function",
    "startLine": 1,
    "endLine": 2,
}


def _record_l2_root(monkeypatch, cli, root):
    calls = []

    def build(*, cwd):
        assert cwd == root
        calls.append(cwd)
        return lambda *args: ([], [])

    monkeypatch.setattr(cli, "build_l2_runner", build)
    return calls


@pytest.fixture
def sem_controls(tmp_path, monkeypatch):
    """Replace only Popen, leaving actual run/stdin/timeout interpretation active."""
    (tmp_path / "a.py").write_text("def measured():\n    return 1\n")
    temporary = tmp_path / "sem-temp"
    temporary.mkdir()
    monkeypatch.setattr(gt.tempfile, "tempdir", str(temporary))
    real_which = gt.shutil.which
    monkeypatch.setattr(
        gt.shutil,
        "which",
        lambda command: "/controlled/sem" if command == "sem" else real_which(command),
    )
    monkeypatch.setattr("code_forge.gate_check.load_gate_config", lambda _: {})
    controls = {
        "version": (0, "sem 0.21.0"),
        "diff": (0, '{"changes":[]}'),
        "impact": (0, '{"impact":{"total":3},"dependents":[]}'),
        "calls": [],
        "root": tmp_path,
        "temporary": temporary,
    }
    real_popen = subprocess.Popen

    class ControlledPopen:
        def __init__(self, command, **kwargs):
            if command == ["sem", "--version"]:
                stage = "version"
            elif command == ["sem", "diff", "--patch", "--format", "json"]:
                stage = "diff"
            elif command == ["sem", "impact", "measured", "--file", "a.py", "--json"]:
                stage = "impact"
            else:
                raise AssertionError("unexpected semantic command")
            self.args = command
            self.packet = controls[stage]
            self.call = {
                "command": command,
                "stage": stage,
                "cwd": kwargs.get("cwd"),
                "timeouts": [],
                "killed": False,
                "waited": False,
            }
            if kwargs.get("stdin") is not None:
                self.call["stdin"] = kwargs["stdin"].read()
                assert self.call["stdin"] == DIFF
            controls["calls"].append(self.call)
            if self.packet[0] == "missing":
                raise FileNotFoundError("controlled missing executable")
            self.returncode = self.packet[0] if type(self.packet[0]) is int else None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.wait()

        def communicate(self, input=None, timeout=None):
            self.call["timeouts"].append(timeout)
            if self.packet[0] == "timeout":
                raise subprocess.TimeoutExpired(self.args, timeout)
            return self.packet[1], "controlled stderr" if self.returncode else ""

        def kill(self):
            self.call["killed"] = True
            self.returncode = -9

        def wait(self, timeout=None):
            self.call["waited"] = True
            return self.returncode

        def poll(self):
            return self.returncode

    def popen(command, *args, **kwargs):
        if command and command[0] == "sem":
            assert not args and not kwargs.get("shell")
            return ControlledPopen(command, **kwargs)
        assert (
            isinstance(command, list)
            and command[0] == "git"
            and command[1]
            in {
                "rev-parse",
                "status",
                "diff",
                "ls-files",
                "show",
                "log",
                "symbolic-ref",
                "for-each-ref",
                "config",
                "worktree",
            }
        ), "unexpected real process"
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    yield controls
    assert not list(temporary.iterdir())


@pytest.mark.parametrize(
    "packet,status",
    [
        ((7, "diagnostic"), "execution_error"),
        (("missing", ""), "execution_error"),
        (("timeout", ""), "timeout"),
        ((0, "not JSON"), "parse_error"),
        ((0, '{"changes":['), "parse_error"),
        ((0, "[]"), "schema_error"),
        ((0, "null"), "schema_error"),
        ((0, "{}"), "schema_error"),
        ((0, '{"changes":null}'), "schema_error"),
        ((0, '{"changes":{}}'), "schema_error"),
        ((0, '{"changes":[42]}'), "schema_error"),
    ],
)
def test_failed_diff_is_not_completed_empty(sem_controls, packet, status):
    sem_controls["diff"] = packet
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == status and not result.completed and result.entities == []
    assert result.diagnostic and result.capability.status == "modern"
    assert [call["stage"] for call in sem_controls["calls"]] == ["version", "diff"]
    for call in sem_controls["calls"]:
        assert call["cwd"] == str(sem_controls["root"])
    version, diff = sem_controls["calls"]
    assert version["timeouts"] == [2]
    if packet[0] == "missing":
        assert diff["timeouts"] == []
    else:
        assert diff["timeouts"] == [gt._SEM_DIFF_TIMEOUT_S]
    if packet[0] == "timeout":
        assert diff["killed"] and diff["waited"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("filePath", ""),
        ("filePath", 1),
        ("entityName", 1),
        ("entityName", None),
        ("entityType", []),
        ("entityType", None),
        ("changeType", {}),
        ("changeType", None),
        ("startLine", True),
        ("startLine", -1),
        ("endLine", "2"),
    ],
)
def test_entity_fields_are_validated_before_grouping(sem_controls, key, value):
    entity = {**ENTITY, key: value}
    sem_controls["diff"] = (0, json.dumps({"changes": [entity]}))
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == "schema_error" and result.entities == []


def test_valid_empty_is_completed_and_cached(sem_controls):
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == "completed_empty" and result.completed
    assert group_diff(result.entities, sem_controls["root"]).groups == []
    source = GraphTriageSource(sem_controls["root"])
    assert source.facts(["a.py"], DIFF) == [] and source.findings_cache == []
    replay = gt.GraphTriageRunner()
    replay._cached_findings = source.findings_cache
    count = len(sem_controls["calls"])
    assert replay.run(DIFF, sem_controls["root"]) == []
    assert len(sem_controls["calls"]) == count and not replay.infra_errors


def test_valid_entities_preserve_grouping_and_complete_impact(sem_controls):
    sem_controls["diff"] = (0, json.dumps({"changes": [ENTITY]}))
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == "completed" and result.entities == [ENTITY]
    assert group_diff(result.entities, sem_controls["root"]).groups[0].passes == 3
    runner = gt.GraphTriageRunner()
    findings = runner.run(DIFF, sem_controls["root"])
    assert len(findings) == 1 and "3 downstream" in findings[0].description
    assert runner.acquisition_outcome.impact_complete is True and runner.is_advisory
    count = len(sem_controls["calls"])
    assert runner.run(DIFF, sem_controls["root"]) == findings
    assert len(sem_controls["calls"]) == count


@pytest.mark.parametrize(
    "packet,status",
    [
        ((0, "sem 0.20.0"), "legacy"),
        ((2, "failed"), "version_error"),
        (("missing", ""), "version_error"),
        (("timeout", ""), "version_timeout"),
        ((0, "unexpected"), "version_invalid"),
        ((0, "sem nope.nope"), "version_invalid"),
    ],
)
def test_capability_failure_keeps_version_facts(sem_controls, packet, status):
    sem_controls["version"] = packet
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == "inapplicable" and not result.completed
    assert result.capability.available and not result.capability.applicable
    assert result.capability.status == status and result.diagnostic
    assert all(call["stage"] == "version" for call in sem_controls["calls"])


def test_legacy_index_retains_applicability(sem_controls):
    sem_controls["version"] = (0, "sem 0.10.3")
    (sem_controls["root"] / ".semcode.db").mkdir()
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.completed and result.capability.applicable
    assert result.capability.version == "0.10.3" and gt._sem_has_index(sem_controls["root"])


def test_unavailable_sem_is_visible_without_spawn(sem_controls, monkeypatch):
    monkeypatch.setattr(gt.shutil, "which", lambda _: None)
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == "unavailable" and result.capability.available is False
    runner = gt.GraphTriageRunner()
    assert runner.run(DIFF, sem_controls["root"]) == [] and runner.infra_errors
    assert not sem_controls["calls"]


def test_disabled_and_empty_input_remain_quiet(sem_controls, monkeypatch):
    monkeypatch.setattr(
        "code_forge.gate_check.load_gate_config", lambda _: {"graph_triage": {"enabled": False}}
    )
    runner = gt.GraphTriageRunner()
    assert runner.run(DIFF, sem_controls["root"]) == []
    assert runner.acquisition_outcome.status == "disabled" and not runner.infra_errors
    assert runner.run(" \n", sem_controls["root"]) == []
    assert runner.acquisition_outcome.status == "completed_empty" and not runner.infra_errors
    assert not sem_controls["calls"]


@pytest.mark.parametrize(
    "packet",
    [
        (7, "failed"),
        ("timeout", ""),
        (0, "not JSON"),
        (0, "[]"),
        (0, '{"impact":{}}'),
        (0, '{"impact":{"total":true},"dependents":[]}'),
        (0, '{"impact":{"total":-1},"dependents":[]}'),
        (0, '{"impact":{"total":3},"dependents":null}'),
        (0, '{"impact":{"total":3},"dependents":[42]}'),
        ("missing", ""),
    ],
)
def test_incomplete_impact_is_not_cached_as_success(sem_controls, packet):
    sem_controls["diff"] = (0, json.dumps({"changes": [ENTITY]}))
    sem_controls["impact"] = packet
    runner = gt.GraphTriageRunner()
    assert runner.run(DIFF, sem_controls["root"]) == []
    assert runner.acquisition_outcome.status == "impact_incomplete"
    assert runner.acquisition_outcome.impact_complete is False and runner.infra_errors
    assert runner._cached_findings is None
    count = len(sem_controls["calls"])
    assert runner.run(DIFF, sem_controls["root"]) == [] and runner.infra_errors
    assert len(sem_controls["calls"]) > count


def test_adapter_failure_clears_old_success_and_replays_failure(sem_controls):
    source = GraphTriageSource(sem_controls["root"])
    assert source.facts(["a.py"], DIFF) == [] and source.findings_cache == []
    sem_controls["diff"] = (7, "failed")
    with pytest.raises(RuntimeError, match="execution_error"):
        source.facts(["a.py"], DIFF)
    assert source.findings_cache is None
    replay = gt.GraphTriageRunner()
    replay._cached_findings = source.findings_cache
    count = len(sem_controls["calls"])
    assert replay.run(DIFF, sem_controls["root"]) == [] and replay.infra_errors
    assert replay._cached_findings is None and len(sem_controls["calls"]) > count


def test_temporary_write_failure_keeps_failed_outcome_and_closes_fd(sem_controls, monkeypatch):
    seen = []

    def fail(fd, *args, **kwargs):
        seen.append(fd)
        raise OSError("controlled fdopen failure")

    monkeypatch.setattr(gt.os, "fdopen", fail)
    result = gt._run_sem(DIFF, sem_controls["root"])
    assert result.status == "execution_error" and "fdopen failure" in result.diagnostic
    for fd in seen:
        with pytest.raises(OSError):
            gt.os.fstat(fd)


def test_unknown_process_shapes_refuse_launch(sem_controls):
    with pytest.raises(AssertionError, match="unexpected semantic command"):
        subprocess.Popen(["sem", "unknown"])
    with pytest.raises(AssertionError, match="unexpected real process"):
        subprocess.Popen(["unknown-provider"])


@pytest.fixture
def cli_pipeline(sem_controls, monkeypatch):
    """Execute actual CLI acquisition/cache routing with provider dispatch refused."""
    from code_forge import cli, context_sources
    from code_forge.backend import BackendConfig
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict

    root = sem_controls["root"]
    monkeypatch.chdir(root)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(root.parent.resolve()))
    monkeypatch.setenv("FORGE_PROJECT_DIR", str(root))
    (root / "custom.yaml").write_text("tools: {}\n")
    args = cli._build_parser().parse_args(
        [
            "review",
            "--allow-main",
            "--backend",
            "test",
            "--registry",
            "custom.yaml",
            "--falsification-engine",
            "stub",
            "a.py",
        ]
    )
    backend = BackendConfig(
        name="test", type="api", format="openai", base_url="https://example.invalid", model="test"
    )
    resolved = ResolvedReview(
        source_files=[root / "a.py"], baseline_content=None, git_diff=DIFF, mode_hint="non-git"
    )
    monkeypatch.setattr(cli, "resolve_baseline", lambda *a, **kw: resolved)
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: "subprocess")
    monkeypatch.setattr("code_forge.backend.resolve_backend", lambda *a, **kw: backend)
    monkeypatch.setattr(cli, "_check_backend_credentials", lambda *a, **kw: None)
    monkeypatch.setattr("code_forge.user_config.load_user_retry", dict)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", lambda *a, **kw: 100000)
    monkeypatch.setattr(cli, "_run_test_assertion_review", lambda *a, **kw: [])
    seen = {"providers": [], "actual_hold": cli._run_hold_loop}
    actual_gather = context_sources.gather

    def gather(sources, *args, **kwargs):
        selected = [source for source in sources if isinstance(source, GraphTriageSource)]
        assert len(selected) == 1
        seen["source"] = selected[0]
        return actual_gather(selected, *args, **kwargs)

    def provider(*args, **kwargs):
        seen["providers"].append({"args": args, "kwargs": kwargs})

        def refuse():
            raise AssertionError("provider execution forbidden")

        return refuse

    def hold(**kwargs):
        seen["hold"] = kwargs
        return Verdict.PENDING

    monkeypatch.setattr(context_sources, "gather", gather)
    monkeypatch.setattr(cli, "build_l1_provider", provider)
    monkeypatch.setattr("code_forge.factories.build_grouped_l1_provider", provider)
    monkeypatch.setattr(cli, "_run_hold_loop", hold)
    return root, args, seen


def _write_owned_graph(root):
    import sqlite3
    from contextlib import closing

    db = root / ".code-review-graph" / "graph.db"
    db.parent.mkdir()
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("create table metadata (key text, value text)")
        connection.execute("insert into metadata values ('git_head_sha', ?)", ("a" * 40,))
        connection.execute(
            "create table nodes (id integer, kind text, name text, qualified_name text, "
            "file_path text, line_start integer, line_end integer)"
        )
        connection.execute(
            "insert into nodes values (1, 'Function', 'measured', 'a.py::measured', 'a.py', 1, 2)"
        )
        connection.execute(
            "create table edges (kind text, source_qualified text, target_qualified text)"
        )
        connection.commit()
    return db


@pytest.mark.parametrize(
    "head,allow,expected_count",
    [
        ("b" * 40, False, 0),
        (None, False, 0),
        ("a" * 40, False, 1),
        ("b" * 40, True, 1),
        (None, True, 1),
    ],
)
def test_actual_cli_snapshot_gate_survives_hold_replay(
    sem_controls, cli_pipeline, monkeypatch, head, allow, expected_count
):
    from code_forge import cli
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    _write_owned_graph(root)
    monkeypatch.setattr(gt.shutil, "which", lambda command: None)
    monkeypatch.delenv("CRG_DB_PATH", raising=False)
    monkeypatch.setattr(
        cli,
        "resolve_baseline",
        lambda *a, **kw: ResolvedReview(
            source_files=[root / "a.py"],
            baseline_content=None,
            git_diff=DIFF,
            mode_hint="non-git",
            head_sha=head,
        ),
    )
    args.allow_unsnapshotted_context = allow
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PENDING
    runner = gt.GraphTriageRunner()
    runner._cached_findings = seen["hold"]["pre_graph_findings"]
    replayed = runner.run(DIFF, root)
    assert len(replayed) == expected_count
    assert len(seen["hold"]["pre_graph_findings"]) == expected_count
    assert not runner.infra_errors and not sem_controls["calls"]


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize(
    "policy",
    ["graph_triage:\n  enabled: false\n", "unreadable"],
)
def test_actual_cli_policy_refusal_never_seeds_an_acquisition_retry(
    sem_controls, cli_pipeline, monkeypatch, policy, allow
):
    from code_forge import cli, gate_check
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    _write_owned_graph(root)
    gate = root / ".code-forge/gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text("graph_triage:\n  enabled: false\n" if policy == "unreadable" else policy)
    reads = []

    def load(path):
        reads.append(str(path))
        if policy == "unreadable":
            raise PermissionError("controlled policy read refusal")
        return actual_gate_loader(path)

    monkeypatch.setattr(gate_check, "load_gate_config", load)
    monkeypatch.setattr(gt.shutil, "which", lambda command: None)
    monkeypatch.delenv("CRG_DB_PATH", raising=False)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", lambda *a, **kw: 0)
    args.allow_unsnapshotted_context = allow
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PENDING
    assert seen["source"].findings_cache is None
    assert seen["hold"]["pre_graph_findings"] == []
    assert reads and not sem_controls["calls"]
    initial_reads = len(reads)
    gate.write_text("test:\n  command: [pytest]\n")
    replay = gt.GraphTriageRunner()
    replay._cached_findings = seen["hold"]["pre_graph_findings"]
    assert replay.run(DIFF, root) == [] and len(reads) == initial_reads
    assert not replay.infra_errors and not sem_controls["calls"]


def test_actual_cli_rejects_malformed_policy_before_acquisition(sem_controls, cli_pipeline):
    from code_forge import cli
    from code_forge.errors import CliError

    root, args, seen = cli_pipeline
    gate = root / ".code-forge/gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text("graph_triage: [\n")
    with pytest.raises(CliError, match="gate.yaml parse error"):
        cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert "hold" not in seen and "source" not in seen
    assert not sem_controls["calls"]


@pytest.mark.parametrize("policy", ["graph_triage: [\n", "graph_triage:\n  enabled: false\n"])
def test_graph_runner_actual_invalid_policy_stops_before_backend(sem_controls, monkeypatch, policy):
    root = sem_controls["root"]
    _write_owned_graph(root)
    gate = root / ".code-forge/gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text(policy)
    monkeypatch.setattr("code_forge.gate_check.load_gate_config", actual_gate_loader)

    def detect(*args, **kwargs):
        raise AssertionError("invalid existing policy must refuse discovery")

    monkeypatch.setattr(gt, "_detect_backend", detect)
    runner = gt.GraphTriageRunner()
    assert runner.run(DIFF, root) == []
    assert runner.acquisition_outcome.status == "configuration_error"
    assert runner.infra_errors and not sem_controls["calls"]


@pytest.mark.parametrize(
    "failure", [ValueError("controlled bad policy"), PermissionError("controlled unreadable policy")]
)
def test_graph_runner_policy_failure_refuses_backend_autodetection(
    sem_controls, monkeypatch, capsys, failure
):
    def load(path):
        raise failure

    def detect(*args, **kwargs):
        raise AssertionError("policy refusal must precede backend discovery")

    monkeypatch.setattr("code_forge.gate_check.load_gate_config", load)
    monkeypatch.setattr(gt, "_detect_backend", detect)
    runner = gt.GraphTriageRunner()
    assert runner.run(DIFF, sem_controls["root"]) == []
    assert runner.acquisition_outcome.status == "configuration_error"
    assert not runner.acquisition_outcome.completed
    assert runner.infra_errors and str(failure) in runner.infra_errors[0]
    assert capsys.readouterr().err == runner.infra_errors[0] + "\n"
    assert not sem_controls["calls"]


def test_gather_preserves_policy_refusal_origin_and_healthy_neighbor():
    from code_forge.context_sources import FactRow, gather

    class Refused:
        name = "refused"

        def snapshot_sha(self):
            raise ValueError("controlled invalid policy")

        def facts(self, changed_files, diff_text):
            raise AssertionError("refused source must not acquire facts")

    class Healthy:
        name = "healthy"

        def snapshot_sha(self):
            return None

        def facts(self, changed_files, diff_text):
            return [FactRow("measured", "a.py", "0", "", self.name)]

    result = gather([Refused(), Healthy()], ["a.py"], DIFF, None)
    assert result.refused_sources == ["refused"]
    assert result.skipped_sources == []
    assert result.errors == ["refused: ValueError: controlled invalid policy"]
    assert [row.source for row in result.rows] == ["healthy"]


def test_gather_graph_failure_keeps_healthy_neighbor(sem_controls):
    from code_forge.context_sources import FactRow, gather

    class Neighbor:
        name = "neighbor"

        def snapshot_sha(self):
            return None

        def facts(self, changed_files, diff_text):
            return [FactRow("healthy", "a.py", "0", "", self.name)]

    sem_controls["diff"] = (7, "failed")
    source = GraphTriageSource(sem_controls["root"])
    notices = []
    result = gather(
        [source, Neighbor()],
        ["a.py"],
        DIFF,
        head_sha=None,
        on_error=lambda name, message: notices.append((name, message)),
    )
    assert [row.entity for row in result.rows] == ["healthy"]
    assert len(result.errors) == 1 and result.errors[0].startswith("graph_triage: RuntimeError:")
    assert notices == [("graph_triage", result.errors[0])]
    assert source.findings_cache is None


@pytest.mark.parametrize(
    "packet,cache,reason",
    [
        ((7, "failed"), None, "execution_error"),
        ((0, '{"changes":[]}'), [], "sem returned no entities"),
    ],
)
def test_actual_cli_distinguishes_failed_grouping_and_cache(
    sem_controls, cli_pipeline, packet, cache, reason, capsys
):
    from code_forge import cli
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    sem_controls["diff"] = packet
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PENDING
    assert seen["source"].findings_cache == cache
    assert seen["hold"]["pre_graph_findings"] == cache
    grouping_warnings = [
        line for line in capsys.readouterr().err.splitlines() if "grouping: estimated" in line
    ]
    assert seen["providers"] and len(grouping_warnings) == 1
    assert reason in grouping_warnings[0]
    with pytest.raises(AssertionError, match="provider execution forbidden"):
        seen["hold"]["l1_provider"]()
    assert all(call["cwd"] == str(root) for call in sem_controls["calls"])


def test_actual_cli_uses_valid_entities_for_grouping(sem_controls, cli_pipeline):
    from code_forge import cli
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    sem_controls["diff"] = (0, json.dumps({"changes": [ENTITY]}))
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PENDING
    assert len(seen["hold"]["pre_graph_findings"]) == 1
    assert any(call["stage"] == "impact" for call in sem_controls["calls"])
    assert len(seen["providers"]) == 1
    specs = seen["providers"][0]["args"][1]
    assert isinstance(specs, list) and len(specs) == 1
    assert specs[0]["resolved"].git_diff == DIFF
    assert specs[0]["resolved"].source_files == [Path("a.py")]


@pytest.mark.parametrize("cache", [None, []])
def test_actual_hold_loop_seed_preserves_acquisition_failure(sem_controls, monkeypatch, cache):
    from code_forge import cli
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Mode, Verdict

    root = sem_controls["root"]
    sem_controls["diff"] = (7, "failed")
    seen = {}

    def run(machine):
        runner = next(
            item for item in machine.advisory_runners if isinstance(item, gt.GraphTriageRunner)
        )
        assert runner._cached_findings is cache
        assert runner.run(DIFF, root) == []
        seen["errors"] = list(runner.infra_errors)
        seen["outcome"] = runner.acquisition_outcome
        return Verdict.PASS

    monkeypatch.setattr(cli.StateMachine, "run", run)
    verdict = cli._run_hold_loop(
        mode=Mode.LOCAL,
        falsifier=None,
        autofixer=None,
        revert_fn=None,
        l1_provider=None,
        resolved=ResolvedReview(
            source_files=[root / "a.py"], baseline_content=None, git_diff=DIFF, mode_hint="non-git"
        ),
        source_hash="controlled",
        baseline_repr="file:a.py",
        cwd=root,
        registry={},
        max_rounds=1,
        max_fix_attempts=1,
        state_path=root / ".code-forge/state.json",
        pre_graph_findings=cache,
    )
    assert verdict == Verdict.PASS
    if cache is None:
        assert seen["errors"] and seen["outcome"].status == "execution_error"
        assert any(call["stage"] == "diff" for call in sem_controls["calls"])
    else:
        assert not seen["errors"] and not sem_controls["calls"]


def test_configuration_error_is_a_declared_acquisition_variant(sem_controls, monkeypatch):
    from typing import get_args, get_type_hints

    def refuse(path):
        raise ValueError("controlled invalid policy")

    monkeypatch.setattr("code_forge.gate_check.load_gate_config", refuse)
    runner = gt.GraphTriageRunner()
    assert runner.run(DIFF, sem_controls["root"]) == []
    outcome = runner.acquisition_outcome
    assert outcome is not None and not outcome.completed
    assert outcome.status == "configuration_error"
    assert outcome.status in get_args(get_type_hints(gt.SemAcquisition)["status"])


@pytest.mark.parametrize("head", ["a" * 40, "b" * 40])
def test_source_retains_backend_from_snapshot_through_acquisition(sem_controls, monkeypatch, head):
    from code_forge.context_sources import gather

    root = sem_controls["root"]
    _write_owned_graph(root)
    actual_detect = gt._detect_backend
    detected = []

    def detect(*args):
        backend = actual_detect(*args)
        detected.append(backend)
        sem_controls["version"] = (2, "controlled capability loss")
        return backend

    monkeypatch.setattr(gt, "_detect_backend", detect)
    source = GraphTriageSource(root)
    first = gather([source], ["a.py"], DIFF, head_sha=head)
    assert first.rows == []
    assert len(first.errors) == 1 and "inapplicable" in first.errors[0]
    assert detected == [("sem", "/controlled/sem")]
    assert source.findings_cache is None
    second = gather([GraphTriageSource(root)], ["a.py"], DIFF, head_sha=head)
    assert [row.entity for row in second.rows] == (["measured"] if head == "a" * 40 else [])
    assert second.skipped_sources == ([] if head == "a" * 40 else ["graph_triage"])


@pytest.mark.parametrize(
    "retry,head,allow,expected",
    [
        ("sql", "b" * 40, False, 0),
        ("sql", None, False, 0),
        ("sql", "a" * 40, False, 1),
        ("sql", "b" * 40, True, 1),
        ("sql", None, True, 1),
        ("sem", "b" * 40, False, 1),
        ("sem", None, False, 1),
    ],
)
@pytest.mark.parametrize("mode", ["ci", "local"])
def test_actual_cli_uncached_hold_retry_preserves_source_authority(
    sem_controls, cli_pipeline, monkeypatch, retry, head, allow, expected, mode, capsys
):
    """Actual CLI/hold/StateMachine advisory dispatch; core gates remain substituted."""
    from code_forge import cli
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    _write_owned_graph(root)
    sem_controls["diff"] = (7, "controlled initial acquisition failure")
    monkeypatch.setattr(
        cli,
        "resolve_baseline",
        lambda *a, **kw: ResolvedReview(
            source_files=[root / "a.py"],
            baseline_content=None,
            git_diff=DIFF,
            mode_hint="non-git",
            head_sha=head,
        ),
    )
    args.allow_unsnapshotted_context = allow
    args.mode = mode
    snapshots = []

    def advisory_boundary(machine):
        machine.advisory_runners = [
            item for item in machine.advisory_runners if isinstance(item, gt.GraphTriageRunner)
        ]
        assert len(machine.advisory_runners) == 1
        machine._run_advisory_axes()
        snapshots.append(([item.description for item in machine._advisories], machine._state))
        return Verdict.PASS

    def hold(**kwargs):
        assert kwargs["pre_graph_findings"] is None
        seen["initial_source"] = seen["source"]
        assert seen["initial_source"].findings_cache is None
        if retry == "sql":
            sem_controls["version"] = (2, "controlled retry capability loss")
        else:
            sem_controls["diff"] = (0, json.dumps({"changes": [ENTITY]}))
        return seen["actual_hold"](**kwargs)

    monkeypatch.setattr(cli, "_run_hold_loop", hold)
    monkeypatch.setattr(cli.StateMachine, "run", advisory_boundary)
    l2_roots = _record_l2_root(monkeypatch, cli, root)
    monkeypatch.setattr(cli, "build_e2e_checker", lambda: lambda *a: ([], []))
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PASS
    assert l2_roots == [root]
    assert len(snapshots) == 1 and len(snapshots[0][0]) == expected
    if retry == "sql" and expected == 0:
        assert "GraphTriageRunner: context source skipped: graph_triage:" in capsys.readouterr().err
    assert not snapshots[0][1].infra_errors
    assert seen["initial_source"].findings_cache is None
    assert all(call["cwd"] == str(root) for call in sem_controls["calls"])


def test_source_selected_disable_stays_quiet(sem_controls, monkeypatch):
    from code_forge.context_sources import gather

    root = sem_controls["root"]
    _write_owned_graph(root)
    gate = root / ".code-forge/gate.yaml"
    gate.parent.mkdir()
    gate.write_text("test:\n  command: [pytest]\ngraph_triage:\n  enabled: false\n")
    monkeypatch.setattr("code_forge.gate_check.load_gate_config", actual_gate_loader)
    source = GraphTriageSource(root)
    result = gather([source], ["a.py"], DIFF, head_sha=None)
    assert not result.errors and not result.rows
    assert source.findings_cache == []
    assert source.acquisition_outcome is not None
    assert source.acquisition_outcome.status == "disabled"
    assert not sem_controls["calls"]


def test_source_advisory_failure_can_recover_and_cache(sem_controls):
    runner = GraphTriageSource(sem_controls["root"]).advisory_runner(None)
    sem_controls["diff"] = (7, "controlled initial failure")
    assert runner.run(DIFF, sem_controls["root"]) == []
    assert runner.infra_errors and runner._cached_findings is None
    assert runner.acquisition_outcome is not None and not runner.acquisition_outcome.completed
    sem_controls["diff"] = (0, json.dumps({"changes": [ENTITY]}))
    assert len(runner.run(DIFF, sem_controls["root"])) == 1
    assert not runner.infra_errors
    assert runner.acquisition_outcome is not None and runner.acquisition_outcome.completed
    calls = len(sem_controls["calls"])
    assert runner.run(DIFF, sem_controls["root"]) == runner._cached_findings
    assert len(sem_controls["calls"]) == calls


def test_source_facts_failure_clears_prior_execution_metadata(sem_controls, monkeypatch):
    from code_forge.context_sources import gather

    source = GraphTriageSource(sem_controls["root"])
    sem_controls["diff"] = (0, json.dumps({"changes": [ENTITY]}))
    assert source.facts(["a.py"], DIFF)
    assert source.acquisition_outcome is not None and source.acquisition_outcome.completed

    def refuse(*args):
        raise RuntimeError("controlled acquisition dispatch failure")

    monkeypatch.setattr(gt.GraphTriageRunner, "run", refuse)
    result = gather([source], ["a.py"], DIFF, head_sha=None)
    assert result.errors and source.findings_cache is None
    assert source.acquisition_outcome is None


def test_source_failed_snapshot_cannot_reuse_prior_backend_policy(sem_controls, monkeypatch):
    source = GraphTriageSource(sem_controls["root"])
    source.snapshot_sha()

    def refuse(path):
        raise ValueError("controlled policy became invalid")

    monkeypatch.setattr("code_forge.gate_check.load_gate_config", refuse)
    with pytest.raises(ValueError, match="policy became invalid"):
        source.snapshot_sha()
    error = None
    try:
        source.facts(["a.py"], DIFF)
    except RuntimeError as exc:
        error = str(exc)
    assert error is not None and "configuration_error" in error


@pytest.fixture
def graphdb_controls(sem_controls, monkeypatch):
    """Use owned SQLite; only late query/close errors have explicit adapters."""
    import sqlite3
    from contextlib import closing

    root = sem_controls["root"]
    db = _write_owned_graph(root)
    (root / "b.py").write_text("from a import measured\ndef caller():\n    return measured()\n")
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            "insert into nodes values (2, 'Function', 'caller', 'b.py::caller', 'b.py', 2, 3)"
        )
        connection.execute(
            "insert into nodes values (3, 'Module', 'module-level', 'a.py::module', 'a.py', 1, 2)"
        )
        connection.execute("insert into edges values ('CALLS', 'b.py::caller', 'measured')")
        connection.execute("insert into edges values ('IMPORTS_FROM', 'b.py::caller', 'a')")
        connection.commit()
    real_which = gt.shutil.which
    monkeypatch.setattr(gt.shutil, "which", lambda name: None if name == "sem" else real_which(name))
    controls = {
        "root": root,
        "db": db,
        "original": db.read_bytes(),
        "failure": None,
        "queries": [],
        "connections": [],
        "objects": [],
        "closed_adapters": [],
    }
    actual_connect = sqlite3.connect

    class Cursor:
        def __init__(self, connection):
            self.cursor = connection.cursor()

        def execute(self, statement, parameters=()):
            if statement.startswith("SELECT id, kind") and parameters == ("%b.py",):
                return self.cursor.execute("select value from owned_missing_table")
            return self.cursor.execute(statement, parameters)

        def fetchall(self):
            return self.cursor.fetchall()

        def fetchone(self):
            return self.cursor.fetchone()

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args):
            return self.connection.execute(*args)

        def cursor(self):
            return (
                Cursor(self.connection) if controls["failure"] == "query" else self.connection.cursor()
            )

        def close(self):
            self.connection.close()
            controls["closed_adapters"].append(controls["failure"])
            if controls["failure"] == "close":
                raise OSError("controlled owned SQLite close refusal")

    def connect(database, *args, **kwargs):
        assert str(database) in {str(db), "file:%s?mode=ro" % db}
        connection = actual_connect(database, *args, **kwargs)
        controls["connections"].append(str(database))
        controls["objects"].append(connection)
        connection.set_trace_callback(controls["queries"].append)
        if kwargs.get("uri") and controls["failure"] in {"query", "close"}:
            return Connection(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    yield controls
    for connection in controls["objects"]:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("select 1")
    assert not sem_controls["calls"]


@pytest.mark.parametrize("failure", ["missing", "corrupt", "schema", "query", "close"])
def test_graphdb_failed_read_discards_rows_and_recovers(graphdb_controls, failure):
    import sqlite3
    from contextlib import closing

    controls = graphdb_controls
    db, root = controls["db"], controls["root"]
    if failure == "missing":
        db.unlink()
    elif failure == "corrupt":
        db.write_bytes(b"owned non-SQLite contents")
    elif failure == "schema":
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("drop table nodes")
            connection.commit()
    else:
        controls["failure"] = failure
    files = ["a.py", "b.py"] if failure == "query" else ["a.py"]
    outcome = gt._run_graphdb(str(db), files)
    assert not outcome.completed and outcome.status == "execution_error"
    assert outcome.entities == [] and outcome.impact_complete is False and outcome.diagnostic
    if failure == "query":
        assert any("source_qualified" in query for query in controls["queries"])
        assert "no such table" in outcome.diagnostic
    if failure == "close":
        assert controls["closed_adapters"] == ["close"]
        assert "close refusal" in outcome.diagnostic
    runner = gt.GraphTriageRunner()
    runner._backend_selection = (("graphdb", str(db)), {})
    diff = DIFF + "\n+++ b/b.py\n" if failure == "query" else DIFF
    assert runner.run(diff, root) == []
    assert runner.acquisition_outcome is not None and not runner.acquisition_outcome.completed
    assert runner.acquisition_outcome.entities == [] and runner._cached_findings is None
    assert (
        len(runner.infra_errors) == 1
        and "graph.db acquisition execution_error" in runner.infra_errors[0]
    )
    db.write_bytes(controls["original"])
    controls["failure"] = None
    findings = runner.run(DIFF, root)
    assert len(findings) == 1 and "measured" in findings[0].description
    assert not runner.infra_errors and runner.acquisition_outcome.completed
    assert runner.acquisition_outcome.impact_complete is True
    queries = len(controls["queries"])
    assert runner.run(DIFF, root) == findings
    assert len(controls["queries"]) == queries


@pytest.mark.parametrize("diff", ["+++ b/empty.py\n", "diff --git a/a.py b/a.py\n"])
def test_graphdb_successful_empty_is_typed_and_cached(graphdb_controls, diff):
    controls = graphdb_controls
    runner = gt.GraphTriageRunner()
    assert runner.run(diff, controls["root"]) == []
    assert runner.acquisition_outcome is not None and runner.acquisition_outcome.completed
    assert runner.acquisition_outcome.status == "completed_empty"
    assert (
        runner.acquisition_outcome.entities == [] and runner.acquisition_outcome.impact_complete is True
    )
    assert runner._cached_findings == [] and not runner.infra_errors
    queries = len(controls["queries"])
    controls["db"].write_bytes(b"owned corruption after completed empty")
    assert runner.run(diff, controls["root"]) == []
    assert len(controls["queries"]) == queries and not runner.infra_errors


@pytest.mark.parametrize("failure", ["missing", "corrupt", "schema"])
@pytest.mark.parametrize(
    "retry,allow,expected",
    [("current", False, 1), ("stale", False, 0), ("stale", True, 1), ("disabled", False, 0)],
)
@pytest.mark.parametrize("mode", ["ci", "local"])
def test_actual_cli_graphdb_failure_retries_with_source_authority(
    graphdb_controls, cli_pipeline, monkeypatch, failure, retry, allow, expected, mode
):
    import sqlite3
    from contextlib import closing
    from code_forge import cli, context_sources
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    controls = graphdb_controls
    db = controls["db"]
    head = "a" * 40
    args.mode = mode
    args.allow_unsnapshotted_context = allow
    monkeypatch.setattr(
        cli,
        "resolve_baseline",
        lambda *a, **kw: ResolvedReview(
            source_files=[root / "a.py"],
            baseline_content=None,
            git_diff=DIFF,
            mode_hint="non-git",
            head_sha=head,
        ),
    )
    actual_snapshot = context_sources._graphdb_head_sha
    snapshots = []

    def snapshot_then_damage(path):
        snapshot = actual_snapshot(path)
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            assert snapshot == head
            if failure == "missing":
                db.unlink()
            elif failure == "corrupt":
                db.write_bytes(b"owned non-SQLite contents")
            else:
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute("drop table nodes")
                    connection.commit()
        return snapshot

    monkeypatch.setattr(context_sources, "_graphdb_head_sha", snapshot_then_damage)
    dispatched = []

    def advisory_boundary(machine):
        machine.advisory_runners = [
            item for item in machine.advisory_runners if isinstance(item, gt.GraphTriageRunner)
        ]
        assert len(machine.advisory_runners) == 1
        machine._run_advisory_axes()
        runner = machine.advisory_runners[0]
        dispatched.append((list(machine._advisories), runner, machine._state))
        return Verdict.PASS

    def hold(**kwargs):
        seen["hold"] = kwargs
        source = seen["source"]
        seen["initial_source"] = source
        assert kwargs["pre_graph_findings"] is None and source.findings_cache is None
        assert source.acquisition_outcome is not None and not source.acquisition_outcome.completed
        assert source.acquisition_outcome.status == "execution_error"
        db.write_bytes(controls["original"])
        if retry == "stale":
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("update metadata set value=? where key='git_head_sha'", ("b" * 40,))
                connection.commit()
        elif retry == "disabled":
            gate = root / ".code-forge/gate.yaml"
            gate.parent.mkdir(exist_ok=True)
            gate.write_text("test:\n  command: [pytest]\ngraph_triage:\n  enabled: false\n")
            monkeypatch.setattr("code_forge.gate_check.load_gate_config", actual_gate_loader)
        return seen["actual_hold"](**kwargs)

    monkeypatch.setattr(cli, "_run_hold_loop", hold)
    monkeypatch.setattr(cli.StateMachine, "run", advisory_boundary)
    l2_roots = _record_l2_root(monkeypatch, cli, root)
    monkeypatch.setattr(cli, "build_e2e_checker", lambda: lambda *a: ([], []))
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PASS
    assert l2_roots == [root]
    assert len(dispatched) == 1 and len(dispatched[0][0]) == expected
    runner, state = dispatched[0][1:]
    assert not runner.infra_errors and not state.infra_errors
    assert seen["initial_source"].findings_cache is None
    if expected:
        assert runner.acquisition_outcome.completed and runner._cached_findings
        assert "measured" in dispatched[0][0][0].description
    elif retry == "disabled":
        assert runner.acquisition_outcome.status == "disabled" and runner._cached_findings == []
    else:
        assert runner.acquisition_outcome is None and runner._cached_findings is None
    assert snapshots == (
        [head] if retry == "disabled" else [head, "b" * 40 if retry == "stale" else head]
    )


@pytest.mark.parametrize("mode", ["ci", "local"])
def test_actual_cli_graphdb_empty_success_seeds_once(graphdb_controls, cli_pipeline, monkeypatch, mode):
    import sqlite3
    from contextlib import closing
    from code_forge import cli
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict

    root, args, seen = cli_pipeline
    controls = graphdb_controls
    args.mode = mode
    with closing(sqlite3.connect(controls["db"])) as connection:
        connection.execute("delete from nodes")
        connection.commit()
    monkeypatch.setattr(
        cli,
        "resolve_baseline",
        lambda *a, **kw: ResolvedReview(
            source_files=[root / "a.py"],
            baseline_content=None,
            git_diff=DIFF,
            mode_hint="non-git",
            head_sha="a" * 40,
        ),
    )
    observations = []

    def advisory_boundary(machine):
        machine.advisory_runners = [
            item for item in machine.advisory_runners if isinstance(item, gt.GraphTriageRunner)
        ]
        assert len(machine.advisory_runners) == 1
        before = len(controls["queries"])
        machine._run_advisory_axes()
        assert not machine._advisories and not machine._state.infra_errors
        assert len(controls["queries"]) == before
        observations.append(machine.advisory_runners[0]._cached_findings)
        return Verdict.PASS

    def hold(**kwargs):
        assert kwargs["pre_graph_findings"] == [] and seen["source"].findings_cache == []
        assert seen["source"].acquisition_outcome.status == "completed_empty"
        controls["db"].write_bytes(b"owned corruption after completed empty")
        return seen["actual_hold"](**kwargs)

    monkeypatch.setattr(cli, "_run_hold_loop", hold)
    monkeypatch.setattr(cli.StateMachine, "run", advisory_boundary)
    l2_roots = _record_l2_root(monkeypatch, cli, root)
    monkeypatch.setattr(cli, "build_e2e_checker", lambda: lambda *a: ([], []))
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root) == Verdict.PASS
    assert l2_roots == [root]
    assert observations == [[]]

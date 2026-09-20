# SPDX-License-Identifier: Apache-2.0
"""Resource guards for the mutmut subprocess tree.

Regression coverage for the OOM-killed review service (2026-09-15): mutmut
>=3.4 defaults --max-children to os.cpu_count(), so on a 16-core host the
per-mutant loop fanned out to 16 concurrent full-suite pytest processes
(6.3G memory peak, systemd Result=oom-kill). These tests pin the cap, the
RLIMIT_AS backstop, the integration-test exclusion, and the detached
launcher's forwarding of both guards.
"""
import json
import os
import pathlib
import subprocess
import sys
from unittest.mock import patch

from code_forge.disposition import Disposition
from code_forge.mutation import (
    _DEFAULT_MAX_CHILDREN_CAP,
    _build_mutmut_config,
    _effective_max_children,
    _exclude_unmirrorable_tests,
    _memory_limit_bytes,
    launch_detached_mutation,
    run_mutation,
)


def _run_calls(mock_run):
    """Return the mutmut 'run' invocation captured by the mocked subprocess."""
    runs = [
        call[0][0]
        for call in mock_run.call_args_list
        if isinstance(call[0][0], list)
        and len(call[0][0]) >= 2
        and call[0][0][1] == "run"
    ]
    assert len(runs) == 1, f"expected exactly one mutmut run call, got {runs!r}"
    return runs[0]


def _run_mutation_guarded(tmp_path, mock_run, **kwargs):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    with patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut"), \
         patch("code_forge.mutation._resolve_mutmut_invocation", return_value=["mutmut"]):
        findings, errors = run_mutation(
            ["src/pkg/mod.py"], ["pytest", "-q"], cwd=tmp_path, **kwargs
        )
    assert errors == []
    assert findings == []
    return _run_calls(mock_run)


def test_run_mutation_caps_max_children_by_default(tmp_path):
    with patch("code_forge.mutation.subprocess.run") as mock_run:
        argv = _run_mutation_guarded(tmp_path, mock_run)
    idx = argv.index("--max-children")
    value = int(argv[idx + 1])
    assert value == min((os.cpu_count() or 4), _DEFAULT_MAX_CHILDREN_CAP)
    assert value <= _DEFAULT_MAX_CHILDREN_CAP


def test_explicit_max_children_wins(tmp_path):
    with patch("code_forge.mutation.subprocess.run") as mock_run:
        argv = _run_mutation_guarded(tmp_path, mock_run, max_children=7)
    assert int(argv[argv.index("--max-children") + 1]) == 7


def test_env_overrides_default_children_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_MUTATION_MAX_CHILDREN", "2")
    assert _effective_max_children(None) == 2
    with patch("code_forge.mutation.subprocess.run") as mock_run:
        argv = _run_mutation_guarded(tmp_path, mock_run)
    assert int(argv[argv.index("--max-children") + 1]) == 2


def test_run_mutation_sets_rlimit_as_backstop(tmp_path):
    import resource

    limit = 256 * 1024**2
    with patch("code_forge.mutation.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut"), \
             patch("code_forge.mutation._resolve_mutmut_invocation", return_value=["mutmut"]):
            run_mutation(
                ["src/pkg/mod.py"], ["pytest", "-q"], cwd=tmp_path,
                memory_limit_bytes=limit,
            )

    run_call = next(
        call for call in mock_run.call_args_list
        if isinstance(call[0][0], list) and call[0][0][1:2] == ["run"]
    )
    preexec = run_call[1]["preexec_fn"]
    if sys.platform == "win32":
        assert preexec is None
        return
    assert preexec is not None
    # Execute the limiter in a forked child: setrlimit is process-wide and
    # irreversible for the hard limit, so it must never run in the pytest
    # process itself.
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            preexec()
            soft, _hard = resource.getrlimit(resource.RLIMIT_AS)
            os._exit(0 if soft == limit else 1)
        except (OSError, ValueError):
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0


def test_memory_limit_param_beats_default():
    assert _memory_limit_bytes(256 * 1024**2) == 256 * 1024**2


def test_memory_limit_env_in_mb(monkeypatch):
    monkeypatch.setenv("FORGE_MUTATION_MEMORY_LIMIT_MB", "128")
    assert _memory_limit_bytes(None) == 128 * 1024**2


def test_effective_children_clamps_minimum():
    assert _effective_max_children(0) == 1
    assert _effective_max_children(-5) == 1


def test_selection_appends_integration_exclusion():
    selection = _exclude_unmirrorable_tests(["-q", "--ignore=tests/test_x.py"])
    assert selection[-2:] == ["-m", "not integration and not source_scan"]


def test_selection_combines_existing_marker_expr():
    selection = _exclude_unmirrorable_tests(["-q", "-m", "slow"])
    assert selection[selection.index("-m") + 1] == (
        "(slow) and (not integration and not source_scan)"
    )


def test_selection_excludes_source_scanning_tests():
    """Tests that grep the source tree read mutmut's mirror there.

    The mirror holds one rewritten variant per mutation, so a mutated
    string literal reads as the violation such a scan forbids, reported
    at a line number past the end of the real file.
    """
    selection = _exclude_unmirrorable_tests(["-q"])
    marker_expr = selection[selection.index("-m") + 1]
    assert "not source_scan" in marker_expr


def test_build_mutmut_config_excludes_integration(tmp_path):
    (tmp_path / "src").mkdir()
    config = _build_mutmut_config(["src/pkg/mod.py"], ["pytest", "-q"])
    lines = config.splitlines()
    idx = lines.index("pytest_add_cli_args_test_selection=-q")
    assert lines[idx + 1] == "    -m"
    assert lines[idx + 2] == "    not integration and not source_scan"


def test_detached_script_forwards_resource_guards(tmp_path, run_detached_payload):
    captured = {}

    def spawn(args, **kwargs):
        captured["script"] = args[2]
        return type("Child", (), {"pid": 4321, "wait": lambda self, timeout=None: 0})()

    result_path = tmp_path / "result.json"
    with patch("code_forge.mutation.subprocess.Popen", side_effect=spawn):
        assert launch_detached_mutation(
            ["source.py"], ["pytest"], tmp_path, result_path,
            max_children=3, memory_limit_bytes=64 * 1024**2,
        ) is True

    finding = type("F", (), {
        "id": "mutant-x", "source": "MUTANT",
        "disposition": Disposition.CONFIRMED,
    })()
    with patch("code_forge.mutation.run_mutation", return_value=([finding], [])) as run:
        run_detached_payload(captured["script"])
    _, kwargs = run.call_args
    assert kwargs["max_children"] == 3
    assert kwargs["memory_limit_bytes"] == 64 * 1024**2
    data = json.loads(result_path.read_text())
    assert data["status"] == "done"
    assert data["survivors"] == ["mutant-x"]


class TestRepoGateConfigIsTracked:
    """This repo's own gate.yaml must ship its mutation-mirror needs.

    The mirror only carries source_paths, so a test that opens a file by
    path needs its directory in also_copy. Those three entries each came
    from a real mutation-gate failure; keeping them in a gitignored local
    file meant a fresh clone hit the same three failures again.
    """

    def _gate_path(self):
        return pathlib.Path(__file__).resolve().parent.parent / ".code-forge" / "gate.yaml"

    def test_gate_yaml_is_committed(self):
        assert self._gate_path().is_file(), (
            "the gate config is part of the repo, not a local artifact"
        )

    def test_also_copy_carries_every_path_loading_directory(self):
        from code_forge.gate_check import load_gate_config

        also_copy = load_gate_config(self._gate_path())["test"]["also_copy"]
        for needed in ("scripts/", "cli/", "fixtures/"):
            assert needed in also_copy, (
                "%s holds files tests open by path; without it the "
                "mutation gate dies with FileNotFoundError" % needed
            )


def test_review_forwards_skip_globs_from_gate_config():
    """The review path must carry the glob keys, not just the mutation API.

    Every skip/include test below this line drives run_mutation directly,
    so all of them stayed green while machine.py read gate.yaml without
    ever looking up mutation_skip_globs -- the keys parsed, validated
    against the schema, and were dropped one call short of the runner.
    A gate.yaml skip list that silently does nothing is worse than none:
    the run reports PASS over mutants nobody meant to score.
    """
    source = pathlib.Path(
        __import__("code_forge.machine", fromlist=["x"]).__file__
    ).read_text()
    lookup_start = source.index("mutation_max_children = test_config.get")
    call_end = source.index(")", source.index("launch_detached_mutation(", lookup_start))
    region = source[lookup_start:call_end]

    for key in ("mutation_skip_globs", "mutation_include_globs"):
        assert "test_config.get(\"%s\")" % key in region, (
            "machine.py never reads %s out of gate.yaml, so the "
            "configured list dies before reaching the runner" % key
        )
        assert "%s=%s" % (key, key) in region, (
            "%s is read but not passed to launch_detached_mutation" % key
        )

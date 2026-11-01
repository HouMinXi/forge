"""#246: an env-strip retry is recorded even when the retry passes."""

import subprocess
import sys

import pytest

from code_forge import fixval, mutation
from code_forge._fixval_transaction import TransactionError
from tests.test_baseline_failure_causes import CMD, PATCH, private_project  # noqa: F401

FIRST = "/caller-marker/python3: No module named pytest\n"


def retry_passes(calls):
    def run(argv, **kwargs):
        calls.append(dict(kwargs["env"]))
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 11, "", FIRST)
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    return run


def in_private_venv(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
    monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")


def run_mutation_stopping_at_resolver(root, monkeypatch, calls):
    monkeypatch.setattr(mutation, "run_owned_command", retry_passes(calls))
    resolved = []
    monkeypatch.setattr(
        mutation, "_resolve_mutmut_invocation", lambda *a, **k: resolved.append(k["env"])
    )
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7, _evidence=evidence
    )
    return findings, infra, evidence, resolved


def test_mutation_records_a_passing_strip_retry(private_project, monkeypatch):  # noqa: F811
    in_private_venv(monkeypatch)
    calls = []
    findings, infra, evidence, resolved = run_mutation_stopping_at_resolver(
        private_project, monkeypatch, calls
    )
    assert "VIRTUAL_ENV" in calls[0] and all("VIRTUAL_ENV" not in env for env in calls[1:])
    assert (
        resolved and "VIRTUAL_ENV" not in resolved[0]
    )  # the retry passed; measurement continued stripped
    record = evidence["baseline_env_retry"]
    assert record["stripped_virtual_env"] == "/private-venv"
    assert record["first_attempt"].startswith("run 1: returncode 11")
    assert "No module named pytest" in record["first_attempt"]
    outcome = mutation._mutation_outcome(findings, infra, evidence)
    assert outcome["baseline_env_retry"] == record


def test_mutation_without_strip_records_nothing(private_project, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(
        mutation,
        "run_owned_command",
        lambda argv, **k: (
            calls.append(k["env"]) or subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")
        ),
    )
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda *a, **k: None)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/pkg/mod.py"],
        CMD + ["tests/test_mod.py"],
        cwd=private_project,
        baseline_timeout=7,
        _evidence=evidence,
    )
    assert len(calls) == 3
    assert "baseline_env_retry" not in evidence
    assert "baseline_env_retry" not in mutation._mutation_outcome(findings, infra, evidence)


@pytest.mark.parametrize("retry", [False, True], ids=["no-strip", "strip"])
def test_fixval_advises_a_passing_strip_retry(private_project, monkeypatch, retry):  # noqa: F811
    if retry:
        in_private_venv(monkeypatch)
    calls = []
    run = retry_passes(calls)
    monkeypatch.setattr(
        fixval.subprocess,
        "run",
        run
        if retry
        else (
            lambda argv, **k: (
                calls.append(k["env"]) or subprocess.CompletedProcess(argv, 0, "1 passed\n", "")
            )
        ),
    )
    allocate = fixval.tempfile.mkstemp
    monkeypatch.setattr(
        fixval.tempfile, "mkstemp", lambda *a, **k: allocate(*a, **k, dir=private_project)
    )

    def refuse(*_a, **_k):
        raise TransactionError("stop after baseline")

    monkeypatch.setattr(fixval, "FixvalTransaction", refuse)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    result = fixval.run_fixval(candidate, CMD, private_project, "fix: record strip", PATCH)
    assert (
        result.status == fixval.FixvalStatus.BLOCK
    )  # the baseline passed; the stub stops the transaction
    notes = [a for a in result.advisories if a.id == "FIXVAL_BASELINE_ENV_RETRY"]
    if not retry:
        assert notes == []
        return
    assert len(notes) == 1
    assert "VIRTUAL_ENV /private-venv" in notes[0].description
    assert "first attempt: run 1: returncode 11" in notes[0].description
    assert "No module named pytest" in notes[0].description


def strip_then_pass(seen_envs):
    """First guard call asks for the strip (and records its cause); the retry passes."""

    def guard(cmd, env, root, *, allow_strip_retry, retry_cause=None, first_attempt=None, **_kw):
        seen_envs.append(dict(env))
        if allow_strip_retry:
            retry_cause.append("run 1: returncode 11; stderr: No module named pytest")
            return ("needs_strip_retry", [], [])
        return ("passed", [], [])

    return guard


def git_fix_project(root, test_body):
    def git(*argv):
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@test.com")
    git("config", "user.name", "Test")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    code, test = root / "src/calc.py", root / "tests/test_calc.py"
    code.write_text("def add(a, b):\n    return 0\n")
    test.write_text("def test_add():\n    pass\n")
    git("add", ".")
    git("commit", "-m", "initial")
    code.write_text("def add(a, b):\n    return a + b\n")
    test.write_text(test_body)
    diff = subprocess.run(["git", "diff"], cwd=root, check=True, capture_output=True, text=True).stdout
    return diff, code


@pytest.mark.parametrize(
    ("check", "status"),
    [("assert add(1, 2) == 3", "PASS"), ("assert True", "BLOCK")],
    ids=["pass", "hollow-block"],
)
def test_fixval_full_run_after_passing_strip_retry_carries_advisory(
    tmp_path, monkeypatch, check, status
):
    in_private_venv(monkeypatch)
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    seen = []
    monkeypatch.setattr(fixval, "_run_baseline_guard", strip_then_pass(seen))
    diff, code = git_fix_project(tmp_path, "def test_add():\n    %s\n" % check)
    test_cmd = [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, 'src'); from calc import add; " + check,
    ]
    candidate = fixval.FixvalCandidate(["tests/test_calc.py"], ["src/calc.py"])
    result = fixval.run_fixval(candidate, test_cmd, tmp_path, "fix: add", diff)
    assert result.status == getattr(fixval.FixvalStatus, status)
    if status == "BLOCK":
        assert [f.id for f in result.findings] == ["FIXVAL_HOLLOW"]
    assert "VIRTUAL_ENV" in seen[0] and "VIRTUAL_ENV" not in seen[1]
    notes = [a for a in result.advisories if a.id == "FIXVAL_BASELINE_ENV_RETRY"]
    assert len(notes) == 1
    assert "VIRTUAL_ENV /private-venv" in notes[0].description
    assert "first attempt: run 1: returncode 11; stderr: No module named pytest" in notes[0].description
    assert "return a + b" in code.read_text()  # the fix was restored


def test_fixval_patch_allocation_failure_after_strip_retry_carries_advisory(
    private_project,  # noqa: F811 -- imported pytest fixture, intentionally injected
    monkeypatch,
):
    in_private_venv(monkeypatch)
    monkeypatch.setattr(fixval, "_run_baseline_guard", strip_then_pass([]))

    def no_space(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fixval.tempfile, "mkstemp", no_space)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    result = fixval.run_fixval(candidate, CMD, private_project, "fix: record strip", PATCH)
    assert result.status == fixval.FixvalStatus.BLOCK
    assert "cannot allocate revert patch" in result.block_message
    assert [a.id for a in result.advisories] == ["FIXVAL_BASELINE_ENV_RETRY"]
    assert "first attempt: run 1: returncode 11" in result.advisories[0].description


@pytest.mark.parametrize("retried", [True, False], ids=["strip", "no-strip"])
def test_mutation_check_prints_env_retry_note(tmp_path, monkeypatch, capsys, retried):
    from code_forge.cli import _build_parser, _run_mutation_check

    diff_path = tmp_path / "empty.diff"
    diff_path.write_text("", encoding="utf-8")

    def measured(**kwargs):
        if retried:
            kwargs["_evidence"]["baseline_env_retry"] = {
                "first_attempt": "run 1: returncode 11; stderr: No module named pytest",
                "stripped_virtual_env": "/private-venv",
            }
        return [], []

    monkeypatch.setattr("code_forge.mutation.run_mutation", measured)
    args = _build_parser().parse_args(["mutation-check", "--diff", str(diff_path)])
    assert _run_mutation_check(args, cwd=tmp_path) == 0
    notes = [
        line for line in capsys.readouterr().err.splitlines() if "NOTE: baseline env-strip retry" in line
    ]
    if not retried:
        assert notes == []
        return
    assert notes == [
        "code-forge: mutation-check: NOTE: baseline env-strip retry: VIRTUAL_ENV /private-venv and its PATH "
        "entry were dropped; first attempt: run 1: returncode 11; stderr: No module named pytest"
    ]

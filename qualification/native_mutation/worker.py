# SPDX-License-Identifier: Apache-2.0
"""Run one tiny native fixture under the qualification parent's real owner.

No acquisition, process-owner, parser, ledger or StateMachine replacements.
The profiler only observes returns and copies native artifacts before cleanup.
This is synchronous source-checkout coverage, not detached/cgroup/ABA acceptance.
"""

from copy import deepcopy
from dataclasses import replace
import fcntl
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import resource
import select
import shutil
import subprocess
import sys
import tomllib

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from code_forge import mutation  # noqa: E402
from code_forge.autofix import StubAutoFixer  # noqa: E402
from code_forge.baseline import GitRefBaseline, resolve_baseline  # noqa: E402
from code_forge.disposition import Disposition  # noqa: E402
from code_forge.factories import build_l1_provider  # noqa: E402
from code_forge.falsify import StubFalsifier  # noqa: E402
from code_forge.ledger import (  # noqa: E402
    LedgerRow,
    TerminalState,
    append_row,
    finding_suppression_key,
    resolve_ledger_root,
)
from code_forge.machine import StateMachine  # noqa: E402
from code_forge.source import compute_source_hash  # noqa: E402
from code_forge.state import Mode, Verdict, load_state  # noqa: E402
from code_forge.trust import record_trust  # noqa: E402

BASE = b"def double(x):\n    return x * 1\n"
SOURCE_A = b"def double(x):\n    return x * 0\n"
SOURCE_B = b"def double(x):\n    return x * 2\n"
SETUP = b"[unrelated]\nvalue=preserve\n"
SOURCE_A_INVENTORY = {"calc.x_double__mutmut_1": "killed", "calc.x_double__mutmut_2": "survived"}
SOURCE_B_INVENTORY = dict.fromkeys(SOURCE_A_INVENTORY, "survived")
SURVIVOR = "mutant-calc.x_double__mutmut_2"
MEMORY = 1024**3
BASELINE = [sys.executable, "-m", "pytest", "-q", "tests"]


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()


def prepare(root):
    """Fail closed after opt-in; do not turn missing prerequisites into skips."""
    assert sys.platform == "linux" and sys.implementation.name == "cpython"
    assert sys.version_info[:2] == (3, 12)
    assert not sys.flags.optimize, "qualification assertions must remain enabled"
    assert all(0 < limit <= MEMORY for limit in resource.getrlimit(resource.RLIMIT_AS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    assert version("mutmut") == "3.8.1"
    dev = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())["project"]["optional-dependencies"][
        "dev"
    ]
    assert "pytest==" + version("pytest") in dev, "use the repository's pytest pin"
    assert Path(mutation.__file__).resolve() == REPOSITORY / "src/code_forge/mutation.py"
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    assert children.is_file(), "readable Linux task children are required for process ownership"
    children.read_bytes()
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert child.pid in {int(pid) for pid in children.read_bytes().split()}
        descriptor = os.pidfd_open(child.pid)
        try:
            assert not select.select([descriptor], [], [], 0)[0]
        finally:
            os.close(descriptor)
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    executable = shutil.which("git")
    assert executable, "Git is required"
    for key in tuple(os.environ):
        if key.startswith(("FORGE_", "CODE_FORGE_", "GIT_", "PYTEST_", "PYTHON")):
            os.environ.pop(key)
    os.environ.pop("MUTANT_UNDER_TEST", None)
    home = root / "home"
    home.mkdir()
    temporary = root / "tmp"
    temporary.mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(executable)
    os.environ.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CACHE_HOME=str(home / ".cache"),
        TMPDIR=str(temporary),
        TEMP=str(temporary),
        TMP=str(temporary),
        PATH=str(bin_dir),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        FORGE_MUTATION_IDLE_MAX_WAIT="0",
    )
    # Select the supported direct route without a redundant detached launch.
    # run_mutation resolves the real engine through the absolute interpreter.
    assert shutil.which("mutmut") is None
    source = root / "source"
    (source / "tests").mkdir(parents=True)
    (source / "calc.py").write_bytes(BASE)
    (source / "setup.cfg").write_bytes(SETUP)
    (source / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (source / "requirements.txt").write_text("# Empty fixture dependencies.\n", encoding="utf-8")
    (source / "tests/test_calc.py").write_text(
        "from calc import double\n\ndef test_double():\n    assert double(0) == 0\n",
        encoding="utf-8",
    )
    git(source, "init", "-q")
    git(source, "add", ".")
    git(
        source,
        "-c",
        "user.name=Native Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    state = root / "state"
    git(source, "worktree", "add", "--detach", str(state), "HEAD")
    (state / ".code-forge").mkdir()
    for path in (source, state):
        (path / "calc.py").write_bytes(SOURCE_A)
    config = root / "config"
    (config / ".code-forge").mkdir(parents=True)
    gate = {
        "test": {
            "command": BASELINE,
            "timeout_seconds": 20,
            "mutation_timeout_seconds": 60,
            "mutation_max_children": 1,
            "mutation_memory_limit_mb": 1024,
        }
    }
    (config / ".code-forge/gate.yaml").write_text(json.dumps(gate), encoding="utf-8")
    record_trust(config / ".code-forge/gate.yaml", gate)
    return source, state, config


class Observation:
    def __init__(self, source, directory):
        self.source = source
        self.directory = directory
        directory.mkdir()
        self.commands = []
        self.measurements = []

    def profile(self, frame, event, result):
        if event != "return":
            return
        if frame.f_code is mutation.run_owned_command.__code__ and result is not None:
            local = frame.f_locals
            self.commands.append(
                {
                    "argv": local["argv"],
                    "timeout": local["timeout"],
                    "memory_limit_bytes": local["memory_limit_bytes"],
                    "returncode": result.returncode,
                    "ownership": result.ownership,
                }
            )
            index = len(self.commands) - 1
            for name in ("stdout", "stderr"):
                value = getattr(result, name)
                data = value.encode("utf-8") if isinstance(value, str) else value
                (self.directory / f"{index:02d}-{name}.txt").write_bytes(data)
            for name in ("calc.py", "calc.py.meta"):
                path = self.source / "mutants" / name
                if path.is_file():
                    (self.directory / f"{index:02d}-{name}").write_bytes(path.read_bytes())
            write_json(self.directory / "commands.json", self.commands)
        elif frame.f_code is mutation.run_mutation.__code__:
            self.measurements.append(deepcopy(frame.f_locals["evidence"]))
            write_json(self.directory / "measurements.json", self.measurements)

    def run(self, function):
        assert sys.getprofile() is None
        sys.setprofile(self.profile)
        try:
            return function()
        finally:
            sys.setprofile(None)

    def verify(self, inventory, source_bytes):
        assert len(self.commands) == 6 and len(self.measurements) == 1
        expected = [BASELINE] * 3 + [
            [sys.executable, "-c", "import mutmut"],
            [sys.executable, "-m", "mutmut", "run", "--max-children", "1"],
            [sys.executable, "-m", "mutmut", "results", "--all", "true"],
        ]
        assert [item["argv"] for item in self.commands] == expected
        assert [item["timeout"] for item in self.commands] == [20, 20, 20, 30, 60, 10]
        assert self.commands[4]["memory_limit_bytes"] == MEMORY
        for item in self.commands:
            assert item["returncode"] == 0
            owner = item["ownership"]
            assert owner["cleanup_complete"] is True
            assert owner["timed_out"] is False and owner["cancelled"] is False
            assert not any(process.get("remaining") for process in owner["owned"])
        proof = self.measurements[0]
        assert proof["completed_measurement"] is True and proof["baseline_passed"] is True
        assert not proof["skipped"] and not proof["infra_errors"]
        assert proof["inventory"] == inventory
        meta_bytes = (self.directory / "05-calc.py.meta").read_bytes()
        results_bytes = (self.directory / "05-stdout.txt").read_bytes()
        assert proof["metadata_sha256"] == {"calc.py": hashlib.sha256(meta_bytes).hexdigest()}
        assert proof["results_sha256"] == hashlib.sha256(results_bytes).hexdigest()
        native = json.loads(meta_bytes)["exit_code_by_key"]
        assert all(type(code) is int for code in native.values())
        assert native == {key: 0 if status == "survived" else 1 for key, status in inventory.items()}
        assert (self.source / "calc.py").read_bytes() == source_bytes
        assert (self.source / "setup.cfg").read_bytes() == SETUP
        assert not (self.source / "mutants").exists()
        journal = self.source / ".code-forge/mutation-owner.lock"
        assert json.loads(journal.read_text())["phase"] == "complete"
        with journal.open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        for name in ("mutation-empty-config", "mutation-empty-mirror"):
            assert not list((self.source / ".code-forge" / name).iterdir())
        assert not list(self.source.glob(".mutants-forge-*"))
        assert not list((self.source / ".code-forge").glob("mutation-config-*"))


def review(source):
    return resolve_baseline(GitRefBaseline("HEAD"), GitRefBaseline("WORKING"), [Path("calc.py")], source)


def machine(source, state, config, reviewed):
    return StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda _finding: None,
        resolved_review=reviewed,
        source_hash=compute_source_hash(git_diff=reviewed.git_diff or ""),
        baseline_spec_repr="HEAD..WORKING",
        cwd=state,
        source_root=source,
        test_config_root=config,
        registry={},
        l0_runner=lambda *_args: ([], []),
        l1_provider=build_l1_provider("stub", reviewed, max_attempts=1),
        coverage_l1_active=False,
        coverage_exempt_patterns=["calc.py"],
        l2_runner=mutation.run_mutation,
    )


def ruling(source, reviewed, finding):
    line = finding.line_range[0] if finding.line_range else 0
    return LedgerRow(
        fingerprint=finding.fingerprint,
        repo_root=str(resolve_ledger_root(source).resolve()),
        base_sha=reviewed.base_sha,
        head_sha=reviewed.head_sha,
        file=finding.file,
        line=line,
        axis_claim="mutation",
        pass_provenance="adjudicated",
        terminal_state=TerminalState.DISPROVED,
        evidence_class="synthetic fixture ruling for a genuine native survivor",
        ts="2026-11-06T00:00:00Z",
        suppression_key=finding_suppression_key(
            base_sha=reviewed.base_sha,
            head_sha=reviewed.head_sha,
            git_diff=reviewed.git_diff,
            file=finding.file,
            line=line,
            source=finding.source,
            description=finding.description,
        ),
    )


def main(root):
    source, state, config = prepare(root)
    original = (mutation.run_mutation, mutation.run_owned_command, subprocess.Popen)
    observation = Observation(source, root / "seed")
    findings, errors = observation.run(
        lambda: mutation.run_mutation(
            ["calc.py"],
            BASELINE,
            cwd=source,
            timeout=60,
            baseline_timeout=20,
            max_children=1,
            memory_limit_bytes=MEMORY,
        )
    )
    observation.verify(SOURCE_A_INVENTORY, SOURCE_A)
    assert not errors and len(findings) == 1 and findings[0].id == SURVIVOR
    original_review = review(source)
    row = ruling(source, original_review, findings[0])
    cases = []

    def run_case(name, current, expected, eligible, source_bytes, inventory):
        observed = Observation(source, root / name)
        verdict = observed.run(current.run)
        observed.verify(inventory, source_bytes)
        saved = load_state(state / ".code-forge/state.json")
        shutil.copyfile(state / ".code-forge/state.json", observed.directory / "state.json")
        assert verdict is expected and saved.verdict is expected
        assert saved.converged is (expected is Verdict.PASS)
        assert current._mutation_measurement_eligible is eligible
        assert saved.infra_errors == ([] if eligible else ["L2: invalid bound mutation measurement"])
        survivors = [finding for finding in saved.findings if finding.id.startswith("mutant-")]
        assert {finding.id for finding in survivors} == {
            "mutant-" + key for key, value in inventory.items() if value == "survived"
        }
        assert all(
            finding.disposition
            is (Disposition.DISMISSED if expected is Verdict.PASS else Disposition.CONFIRMED)
            for finding in survivors
        )
        # In particular the same previously ruled survivor cannot inherit A's dismissal under B.
        assert any(finding.id == SURVIVOR for finding in survivors)
        notes = [finding for finding in saved.findings if finding.id == "MUTATION_SKIPPED"]
        assert len(notes) == 1 and notes[0].fingerprint == "mutation-unavailable"
        assert notes[0].disposition is Disposition.DISMISSED
        assert not (state / ".code-forge/mutation-result.json").exists()
        cases.append({"name": name, "verdict": verdict.value, "eligible": eligible})

    run_case(
        "unruled",
        machine(source, state, config, original_review),
        Verdict.FAIL,
        True,
        SOURCE_A,
        SOURCE_A_INVENTORY,
    )
    append_row(source, row)
    exact = machine(source, state, config, original_review)
    run_case("exact", exact, Verdict.PASS, True, SOURCE_A, SOURCE_A_INVENTORY)
    exact._state = deepcopy(exact._state)
    append_row(source, replace(row, terminal_state=TerminalState.UNADJUDICATED))
    run_case("revoked", exact, Verdict.FAIL, True, SOURCE_A, SOURCE_A_INVENTORY)
    append_row(source, row)
    for path in (source, state):
        (path / "calc.py").write_bytes(SOURCE_B)
    changed_review = review(source)
    assert changed_review.git_diff != original_review.git_diff
    run_case(
        "new-source-review",
        machine(source, state, config, changed_review),
        Verdict.FAIL,
        True,
        SOURCE_B,
        SOURCE_B_INVENTORY,
    )
    run_case(
        "stale-source-review",
        machine(source, state, config, original_review),
        Verdict.FAIL,
        False,
        SOURCE_B,
        SOURCE_B_INVENTORY,
    )
    assert original == (mutation.run_mutation, mutation.run_owned_command, subprocess.Popen)
    write_json(
        root / "summary.json",
        {
            "cases": cases,
            "measurement_count": 6,
            "owned_command_count": 36,
            "cleanup_complete": True,
            "python": sys.version,
            "pytest": version("pytest"),
            "mutmut": version("mutmut"),
        },
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())

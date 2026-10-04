"""Native generated identities and baseline evidence constrain results."""

import json
import errno
import os
import signal
import subprocess
import sys
import pytest
from code_forge import mutation
from code_forge._mutation_process import MutationProcessError
from code_forge.disposition import Disposition
from code_forge.machine import mutation_result_verdict
from code_forge.state import StateFinding, Verdict


def _write_inventory(root, codes=(1, 1), relative="src/calc.py"):
    mirror = root / "mutants" / relative
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(
        "\n".join(f"def x_allows__mutmut_{i}(n):\n    return n >= {i}" for i in range(1, len(codes) + 1))
    )
    module = relative.removesuffix(".py").replace("/", ".").removeprefix("src.")
    exits = {f"{module}.x_allows__mutmut_{i}": code for i, code in enumerate(codes, 1)}
    mirror.with_name(mirror.name + ".meta").write_text(json.dumps({"exit_code_by_key": exits}))
    return exits


@pytest.mark.parametrize("leaf", ["mirror", "meta"])
@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_inventory_nonregular_nodes_refuse_without_waiting_or_open_descriptors(tmp_path, leaf, kind):
    _write_inventory(tmp_path)
    mirror = tmp_path / "mutants/src/calc.py"
    selected = mirror if leaf == "mirror" else mirror.with_name(mirror.name + ".meta")
    selected.unlink()
    if kind == "fifo":
        os.mkfifo(selected)
    else:
        selected.mkdir()
    before = selected.lstat()
    descriptors = set(os.listdir("/proc/self/fd"))
    original = signal.signal(
        signal.SIGALRM,
        lambda *_: (_ for _ in ()).throw(TimeoutError("inventory blocked at nonregular node")),
    )
    signal.setitimer(signal.ITIMER_REAL, 0.25)
    try:
        with pytest.raises(ValueError, match="must use regular files"):
            mutation._mutation_inventory(str(tmp_path), ["src/calc.py"])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original)
    after = selected.lstat()
    assert (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode)
    assert set(os.listdir("/proc/self/fd")) == descriptors


@pytest.mark.parametrize("leaf", ["mirror", "meta"])
def test_inventory_symlink_published_at_open_is_not_followed(tmp_path, monkeypatch, leaf):
    _write_inventory(tmp_path)
    mirror = tmp_path / "mutants/src/calc.py"
    selected = mirror if leaf == "mirror" else mirror.with_name(mirror.name + ".meta")
    foreign = tmp_path / "foreign"
    foreign.write_bytes(selected.read_bytes())
    before = foreign.lstat()
    original = mutation.os.open
    opened = []

    def publish_link(path, flags, *args, **kwargs):
        if path == selected:
            selected.unlink()
            selected.symlink_to(foreign)
            opened.append(path)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(mutation.os, "open", publish_link)
    with pytest.raises(OSError) as caught:
        mutation._mutation_inventory(str(tmp_path), ["src/calc.py"])
    assert caught.value.errno == errno.ELOOP
    assert opened == [selected]
    after = foreign.lstat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    assert foreign.read_bytes() == selected.read_bytes()


@pytest.mark.parametrize("leaf", ["mirror", "meta"])
@pytest.mark.parametrize("change", ["replace", "parent-replace", "in-place"])
def test_inventory_read_is_bound_to_unchanged_regular_descriptor_and_name(
    tmp_path, monkeypatch, leaf, change
):
    _write_inventory(tmp_path)
    mirror = tmp_path / "mutants/src/calc.py"
    selected = mirror if leaf == "mirror" else mirror.with_name(mirror.name + ".meta")
    original_open = mutation.os.open
    original_read = mutation.os.read
    descriptors = []
    changes = []

    def observe(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if path == selected:
            descriptors.append(fd)
        return fd

    def change_after_read(fd, size):
        raw = original_read(fd, size)
        if fd in descriptors and not changes:
            if change == "replace":
                replacement = selected.with_name(selected.name + ".replacement")
                replacement.write_bytes(selected.read_bytes())
                replacement.replace(selected)
            elif change == "parent-replace":
                retired = selected.parent.with_name("retired-source")
                selected.parent.rename(retired)
                selected.parent.mkdir()
                for original in retired.iterdir():
                    (selected.parent / original.name).write_bytes(original.read_bytes())
            else:
                with selected.open("ab") as stream:
                    stream.write(b" ")
            changes.append(change)
        return raw

    monkeypatch.setattr(mutation.os, "open", observe)
    monkeypatch.setattr(mutation.os, "read", change_after_read)
    with pytest.raises(ValueError, match="changed while reading"):
        mutation._mutation_inventory(str(tmp_path), ["src/calc.py"])
    assert changes == [change] and len(descriptors) == 1
    with pytest.raises(OSError) as caught:
        os.fstat(descriptors[0])
    assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize(
    "case",
    [
        "killed",
        "survived",
        "empty",
        "truncated",
        "unknown",
        "extra",
        "contradictory",
        "missing-meta",
        "incomplete",
        "no-tests",
        "zero",
    ],
)
def test_native_transport_and_generated_metadata_must_reconcile(tmp_path, monkeypatch, case):
    commands = []

    def execute(argv, **kwargs):
        commands.append((argv, kwargs))
        output = ""
        if "run" in argv:
            codes = (
                ()
                if case == "zero"
                else (0, 0)
                if case in ("survived", "empty")
                else (None, 1)
                if case == "incomplete"
                else (33, 1)
                if case == "no-tests"
                else (1, 1)
            )
            _write_inventory(tmp_path, codes)
            if case == "missing-meta":
                (tmp_path / "mutants/src/calc.py.meta").unlink()
        if "results" in argv:
            assert argv[-3:] == ["results", "--all", "true"]
            status = "survived" if case in ("survived", "empty") else "killed"
            output = (
                ""
                if case in ("zero", "empty")
                else "\n".join(f"calc.x_allows__mutmut_{i}: {status}" for i in (1, 2))
            )
            if case == "truncated":
                output = output.splitlines()[0]
            if case == "unknown":
                output += "\ncalc.x_allows__mutmut_9: alien"
            if case == "extra":
                output += "\ncalc.x_allows__mutmut_9: killed"
            if case == "contradictory":
                output = output.replace("killed", "survived")
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    monkeypatch.setattr(
        mutation.subprocess, "run", lambda *_a, **_k: pytest.fail("owned executor was bypassed")
    )
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"],
        [sys.executable, "-m", "pytest"],
        cwd=tmp_path,
        _evidence=evidence,
    )
    assert not (tmp_path / "mutants").exists()
    if case in ("killed", "survived"):
        assert not infra and evidence["baseline_passed"]
        assert len(evidence["inventory"]) == 2
        assert len(findings) == (2 if case == "survived" else 0)
        assert len(commands) == 6
    elif case == "zero":
        assert not evidence["baseline_passed"]
        assert findings[0].fingerprint == "mutation-no-mutants"
        assert not infra
    else:
        assert infra and findings[0].id == "MUTATION_ERROR"
        assert not evidence["baseline_passed"]


@pytest.mark.parametrize("site", ["baseline", "probe", "run", "results"])
def test_cleanup_failure_is_infra_and_withholds_active_workspace(tmp_path, monkeypatch, site):
    def execute(argv, **kwargs):
        current = (
            "run"
            if "run" in argv
            else "results"
            if "results" in argv
            else "probe"
            if "-c" in argv
            else "baseline"
        )
        if current == "run":
            _write_inventory(tmp_path)
        if current == site:
            raise MutationProcessError("KNOWN_INCOMPLETE_CLEANUP", cleanup_complete=False)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert findings[0].id == "MUTATION_ERROR" and infra[0] == "KNOWN_INCOMPLETE_CLEANUP"
    assert "quarantine=" in infra[-1]
    assert not evidence["baseline_passed"]
    if site in ("run", "results"):
        assert (tmp_path / "setup.cfg").is_file()
        assert (tmp_path / "mutants").is_dir()


def test_both_guard_calls_forward_the_owned_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/not-a-real-env")
    seen = []

    def execute(argv, **kwargs):
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(
            argv, 1, "No module named pytest" if len(seen) == 1 else "KNOWN_TEST_FAILURE", ""
        )

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    monkeypatch.setattr(
        mutation.subprocess, "run", lambda *_a, **_k: pytest.fail("owned retry was bypassed")
    )
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], [sys.executable, "-m", "pytest"], cwd=tmp_path
    )
    assert len(seen) == 2
    assert "VIRTUAL_ENV" in seen[0] and "VIRTUAL_ENV" not in seen[1]
    assert findings[0].id == "MUTATION_SKIPPED" and infra


@pytest.mark.parametrize("selection", ["relative", "absolute", "spaces", "windows-separators"])
def test_python_selection_uses_lexical_execution_relative_paths(tmp_path, monkeypatch, selection):
    relative = "src/calc space.py" if selection == "spaces" else "src/calc.py"
    selected = (
        str(tmp_path / relative)
        if selection in ("absolute", "spaces")
        else relative.replace("/", "\\")
        if selection == "windows-separators"
        else relative
    )
    commands = []

    def execute(argv, **kwargs):
        commands.append(argv)
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"]["PYTHONPATH"] == str(tmp_path / "src")
        if "run" in argv:
            config = (tmp_path / "setup.cfg").read_text()
            assert f"only_mutate={relative}\n" in config
            assert str(tmp_path) not in config
            _write_inventory(tmp_path, relative=relative)
        output = (
            "\n".join(
                f"{relative.removesuffix('.py').replace('/', '.').removeprefix('src.')}.x_allows__mutmut_{i}: killed"
                for i in (1, 2)
            )
            if "results" in argv
            else ""
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        [selected], [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert findings == [] and infra == [] and evidence["baseline_passed"]
    assert len(commands) == 6


@pytest.mark.parametrize(
    "kind",
    [
        "outside-absolute",
        "outside-relative",
        "file-link",
        "parent-link",
        "root-link",
        "mirror-root",
        "mirror-parent",
        "mirror-file",
        "mirror-meta",
    ],
)
def test_refused_source_boundaries_launch_no_commands(tmp_path, monkeypatch, kind):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def source(): return 1\n")
    selected = str(outside) if kind == "outside-absolute" else "../outside.py"
    if kind == "file-link":
        (root / "linked.py").symlink_to(outside)
        selected = "linked.py"
    elif kind == "parent-link":
        (root / "linked").symlink_to(tmp_path, target_is_directory=True)
        selected = "linked/outside.py"
    elif kind == "root-link":
        linked = tmp_path / "linked-root"
        linked.symlink_to(root, target_is_directory=True)
        root, selected = linked, "source.py"
    elif kind.startswith("mirror-"):
        selected = "src/calc.py"
        mirror = root / "mutants" / selected
        if kind == "mirror-root":
            (root / "mutants").symlink_to(tmp_path, target_is_directory=True)
        elif kind == "mirror-parent":
            mirror.parent.parent.mkdir()
            mirror.parent.symlink_to(tmp_path, target_is_directory=True)
        else:
            mirror.parent.mkdir(parents=True)
            path = mirror if kind == "mirror-file" else mirror.with_name("calc.py.meta")
            path.symlink_to(outside)
    monkeypatch.setattr(
        mutation, "run_owned_command", lambda *_a, **_k: pytest.fail("refused source launched a command")
    )
    evidence = {}
    findings, infra = mutation.run_mutation([selected], ["pytest"], cwd=root, _evidence=evidence)
    assert findings[0].id == "MUTATION_ERROR" and infra
    assert not evidence["baseline_passed"] and evidence["infra_errors"] == infra
    assert not (root / "setup.cfg").exists()
    assert outside.read_text() == "def source(): return 1\n"


def test_unknown_guard_status_cannot_prove_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(mutation, "_run_baseline_guard", lambda *_a, **_k: ("invented-success", [], []))
    monkeypatch.setattr(
        mutation,
        "run_owned_command",
        lambda *_a, **_k: pytest.fail("unknown baseline launched a command"),
    )
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], ["pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert findings[0].id == "MUTATION_ERROR" and "unknown status" in infra[0]
    assert evidence["baseline_passed"] is False


@pytest.mark.parametrize(
    "case",
    [
        "bool",
        "unknown-code",
        "timeout",
        "skipped",
        "interrupted",
        "suspicious",
        "segfault",
        "no-tests",
        "truncated-identities",
        "duplicate-json",
        "non-object",
        "syntax",
        "mirror-link",
        "meta-link",
    ],
)
def test_generated_metadata_refuses_incomplete_or_unbound_results(tmp_path, case):
    _write_inventory(tmp_path)
    mirror = tmp_path / "mutants/src/calc.py"
    meta = mirror.with_name("calc.py.meta")
    codes = {
        "bool": True,
        "unknown-code": 900,
        "timeout": 36,
        "skipped": 34,
        "interrupted": 2,
        "suspicious": 35,
        "segfault": -9,
        "no-tests": 33,
    }
    if case in codes:
        data = json.loads(meta.read_text())
        data["exit_code_by_key"]["calc.x_allows__mutmut_1"] = codes[case]
        meta.write_text(json.dumps(data))
    elif case == "truncated-identities":
        meta.write_text('{"exit_code_by_key":{"calc.x_allows__mutmut_1":1}}')
    elif case == "duplicate-json":
        data = meta.read_text()
        meta.write_text('{"exit_code_by_key":{},' + data[1:])
    elif case == "non-object":
        meta.write_text("[]")
    elif case == "syntax":
        mirror.write_text("def invalid syntax")
    elif case in ("mirror-link", "meta-link"):
        path = mirror if case == "mirror-link" else meta
        target = tmp_path / "unbound"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises((ValueError, TypeError, SyntaxError)):
        mutation._mutation_inventory(str(tmp_path), ["src/calc.py"])


@pytest.mark.parametrize(
    "stdout, fragment",
    [
        ("malformed", "unparseable"),
        ("calc.x_allows__mutmut_1: alien", "unknown"),
        ("calc.x_allows__mutmut_1: killed\ncalc.x_allows__mutmut_1: killed", "duplicate"),
    ],
)
def test_result_parser_does_not_dismiss_transport_damage(stdout, fragment):
    survivors, warnings = mutation.parse_mutmut_results(stdout)
    assert survivors == [] and fragment in warnings[0]


def test_inventory_absolute_identity_is_bound_to_its_root_and_parent_paths_refuse(tmp_path):
    _write_inventory(tmp_path)
    inventory, _ = mutation._mutation_inventory(str(tmp_path), [str(tmp_path / "src/calc.py")])
    assert len(inventory) == 2 and set(inventory.values()) == {"killed"}
    with pytest.raises(ValueError, match="outside the project root"):
        mutation._mutation_inventory(str(tmp_path), ["../unbound.py"])


@pytest.mark.parametrize("deletion", ["worktree", "index", "spaces"])
def test_mixed_diff_omits_only_git_proven_absent_sources(tmp_path, monkeypatch, deletion):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout

    deleted = "src/deleted space.py" if deletion == "spaces" else "src/deleted.py"
    (tmp_path / "src").mkdir()
    (tmp_path / "src/calc.py").write_text("def allows(n): return n > 1\n")
    (tmp_path / deleted).write_text("def deleted(): return 1\n")
    git("init", "-q")
    git("add", "src")
    git("commit", "-qm", "fixture")
    (tmp_path / "src/calc.py").write_text("def allows(n): return n >= 1\n")
    (tmp_path / deleted).unlink()
    if deletion == "index":
        git("add", "-u")
    selected = git("diff", "--name-only", "-z", "HEAD").split("\0")[:-1]
    assert set(selected) == {"src/calc.py", deleted}
    commands = []
    owned = mutation.run_owned_command

    def execute(argv, **kwargs):
        commands.append(argv)
        if argv[0] == "git":
            return owned(argv, **kwargs)
        if "run" in argv:
            assert "only_mutate=src/calc.py\n" in (tmp_path / "setup.cfg").read_text()
            assert deleted not in (tmp_path / "setup.cfg").read_text()
            _write_inventory(tmp_path)
        output = (
            "calc.x_allows__mutmut_1: killed\ncalc.x_allows__mutmut_2: killed"
            if "results" in argv
            else ""
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        selected, [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert findings == [] and infra == [] and evidence["baseline_passed"]
    assert set(evidence["metadata_sha256"]) == {"src/calc.py"}
    assert len(commands) == 7
    assert not (tmp_path / "mutants").exists()


@pytest.mark.parametrize(
    "condition", ["existing", "untracked-missing", "restored-index-deletion", "restored-during-query"]
)
def test_missing_inventory_is_not_excused_by_source_filtering(tmp_path, monkeypatch, condition):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )

    (tmp_path / "src").mkdir()
    (tmp_path / "src/calc.py").write_text("def allows(n): return n >= 1\n")
    missing = tmp_path / "src/missing.py"
    if condition != "untracked-missing":
        missing.write_text("def missing(): return 1\n")
    git("init", "-q")
    git("add", "src")
    git("commit", "-qm", "fixture")
    if condition in ("restored-index-deletion", "restored-during-query"):
        git("rm", "src/missing.py")
        if condition == "restored-index-deletion":
            missing.write_text("def missing(): return 2\n")
    owned = mutation.run_owned_command
    deletion_queries = []

    def execute(argv, **kwargs):
        if argv[0] == "git":
            deletion_queries.append(argv)
            result = owned(argv, **kwargs)
            if condition == "restored-during-query":
                missing.write_text("def missing(): return 2\n")
            return result
        if "run" in argv:
            _write_inventory(tmp_path)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py", "src/missing.py"],
        [sys.executable, "-m", "pytest"],
        cwd=tmp_path,
        _evidence=evidence,
    )
    assert findings[0].id == "MUTATION_ERROR" and infra
    assert not evidence["baseline_passed"]
    assert "missing.py" in infra[0]
    assert len(deletion_queries) == int(condition in ("untracked-missing", "restored-during-query"))


@pytest.mark.parametrize("failure", ["nonzero", "missing-git", "timeout", "truncated", "empty"])
def test_uncertain_git_deletion_evidence_keeps_required_inventory(tmp_path, monkeypatch, failure):
    (tmp_path / ".git").mkdir()
    deletion_queries = []

    def query(argv, **kwargs):
        deletion_queries.append(argv)
        assert "--diff-filter=D" in argv and "-z" in argv
        assert kwargs["timeout"] == 5 and not kwargs["check"]
        if failure == "missing-git":
            raise FileNotFoundError("missing git")
        if failure == "timeout":
            error = subprocess.TimeoutExpired(argv, 5)
            error.ownership = {"cleanup_complete": True, "timed_out": True, "owned": []}
            raise error
        output = (
            "src/deleted.py\0"
            if failure == "nonzero"
            else "src/deleted.py"
            if failure == "truncated"
            else ""
        )
        return subprocess.CompletedProcess(argv, 1 if failure == "nonzero" else 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", query)
    assert mutation._without_deleted_sources(["src/deleted.py"], tmp_path) == ["src/deleted.py"]
    assert len(deletion_queries) == 1


def test_source_access_failure_is_not_a_known_deletion(tmp_path, monkeypatch):
    existing = tmp_path / "src/existing.py"
    existing.parent.mkdir()
    existing.write_text("value = 1\n")
    path = tmp_path / "src/deleted.py"
    original = mutation.Path.lstat
    observed = []

    def denied(value):
        observed.append(value)
        if value == path:
            raise PermissionError("source access denied")
        return original(value)

    monkeypatch.setattr(mutation.Path, "lstat", denied)
    with pytest.raises(PermissionError, match="source access denied"):
        mutation._without_deleted_sources(["src/existing.py", "src/deleted.py"], tmp_path)
    assert observed == [existing, path]


def test_results_timeout_restores_workspace_without_proving_clean_result(tmp_path, monkeypatch):
    def execute(argv, **kwargs):
        if "run" in argv:
            _write_inventory(tmp_path)
        if "results" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert not infra and not evidence["baseline_passed"]
    assert findings[0].fingerprint == "mutation-results-timeout"
    assert not (tmp_path / "setup.cfg").exists() and not (tmp_path / "mutants").exists()


def test_missing_baseline_runner_retains_default_guard_skip_and_false_proof(tmp_path, monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("MEASURED_MISSING_RUNNER")

    monkeypatch.setattr(mutation, "run_owned_command", missing)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], ["missing-runner"], cwd=tmp_path, _evidence=evidence
    )
    assert findings[0].fingerprint == "mutation-flaky" and "runner not found" in infra[0]
    assert not evidence["baseline_passed"]
    assert not list((tmp_path / ".code-forge").glob("mutation-config-*"))


def test_missing_python_probe_is_a_soft_dependency_skip_without_measured_baseline(monkeypatch):
    monkeypatch.setattr(
        mutation,
        "run_owned_command",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("MEASURED_MISSING_PYTHON")),
    )
    assert mutation._resolve_mutmut_invocation(["/missing/bin/python", "-m", "pytest"]) is None


@pytest.mark.parametrize("outcome", ["clean", "survivor", "skip", "infra", "unproven", "exception"])
def test_generated_producer_keeps_actual_proof_and_failure_evidence(
    tmp_path, monkeypatch, run_detached_payload, outcome
):
    captured = []

    def spawn(argv, **kwargs):
        captured.append(argv[2])
        return type("Child", (), {"pid": 1234, "wait": lambda self, timeout=None: 0})()

    monkeypatch.setattr(mutation.subprocess, "Popen", spawn)
    result_path = tmp_path / "result.json"
    assert mutation.launch_detached_mutation(["src/calc.py"], ["pytest"], tmp_path, result_path)

    def execute(**kwargs):
        if outcome == "exception":
            raise RuntimeError("KNOWN_NATIVE_EXCEPTION")
        if outcome in ("clean", "survivor"):
            kwargs["_evidence"].update(
                baseline_passed=True,
                inventory={"measured": "survived" if outcome == "survivor" else "killed"},
            )
        finding = StateFinding(
            id="MUTATION_SKIPPED" if outcome == "skip" else "mutant-example",
            fingerprint="example",
            source="MUTANT",
            disposition=Disposition.DISMISSED if outcome == "skip" else Disposition.CONFIRMED,
            file="",
            line_range=[],
            description="KNOWN_SKIP" if outcome == "skip" else "survived",
        )
        return (
            [finding] if outcome in ("skip", "survivor") else [],
            ["KNOWN_INFRA"] if outcome == "infra" else [],
        )

    monkeypatch.setattr(mutation, "run_mutation", execute)
    run_detached_payload(captured[0])
    data = json.loads(result_path.read_text())
    if outcome in ("clean", "survivor"):
        assert data["status"] == "done" and data["baseline_passed"] is True
        assert data["inventory"]
        assert mutation_result_verdict(data) is (Verdict.FAIL if outcome == "survivor" else None)
    else:
        assert data["status"] == "error" and data["baseline_passed"] is False
        assert data["message"]
        if outcome == "skip":
            assert data["skipped"] == ["KNOWN_SKIP"]
        if outcome == "infra":
            assert data["infra_errors"] == ["KNOWN_INFRA"]
        if outcome == "exception":
            assert data["infra_errors"] == ["KNOWN_NATIVE_EXCEPTION"]
            assert data["skipped"] == [] and data["inventory"] == {}

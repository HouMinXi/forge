# SPDX-License-Identifier: Apache-2.0
"""scripts/lint_changed.py: diff-scoped ruff selection and the CI wiring (#105)."""

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

import pytest
import yaml


def _repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    if (here.parents[1] / "scripts" / "lint_changed.py").exists():
        return here.parents[1]
    # Under mutmut the suite runs from a mutants/ copy without scripts/.
    for parent in here.parents:
        if parent.name == "mutants":
            return parent.parent
    return here.parents[1]


ROOT = _repo_root()
SCRIPT = ROOT / "scripts" / "lint_changed.py"


def _load():
    spec = importlib.util.spec_from_file_location("lint_changed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_changed = _load()


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
    )
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout


def _write(repo, name, text="x = 1\n"):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for name in ("keep.py", "gone.py", "moved.py", "docs.md"):
        _write(root, name, "value = 1\n# " + name + " padding line one\n# padding line two\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "topic")
    return root


def _commit(repo, message="change"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def test_selects_added_modified_and_renamed_python_files_only(repo):
    _write(repo, "added.py")
    _write(repo, "pkg/stub.pyi")
    _write(repo, "keep.py", "value = 2\n# keep.py padding line one\n# padding line two\n")
    _git(repo, "mv", "moved.py", "renamed.py")
    _write(repo, "notes.md", "# notes\n")
    _write(repo, "script.sh", "echo hi\n")
    _write(repo, "foo.py.bak", "x = 1\n")
    _write(repo, "stub.pyi.orig", "x: int\n")
    _write(repo, "pyfile", "x = 1\n")
    _commit(repo)

    assert lint_changed.changed_python_files("main", cwd=repo) == [
        "added.py",
        "keep.py",
        "pkg/stub.pyi",
        "renamed.py",
    ]


def test_deleted_python_files_are_not_selected(repo):
    _git(repo, "rm", "-q", "gone.py")
    _commit(repo)

    assert lint_changed.changed_python_files("main", cwd=repo) == []


def test_unchanged_branch_selects_nothing(repo):
    assert lint_changed.changed_python_files("main", cwd=repo) == []


def test_diff_is_taken_against_the_merge_base(repo):
    # An edit that lands on the target branch after the topic branched must
    # not show up as a change made by the topic (a two-dot diff would list it).
    _git(repo, "checkout", "-q", "main")
    _write(repo, "keep.py", "value = 3\n# main edit\n")
    _commit(repo, "main moves on")
    _git(repo, "checkout", "-q", "topic")
    _write(repo, "topic_only.py")
    _commit(repo)

    assert lint_changed.changed_python_files("main", cwd=repo) == ["topic_only.py"]


def test_non_ascii_and_spaced_names_survive_selection(repo):
    _write(repo, "dir with space/módulo.py")
    _commit(repo)

    assert lint_changed.changed_python_files("main", cwd=repo) == ["dir with space/módulo.py"]


def test_file_replaced_by_a_symlink_is_not_selected(repo):
    (repo / "keep.py").unlink()
    try:
        (repo / "keep.py").symlink_to("moved.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    _commit(repo)

    assert "T\tkeep.py" in _git(repo, "diff", "--name-status", "--no-renames", "main...HEAD")
    assert lint_changed.changed_python_files("main", cwd=repo) == []


def test_unknown_base_ref_exits_with_git_message(repo):
    with pytest.raises(SystemExit) as excinfo:
        lint_changed.changed_python_files("origin/does-not-exist", cwd=repo)

    message = str(excinfo.value)
    assert excinfo.value.code != 0
    assert message.startswith("lint_changed: git diff origin/does-not-exist...HEAD failed")
    assert "fatal:" in message
    assert "origin/does-not-exist" in message.split("fatal:", 1)[1]


def test_missing_merge_base_exits_with_git_message(repo):
    _git(repo, "checkout", "-q", "--orphan", "unrelated")
    _write(repo, "other.py")
    _commit(repo, "unrelated history")

    with pytest.raises(SystemExit) as excinfo:
        lint_changed.changed_python_files("main", cwd=repo)

    assert excinfo.value.code != 0
    assert "no merge base" in str(excinfo.value)


def test_option_like_base_is_never_read_as_a_git_option(repo, tmp_path):
    target = tmp_path / "written-by-git"

    with pytest.raises(SystemExit) as excinfo:
        lint_changed.changed_python_files(f"--output={target}", cwd=repo)

    assert excinfo.value.code != 0
    assert not target.exists()


@pytest.mark.parametrize("template", ["--output={target}", "-O{target}", "-x"])
def test_option_like_base_is_rejected_on_the_command_line(tmp_path, capsys, template):
    target = tmp_path / "written-by-git"

    with pytest.raises(SystemExit) as excinfo:
        lint_changed.main([f"--base={template.format(target=target)}"])

    assert excinfo.value.code == 2
    assert "base must not start with '-'" in capsys.readouterr().err
    assert not target.exists()


class _Recorder:
    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.commands = []

    def __call__(self, command):
        self.commands.append(list(command))
        return self.statuses.pop(0)


def test_empty_selection_never_invokes_ruff(capsys):
    runner = _Recorder()

    assert lint_changed.run_ruff([], runner) == 0
    assert runner.commands == []
    assert "nothing to lint" in capsys.readouterr().out


def test_both_ruff_commands_receive_the_files_after_a_separator():
    runner = _Recorder(0, 0)

    assert lint_changed.run_ruff(["a.py", "-b.py"], runner) == 0
    assert runner.commands == [
        [sys.executable, "-m", "ruff", "check", "--force-exclude", "--", "a.py", "-b.py"],
        [sys.executable, "-m", "ruff", "format", "--check", "--force-exclude", "--", "a.py", "-b.py"],
    ]


@pytest.mark.parametrize("statuses", [(1, 0), (0, 1), (1, 1), (2, 0)])
def test_either_failure_fails_and_both_commands_still_run(statuses):
    runner = _Recorder(*statuses)

    assert lint_changed.run_ruff(["a.py"], runner) != 0
    assert len(runner.commands) == 2


def test_pin_is_one_exact_ruff_version_in_the_dev_extra():
    requirement = lint_changed.ruff_requirement(ROOT / "pyproject.toml")

    assert re.fullmatch(r"ruff==\d+\.\d+\.\d+", requirement)


def _pyproject(tmp_path, dev):
    path = tmp_path / "pyproject.toml"
    items = ", ".join(f'"{item}"' for item in dev)
    path.write_text(
        f'[project]\nname = "x"\n[project.optional-dependencies]\ndev = [{items}]\n',
        encoding="utf-8",
    )
    return path


def test_pin_is_found_among_other_dev_requirements(tmp_path):
    path = _pyproject(tmp_path, ["pytest==9.1.1", "ruff==0.17.3", "jsonschema>=4.18"])

    assert lint_changed.ruff_requirement(path) == "ruff==0.17.3"


@pytest.mark.parametrize("dev", [["ruff>=0.5"], ["pytest==9.1.1"], ["ruff==0.17.3", "ruff==0.17.2"]])
def test_unpinned_missing_or_duplicate_ruff_is_rejected(tmp_path, dev):
    with pytest.raises(SystemExit):
        lint_changed.ruff_requirement(_pyproject(tmp_path, dev))


def test_print_requirement_prints_the_pin_and_does_not_lint(capsys, monkeypatch):
    monkeypatch.setattr(
        lint_changed,
        "changed_python_files",
        lambda *a, **k: pytest.fail("must not diff when only printing the pin"),
    )

    assert lint_changed.main(["--print-requirement"]) == 0
    assert capsys.readouterr().out.strip() == lint_changed.ruff_requirement(ROOT / "pyproject.toml")


@pytest.mark.parametrize("base", ["origin/main", "origin/release-1.x", "HEAD~3"])
def test_main_lints_the_files_changed_against_the_base(monkeypatch, base):
    seen = {}

    def fake_changed(passed_base):
        seen["base"] = passed_base
        return ["a.py"]

    def fake_run(files):
        seen["files"] = files
        return 7

    monkeypatch.setattr(lint_changed, "changed_python_files", fake_changed)
    monkeypatch.setattr(lint_changed, "run_ruff", fake_run)

    assert lint_changed.main(["--base", base]) == 7
    assert seen == {"base": base, "files": ["a.py"]}


def test_real_runner_returns_the_exit_status_instead_of_raising():
    assert lint_changed._subprocess_runner([sys.executable, "-c", "raise SystemExit(3)"]) == 3


def test_real_runner_runs_in_the_repository_root(capfd, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    command = [sys.executable, "-c", "import os; print(os.getcwd())"]

    assert lint_changed._subprocess_runner(command) == 0
    assert pathlib.Path(capfd.readouterr().out.strip()).resolve() == lint_changed.REPO_ROOT


@pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="ruff is not installed")
def test_real_ruff_honours_the_exclude_list_for_explicit_paths(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.ruff]\nexclude = ["skipped.py"]\n', encoding="utf-8")
    (tmp_path / "skipped.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "dirty.py").write_text("import os\n", encoding="utf-8")

    def runner(command):
        return subprocess.run(command, cwd=tmp_path, check=False, capture_output=True).returncode

    assert lint_changed.run_ruff(["skipped.py", "clean.py"], runner) == 0
    assert lint_changed.run_ruff(["dirty.py"], runner) != 0


def test_main_requires_a_mode():
    with pytest.raises(SystemExit):
        lint_changed.main([])


def _workflow():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "lint-changed.yml").read_text("utf-8"))


def test_workflow_runs_on_pull_requests_with_full_history_and_the_pinned_ruff():
    workflow = _workflow()
    steps = workflow["jobs"]["lint-changed"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    runs = [s["run"] for s in steps if "run" in s]

    assert workflow["on"] == {"pull_request": {"branches": ["main"]}}
    assert checkout["with"]["fetch-depth"] == 0
    assert any("--print-requirement" in run and "pip install" in run for run in runs)
    lint = next(s for s in steps if "--base" in s.get("run", ""))
    assert lint["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert lint["run"].strip() == 'python scripts/lint_changed.py --base "origin/$BASE_REF"'


def test_workflow_never_lints_the_whole_tree():
    text = (ROOT / ".github" / "workflows" / "lint-changed.yml").read_text("utf-8")

    assert "ruff check ." not in text
    assert "ruff format ." not in text


def _job():
    return _workflow()["jobs"]["lint-changed"]


def test_workflow_is_read_only_and_does_not_keep_credentials():
    workflow = _workflow()
    steps = _job()["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))

    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in _job()
    assert checkout["with"]["persist-credentials"] is False


def test_workflow_failures_are_not_swallowed():
    job = _job()

    assert job.get("continue-on-error", False) is False
    assert all(step.get("continue-on-error", False) is False for step in job["steps"])


def test_workflow_actions_are_pinned_to_commit_shas():
    uses = [step["uses"] for step in _job()["steps"] if "uses" in step]

    assert len(uses) == 2
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", use) for use in uses)


def test_workflow_job_settings_are_pinned():
    job = _job()
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@"))

    assert job["name"] == "Lint changed Python files"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 10
    assert setup["with"]["python-version"] == "3.12"


def test_workflow_cancels_superseded_runs_per_pull_request():
    concurrency = _workflow()["concurrency"]

    assert concurrency == {
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True,
    }

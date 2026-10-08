"""The harness must not be able to fail quietly.

Found running the first real evaluation (Phase 57-6). Three defects
compounded into a result that looked entirely normal: the trust grant was
written one directory above where the child reads it, so the harness
backend was discarded as untrusted; the child then fell back to the user
config, where that backend does not exist; and the resulting non-zero exit
was read as the reviewer's verdict rather than as a setup failure.

The visible symptom was a reviewer that found nothing, on a run where the
reviewer was never invoked. Every one of the suite's other tests was green
throughout.
"""

import os
import pathlib
import subprocess

import pytest

import code_forge.eval.runner as runner
from code_forge.trust import _trust_store_path


class TestTrustDirectoryMatchesTheReader:
    def test_the_grant_lands_where_the_child_looks_for_it(self):
        # The child resolves its store from XDG_CONFIG_HOME. Whatever
        # directory the runner hands record_trust must be the same one
        # that expression produces, or the grant is invisible.
        xdg = pathlib.Path("/tmp/example-repo/.xdg-config")
        expected = _trust_store_path(xdg / "code-forge")
        assert expected.parent.name == "code-forge"
        assert expected == xdg / "code-forge" / "trusted.json"

    def test_the_bare_xdg_dir_is_the_wrong_answer(self):
        # Pinning the mistake itself: xdg_dir/trusted.json is one level
        # above the reader, which is exactly what shipped.
        xdg = pathlib.Path("/tmp/example-repo/.xdg-config")
        assert _trust_store_path(xdg) != _trust_store_path(xdg / "code-forge")

    def test_the_runner_actually_grants_where_the_child_reads(self, monkeypatch, tmp_path):
        """The arithmetic above is not the bug; passing the wrong dir was.

        An injection reverting record_trust's config_dir to the bare
        xdg_dir left the two tests above green, because neither one runs
        the code that chooses it. This one does: it captures what the
        runner hands record_trust and resolves it the way the child will.
        """
        seen = {}
        monkeypatch.setattr(
            runner,
            "record_trust",
            lambda path, data, config_dir=None: seen.update(dir=config_dir),
        )
        monkeypatch.setattr(
            runner,
            "_run_review",
            lambda cmd, temp_dir, env, timeout_s: (0, ""),
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "m.py").write_text("a = 1\n", encoding="utf-8")
        diff = tmp_path / "d.diff"
        diff.write_text(
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
            encoding="utf-8",
        )
        runner._run_single(_stub_entry(), diff, str(repo), "harness")

        granted = _trust_store_path(seen["dir"])
        # What the child computes from the XDG_CONFIG_HOME the runner sets.
        expected = _trust_store_path(repo / ".xdg-config" / "code-forge")
        assert granted == expected


class TestSetupFailureIsNotAVerdict:
    """A child that never reviewed must not be scored as if it had."""

    def _run(self, monkeypatch, tmp_path, stderr_text, returncode=2):
        monkeypatch.setattr(
            runner,
            "_run_review",
            lambda cmd, temp_dir, env, timeout_s: (returncode, stderr_text),
        )
        monkeypatch.setattr(runner, "_create_gate_yaml", lambda *a, **kw: _stub_gate(tmp_path))
        monkeypatch.setattr(runner, "record_trust", lambda *a, **kw: None)

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "m.py").write_text("a = 1\n", encoding="utf-8")
        diff = tmp_path / "d.diff"
        diff.write_text(
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
            encoding="utf-8",
        )
        entry = _stub_entry()
        return runner._run_single(entry, diff, str(repo), "harness")

    def test_an_untrusted_gate_is_infra_not_a_hold(self, monkeypatch, tmp_path):
        flagged, reason = self._run(
            monkeypatch,
            tmp_path,
            "Untrusted repo backends ignored. Run 'code-forge trust' to enable.\n",
        )
        assert flagged is False
        assert "no state.json" in reason

    def test_a_missing_backend_is_infra_not_a_hold(self, monkeypatch, tmp_path):
        flagged, reason = self._run(
            monkeypatch,
            tmp_path,
            "code-forge: error: unknown backend 'harness' (configured: deepseek)\n",
        )
        assert flagged is False
        assert "no state.json" in reason


def _stub_entry():
    from code_forge.eval.corpus import CorpusEntry

    return CorpusEntry(
        name="e",
        diff_file="d.diff",
        expected_verdict="HOLD",
        expected_advisory=[],
        expected_findings=[],
        axis_tags=[],
    )


def _stub_gate(tmp_path):
    p = tmp_path / ".code-forge" / "gate.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("backends: {}\n", encoding="utf-8")
    return p


class TestL0DoesNotBlockTheReview:
    """The fourth layer of the same failure: no tools.yaml, no review.

    forge probes for linters when tools.yaml is absent and raises "No
    toolchain detected" before reviewing anything. In a scratch repo
    holding one reconstructed file there is nothing to detect, so every
    entry died there -- scored, once again, as a reviewer that found
    nothing.
    """

    def test_the_harness_writes_a_registry_detection_accepts(self, tmp_path):
        from code_forge.registry import load_registry

        runner._create_gate_yaml(tmp_path, "harness", {"type": "api", "model": "m"})
        path = tmp_path / ".code-forge" / "tools.yaml"
        # Non-empty is the actual requirement: detect.py falls through to
        # detection when the registry loads empty, which an empty dict does.
        assert load_registry(str(path))

    def test_it_does_not_lint_the_corpus(self, tmp_path):
        import yaml as _yaml

        runner._create_gate_yaml(tmp_path, "harness", {"type": "api", "model": "m"})
        data = _yaml.safe_load((tmp_path / ".code-forge" / "tools.yaml").read_text(encoding="utf-8"))
        # Reconstructed base files are not valid Python; a real linter here
        # would report the corpus's own construction as false positives.
        for name, cfg in data["tools"].items():
            assert cfg["command"] == "true", name
            assert cfg["file_patterns"] == ["*.nomatch"], name

    def test_a_tools_yaml_from_the_diff_wins(self, tmp_path):
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir(parents=True)
        (gate_dir / "tools.yaml").write_text("tools: {mine: {}}\n", encoding="utf-8")
        runner._create_gate_yaml(tmp_path, "harness", {"type": "api", "model": "m"})
        assert "mine" in (gate_dir / "tools.yaml").read_text(encoding="utf-8")


class TestStateJsonIsTheSignal:
    """The classifier asks whether a review happened, not how it phrased failing.

    Review t_3848264c enumerated what the earlier substring matching still
    let through -- AuthenticationError, RateLimitError, InsufficientQuota,
    BadRequestError, a busy lock, a missing credential -- each exiting
    non-zero having written nothing, each scored as a HOLD no reviewer
    produced. Anticipating wording is not a strategy that terminates;
    the file's presence answers the question outright.
    """

    def _run(self, monkeypatch, tmp_path, stderr_text, write_state, returncode=2):
        def fake_review(cmd, temp_dir, env, timeout_s):
            if write_state:
                p = pathlib.Path(temp_dir) / ".code-forge"
                p.mkdir(parents=True, exist_ok=True)
                (p / "state.json").write_text('{"findings": []}', encoding="utf-8")
            return returncode, stderr_text

        monkeypatch.setattr(runner, "_run_review", fake_review)
        monkeypatch.setattr(runner, "record_trust", lambda *a, **kw: None)

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "m.py").write_text("a = 1\n", encoding="utf-8")
        diff = tmp_path / "d.diff"
        diff.write_text(
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
            encoding="utf-8",
        )
        return runner._run_single(_stub_entry(), diff, str(repo), "harness")

    @pytest.mark.parametrize(
        "stderr",
        [
            "openai.AuthenticationError: invalid api key\n",
            "openai.RateLimitError: 429 too many requests\n",
            "InsufficientQuota: credit balance is too low\n",
            "BadRequestError: model not found\n",
            "another code-forge holds the lock on this repo\n",
            "code-forge: error: MIMO_PRO_API_KEY is not set\n",
            "",  # no stderr at all, which no substring rule can catch
        ],
    )
    def test_a_child_that_never_reviewed_is_infra(self, monkeypatch, tmp_path, stderr):
        flagged, reason = self._run(monkeypatch, tmp_path, stderr, write_state=False)
        assert flagged is False
        assert "no state.json" in reason

    def test_a_real_hold_survives_infra_wording_in_its_findings(self, monkeypatch, tmp_path):
        """Reviewing networking code makes this collision likely, not exotic."""
        flagged, reason = self._run(
            monkeypatch,
            tmp_path,
            "1 CONFIRMED: retry loop swallows Connection refused\n",
            write_state=True,
        )
        assert flagged is True
        assert reason == ""

    def test_a_genuine_outage_keeps_its_specific_reason(self, monkeypatch, tmp_path):
        flagged, reason = self._run(
            monkeypatch,
            tmp_path,
            "APIConnectionError: Connection refused\n",
            write_state=False,
        )
        assert flagged is False
        assert "backend failure" in reason


class TestUnknownBackendFailsBeforeTheCorpus:
    """A typo must not run 150 entries against a port nothing listens on.

    Without this, an unresolvable --backend fell through to the harness's
    placeholder (localhost:0), and the operator was told the reviewer
    could not connect -- not that the backend they named does not exist.
    """

    def _eval(self, tmp_path, backend, monkeypatch, capsys):
        import argparse

        from code_forge import cli

        gate = tmp_path / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(
            "backends:\n  real:\n    type: api\n    format: openai\n    model: m\n"
            "    base_url: https://example.invalid/v1\n"
            "    api_key_env: EXAMPLE_KEY\n",
            encoding="utf-8",
        )
        corpus = tmp_path / "corpus.yaml"
        corpus.write_text("entries: []\n", encoding="utf-8")

        # Trust it, or the loader discards repo backends and BOTH names
        # resolve to nothing -- which would make the negative case pass for
        # the wrong reason.
        import yaml as _yaml

        from code_forge.trust import record_trust

        xdg = tmp_path / ".xdg"
        (xdg / "code-forge").mkdir(parents=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        record_trust(
            gate,
            _yaml.safe_load(gate.read_text(encoding="utf-8")),
            config_dir=xdg / "code-forge",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "_load_user_backends_raw", lambda: {}, raising=False)
        args = argparse.Namespace(
            corpus=corpus,
            backend=backend,
            runs=1,
            output=None,
        )
        rc = cli._run_eval(args)
        return rc, capsys.readouterr()

    def test_a_typo_stops_before_any_entry_runs(self, tmp_path, monkeypatch, capsys):
        rc, out = self._eval(tmp_path, "reeal", monkeypatch, capsys)
        assert rc != 0
        assert "unknown backend" in out.err
        assert "reeal" in out.err

    def test_a_known_backend_is_not_rejected(self, tmp_path, monkeypatch, capsys):
        rc, out = self._eval(tmp_path, "real", monkeypatch, capsys)
        # Empty corpus, so it gets past resolution and finds nothing to do.
        assert "unknown backend" not in out.err


class _PreparationStopped(Exception):
    pass


class TestExplicitSeedContext:
    @pytest.fixture(autouse=True)
    def isolated_git(self, monkeypatch, tmp_path):
        for name in tuple(os.environ):
            if name.startswith("GIT_"):
                monkeypatch.delenv(name)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
        monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "templates"))
        monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/dev/null")
        monkeypatch.setenv("GIT_CONFIG_KEY_1", "commit.gpgsign")
        monkeypatch.setenv("GIT_CONFIG_VALUE_1", "false")
        (tmp_path / "templates").mkdir()
        monkeypatch.setattr(runner, "_create_gate_yaml", self.stop)
        monkeypatch.setattr(runner, "record_trust", self.forbidden)
        monkeypatch.setattr(runner, "_run_review", self.forbidden)

    @staticmethod
    def stop(*args, **kwargs):
        raise _PreparationStopped()

    @staticmethod
    def forbidden(*args, **kwargs):
        pytest.fail("preparation reached trust or review")

    @staticmethod
    def git(repo, *args):
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, check=True,
        ).stdout

    @staticmethod
    def fixture(tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        corpus = tmp_path / "corpus"
        seed = corpus / "base_files" / "e"
        seed.mkdir(parents=True)
        (seed / "m.py").write_bytes(b"a = 1\n")
        patch = corpus / "d.diff"
        patch.write_bytes(
            b"diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
        )
        return repo, corpus, seed, patch

    def test_uninitialized_child_cannot_discover_ancestor(self, tmp_path, record_property):
        self.git(tmp_path, "init", "-b", "main")
        child = tmp_path / "uninitialized"
        child.mkdir()
        command = ["git", "rev-parse", "--show-toplevel"]
        blocked = subprocess.run(command, cwd=child, capture_output=True, check=False)
        assert os.environ["GIT_CEILING_DIRECTORIES"] == str(tmp_path.resolve())
        assert blocked.returncode != 0 and not blocked.stdout
        without_ceiling = os.environ.copy()
        without_ceiling.pop("GIT_CEILING_DIRECTORIES")
        visible = subprocess.run(command, cwd=child, env=without_ceiling, capture_output=True, check=False)
        assert visible.returncode == 0
        assert pathlib.Path(os.fsdecode(visible.stdout).strip()).resolve() == tmp_path.resolve()
        record_property("ceiling", os.environ["GIT_CEILING_DIRECTORIES"])
        record_property("blocked_returncode", blocked.returncode)
        record_property("blocked_stderr", os.fsdecode(blocked.stderr))
        record_property("control_ancestor", os.fsdecode(visible.stdout).strip())

    def test_ignored_seed_files_remain_in_the_committed_context(self, tmp_path):
        repo, corpus, seed, patch = self.fixture(tmp_path)
        contents = {
            ".gitignore": b"*.c\n*.tmp\n",
            "m.py": b"a = 1\n",
            "unchanged.c": b"neighbor\0bytes\n",
            "empty.c": b"",
            "nested/.gitignore": b"*.h\n",
            "nested/unchanged.h": b"header\n",
        }
        for name, raw in contents.items():
            path = seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (repo / "existing.txt").write_bytes(b"existing\n")
        (repo / "runtime.tmp").write_bytes(b"unrelated ignored file\n")
        with pytest.raises(_PreparationStopped):
            runner._run_single(_stub_entry(), patch, str(repo), "harness", corpus_dir=corpus)
        names = self.git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD").split(b"\0")
        assert set(names) - {b""} == {name.encode() for name in contents} | {b"existing.txt"}
        for name, raw in contents.items():
            assert self.git(repo, "show", "HEAD:" + name) == raw
            assert (repo / name).read_bytes() == (b"a = 2\n" if name == "m.py" else raw)
        assert self.git(repo, "diff", "--name-only", "HEAD").splitlines() == [b"m.py"]
        assert (repo / "runtime.tmp").read_bytes() == b"unrelated ignored file\n"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX filenames and copy modes")
    def test_literal_paths_and_copytree_symlink_semantics(self, tmp_path):
        repo, corpus, seed, patch = self.fixture(tmp_path)
        names = ["--leading.c", ":(glob)*.c", "line\nbreak.c", "executable.c"]
        (seed / ".gitignore").write_text("*.c\nalias-dir/\n")
        for name in names:
            (seed / name).write_bytes(b"source bytes\n")
        (seed / "executable.c").chmod(0o755)
        (seed / "alias.c").symlink_to("executable.c")
        (seed / "sub").mkdir()
        (seed / "sub/child.c").write_bytes(b"child\n")
        (seed / "alias-dir").symlink_to("sub", target_is_directory=True)
        (repo / "runtime.c").write_bytes(b"unrelated ignored runtime file\n")
        with pytest.raises(_PreparationStopped):
            runner._run_single(_stub_entry(), patch, str(repo), "harness", corpus_dir=corpus)
        copied = names + ["alias.c", "sub/child.c", "alias-dir/child.c"]
        tracked = self.git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD").split(b"\0")
        assert set(tracked) - {b""} == {name.encode() for name in copied + ["m.py", ".gitignore"]}
        for name in copied:
            assert self.git(repo, "show", "HEAD:" + name) == (repo / name).read_bytes()
        assert not (repo / "alias.c").is_symlink()
        assert not (repo / "alias-dir").is_symlink()
        tree = self.git(repo, "ls-tree", "HEAD", "executable.c", "alias.c")
        assert len(tree.splitlines()) == 2
        assert all(line.startswith(b"100755 blob ") for line in tree.splitlines())

    @pytest.mark.parametrize("kind", ["none", "missing", "empty", "unchanged"])
    def test_legacy_seed_variants_reach_the_gate(self, tmp_path, kind):
        repo, corpus, seed, patch = self.fixture(tmp_path)
        if kind != "unchanged":
            (seed / "m.py").unlink()
            (repo / "m.py").write_bytes(b"a = 1\n")
        if kind == "missing":
            seed.rmdir()
        elif kind == "none":
            corpus = None
        elif kind == "unchanged":
            (repo / "m.py").write_bytes(b"a = 1\n")
            self.git(repo, "init", "-b", "main")
            self.git(repo, "add", "m.py")
            self.git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "existing")
        with pytest.raises(_PreparationStopped):
            runner._run_single(_stub_entry(), patch, str(repo), "harness", corpus_dir=corpus)
        assert (repo / "m.py").read_bytes() == b"a = 2\n"

    @pytest.mark.parametrize("operation", ["init", "initial-commit", "add", "explicit-add", "seed-commit"])
    @pytest.mark.parametrize("failure", ["status", "oserror"])
    def test_git_preparation_failures_stop_before_gate(self, monkeypatch, tmp_path, operation, failure):
        repo, corpus, seed, patch = self.fixture(tmp_path)
        real_run = runner.subprocess.run
        commands = []
        failed = []

        def run(cmd, **kwargs):
            if "init" in cmd and "commit" not in cmd:
                current = "init"
            elif "commit" in cmd:
                current = "seed-commit" if "seed base files" in cmd else "initial-commit"
            elif "add" in cmd:
                current = "explicit-add" if "-f" in cmd else "add"
            else:
                current = "other"
            commands.append(current)
            if current == operation:
                failed.append(current)
                if failure == "oserror":
                    raise OSError("Git launch failed")
                if operation == "init":
                    (repo / ".git").write_text("invalid git directory marker\n")
                else:
                    (repo / ".git" / "index.lock").write_text("held by fixture\n")
                result = real_run(cmd, **kwargs)
                assert result.returncode != 0 and result.stderr
                return result
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "run", run)
        monkeypatch.setattr(runner, "_create_gate_yaml", self.forbidden)
        flagged, reason = runner._run_single(_stub_entry(), patch, str(repo), "harness", corpus_dir=corpus)
        assert flagged is False and reason.startswith("infra:")
        assert failed == [operation]
        assert commands[-1] == operation
        assert not (repo / ".code-forge").exists()
        if (repo / "m.py").exists():
            assert (repo / "m.py").read_bytes() == b"a = 1\n"
        else:
            assert operation in ("init", "initial-commit")

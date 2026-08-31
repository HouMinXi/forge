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

import pathlib

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

    def test_the_runner_actually_grants_where_the_child_reads(
        self, monkeypatch, tmp_path
    ):
        """The arithmetic above is not the bug; passing the wrong dir was.

        An injection reverting record_trust's config_dir to the bare
        xdg_dir left the two tests above green, because neither one runs
        the code that chooses it. This one does: it captures what the
        runner hands record_trust and resolves it the way the child will.
        """
        seen = {}
        monkeypatch.setattr(
            runner, "record_trust",
            lambda path, data, config_dir=None: seen.update(dir=config_dir),
        )
        monkeypatch.setattr(
            runner, "_run_review",
            lambda cmd, temp_dir, env, timeout_s: (0, ""),
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "m.py").write_text("a = 1\n", encoding="utf-8")
        diff = tmp_path / "d.diff"
        diff.write_text(
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1 +1 @@\n-a = 1\n+a = 2\n",
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
            runner, "_run_review",
            lambda cmd, temp_dir, env, timeout_s: (returncode, stderr_text),
        )
        monkeypatch.setattr(runner, "_create_gate_yaml", lambda *a, **kw: _stub_gate(tmp_path))
        monkeypatch.setattr(runner, "record_trust", lambda *a, **kw: None)

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "m.py").write_text("a = 1\n", encoding="utf-8")
        diff = tmp_path / "d.diff"
        diff.write_text(
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1 +1 @@\n-a = 1\n+a = 2\n",
            encoding="utf-8",
        )
        entry = _stub_entry()
        return runner._run_single(entry, diff, str(repo), "harness")

    def test_an_untrusted_gate_is_infra_not_a_hold(self, monkeypatch, tmp_path):
        flagged, reason = self._run(
            monkeypatch, tmp_path,
            "Untrusted repo backends ignored. Run 'code-forge trust' to enable.\n",
        )
        assert flagged is False
        assert "no state.json" in reason

    def test_a_missing_backend_is_infra_not_a_hold(self, monkeypatch, tmp_path):
        flagged, reason = self._run(
            monkeypatch, tmp_path,
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
        data = _yaml.safe_load(
            (tmp_path / ".code-forge" / "tools.yaml").read_text(encoding="utf-8")
        )
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
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1 +1 @@\n-a = 1\n+a = 2\n",
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

    def test_a_real_hold_survives_infra_wording_in_its_findings(
        self, monkeypatch, tmp_path
    ):
        """Reviewing networking code makes this collision likely, not exotic."""
        flagged, reason = self._run(
            monkeypatch, tmp_path,
            "1 CONFIRMED: retry loop swallows Connection refused\n",
            write_state=True,
        )
        assert flagged is True
        assert reason == ""

    def test_a_genuine_outage_keeps_its_specific_reason(self, monkeypatch, tmp_path):
        flagged, reason = self._run(
            monkeypatch, tmp_path,
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
            corpus=corpus, backend=backend, runs=1, output=None,
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

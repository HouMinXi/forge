# SPDX-License-Identifier: Apache-2.0
"""Tests for the eval CLI subcommand (17-04).

Verifies: parser registration, _run_eval dispatch, format_table stderr,
JSON output via --output, --runs validation, exit code on bad corpus path.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Lazy-import test: ensure cli module can be loaded
from code_forge.cli import _build_parser, main
from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.scorer import EvalResult, EvalSummary


# ---------------------------------------------------------------------------
# Parser registration tests
# ---------------------------------------------------------------------------


class TestEvalParser:
    """Verify eval subcommand is registered with correct args."""

    def test_eval_subcommand_recognized(self):
        """eval is a known subcommand in the parser."""
        parser = _build_parser()
        args = parser.parse_args(["eval", "--corpus", "c.yaml", "--backend", "b"])
        assert args.subcommand == "eval"

    def test_corpus_argument_required(self):
        """--corpus is required."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["eval", "--backend", "b"])

    def test_backend_argument_required(self):
        """--backend is required."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["eval", "--corpus", "c.yaml"])

    def test_runs_argument_optional_int(self):
        """--runs is optional and parsed as int."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b", "--runs", "5",
        ])
        assert args.runs == 5

    def test_output_argument_optional_path(self):
        """--output is optional and parsed as Path."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b",
            "--output", "/tmp/out.json",
        ])
        assert args.output == Path("/tmp/out.json")

    def test_runs_default_is_none(self):
        """--runs defaults to None (axis-dependent)."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b",
        ])
        assert args.runs is None

    def test_output_default_is_none(self):
        """--output defaults to None (no JSON output)."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b",
        ])
        assert args.output is None


# ---------------------------------------------------------------------------
# _run_eval dispatch tests (mocked -- no real LLM calls)
# ---------------------------------------------------------------------------


class TestRunEval:
    """Test _run_eval function with mocked replay_entry."""

    def _make_entry(self, name="test-bug", expected="HOLD"):
        return CorpusEntry(
            name=name,
            diff_file="diffs/%s.diff" % name,
            expected_verdict=expected,
            axis_tags=["RUNTIME"],
        )

    def _make_result(self, entry, actual="HOLD", runs=1, caught=1, skip=""):
        return EvalResult(
            entry=entry,
            actual_verdict=actual,
            runs=runs,
            caught_count=caught,
            skipped_reason=skip,
        )

    def test_run_eval_calls_pipeline(self, tmp_path, capsys):
        """_run_eval loads corpus, replays entries, prints table."""
        from code_forge.cli import _run_eval

        entry = self._make_entry()
        result = self._make_result(entry)

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "corpus.yaml"
        mock_args.backend = "test-backend"
        mock_args.runs = None
        mock_args.output = None

        with patch("code_forge.eval.corpus.load_corpus", return_value=[entry]), \
             patch("code_forge.eval.runner.replay_entry", return_value=result), \
             patch("code_forge.eval.scorer.compute_summary") as mock_summary, \
             patch("code_forge.eval.scorer.format_table", return_value="table-output"):

            summary = EvalSummary(
                total=1, caught=1, missed=0, correct_pass=0,
                false_positive=0, skipped=0, results=[result],
                advisory_caught=0, advisory_missed=0,
            )
            mock_summary.return_value = summary

            rc = _run_eval(mock_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "table-output" in captured.err

    def test_run_eval_writes_json_output(self, tmp_path):
        """--output causes JSON report to be written."""
        from code_forge.cli import _run_eval

        entry = self._make_entry()
        result = self._make_result(entry)
        out_path = tmp_path / "results.json"

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "corpus.yaml"
        mock_args.backend = "test-backend"
        mock_args.runs = None
        mock_args.output = out_path

        summary = EvalSummary(
            total=1, caught=1, missed=0, correct_pass=0,
            false_positive=0, skipped=0, results=[result],
            advisory_caught=0, advisory_missed=0,
        )

        with patch("code_forge.eval.corpus.load_corpus", return_value=[entry]), \
             patch("code_forge.eval.runner.replay_entry", return_value=result), \
             patch("code_forge.eval.scorer.compute_summary", return_value=summary), \
             patch("code_forge.eval.scorer.format_table", return_value="t"), \
             patch("code_forge.eval.scorer.write_json_report") as mock_write:

            rc = _run_eval(mock_args)

        assert rc == 0
        mock_write.assert_called_once_with(summary, out_path)

    def test_run_eval_passes_runs_override(self, tmp_path):
        """--runs N is forwarded to replay_entry."""
        from code_forge.cli import _run_eval

        entry = self._make_entry()
        result = self._make_result(entry, runs=5, caught=3)

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "corpus.yaml"
        mock_args.backend = "test-backend"
        mock_args.runs = 5
        mock_args.output = None

        summary = EvalSummary(
            total=1, caught=1, missed=0, correct_pass=0,
            false_positive=0, skipped=0, results=[result],
            advisory_caught=0, advisory_missed=0,
        )

        with patch("code_forge.eval.corpus.load_corpus", return_value=[entry]), \
             patch("code_forge.eval.runner.replay_entry", return_value=result) as mock_replay, \
             patch("code_forge.eval.scorer.compute_summary", return_value=summary), \
             patch("code_forge.eval.scorer.format_table", return_value="t"):

            rc = _run_eval(mock_args)

        assert rc == 0
        mock_replay.assert_called_once()
        call_kwargs = mock_replay.call_args
        assert call_kwargs[1].get("runs") == 5 or call_kwargs[0][3] == 5

    def test_run_eval_runs_validation_rejects_zero(self, capsys):
        """--runs 0 prints error and returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        mock_args = MagicMock()
        mock_args.corpus = Path("/tmp/nonexistent.yaml")
        mock_args.backend = "test"
        mock_args.runs = 0
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "--runs must be >= 1" in captured.err

    def test_run_eval_runs_validation_rejects_negative(self, capsys):
        """--runs -1 prints error and returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        mock_args = MagicMock()
        mock_args.corpus = Path("/tmp/nonexistent.yaml")
        mock_args.backend = "test"
        mock_args.runs = -1
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "--runs must be >= 1" in captured.err

    def test_run_eval_file_not_found(self, capsys, tmp_path):
        """Missing corpus file returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "no-such-file.yaml"
        mock_args.backend = "test"
        mock_args.runs = None
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR

    def test_run_eval_malformed_yaml(self, capsys, tmp_path):
        """Malformed YAML corpus returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(": :\n  - [invalid", encoding="utf-8")

        mock_args = MagicMock()
        mock_args.corpus = bad_yaml
        mock_args.backend = "test"
        mock_args.runs = None
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR


# ---------------------------------------------------------------------------
# known_subcommands includes eval
# ---------------------------------------------------------------------------


class TestEvalKnownSubcommand:
    """Verify eval is in the known_subcommands set."""

    def test_eval_in_known_subcommands(self):
        """eval must be in known_subcommands to avoid review fallback."""
        import code_forge.cli as cli_mod
        src = Path(cli_mod.__file__).read_text(encoding="utf-8")
        assert "'eval'" in src or '"eval"' in src


# ---------------------------------------------------------------------------
# Corpus manifest completeness test
# ---------------------------------------------------------------------------


class TestCorpusCompleteness:
    """Verify corpus.yaml contains exactly the required entries."""

    REQUIRED_NAMES = {
        "gate-yaml-rce",
        "E1-stale-nftables",
        "E2-pcap-suffix",
        "E3-transit-probe",
        "E4-curl-tproxy",
        "E5-fast-502",
        "E6-reprobe-blackout",
        "E7-killswitch-reprobe",
        "BUG-P12-01",
        "ttl_class",
        "E8-blast-radius-llm-invoke",
        "E9-killswitch-mark-conflict",
        # X-series: "the check that cannot fail", each buggy entry paired
        # with a control carrying the same edit made correctly. Listed here
        # rather than relaxing the comparison to a subset check: the set is
        # still exact, so a renamed or dropped entry still fails, and the
        # pairing is part of the corpus contract -- a buggy entry whose
        # control went missing measures nothing, and would otherwise go
        # missing quietly.
        "X1-guard-always-false",
        "X1c-guard-always-false-control",
        "X2-pipeline-exit-mask",
        "X2c-pipeline-exit-mask-control",
        "X3-noop-handler",
        "X3c-noop-handler-control",
    }

    def test_corpus_has_all_entries(self):
        """corpus.yaml must contain exactly the required entries."""
        from code_forge.eval.corpus import load_corpus

        manifest = Path(__file__).parent / "eval" / "corpus" / "corpus.yaml"
        entries = load_corpus(manifest)
        names = {e.name for e in entries}
        assert names == self.REQUIRED_NAMES, (
            "Missing: %s, Extra: %s"
            % (self.REQUIRED_NAMES - names, names - self.REQUIRED_NAMES)
        )

    def test_all_diff_files_exist_and_nonempty(self):
        """Each entry's diff_file must exist and be non-empty."""
        from code_forge.eval.corpus import load_corpus

        manifest = Path(__file__).parent / "eval" / "corpus" / "corpus.yaml"
        entries = load_corpus(manifest)
        corpus_dir = manifest.parent
        for entry in entries:
            diff_path = corpus_dir / entry.diff_file
            assert diff_path.exists(), "diff file missing: %s" % entry.diff_file
            assert diff_path.stat().st_size > 0, (
                "diff file empty: %s" % entry.diff_file
            )

    def test_every_buggy_entry_has_a_control(self):
        """X-series entries come in pairs, and the pair is the measurement.

        A buggy entry on its own proves nothing: forge exits non-zero for
        plenty of reasons, so "flagged" only means something when the same
        edit made correctly is NOT flagged. If a control is dropped or
        renamed its partner keeps running and keeps producing a number that
        looks like a result, which is the failure this guards.
        """
        from code_forge.eval.corpus import load_corpus

        manifest = Path(__file__).parent / "eval" / "corpus" / "corpus.yaml"
        names = {e.name for e in load_corpus(manifest)}
        buggy = [n for n in names if n.startswith("X") and "-control" not in n]
        assert buggy, "X-series entries disappeared from the corpus"
        for name in buggy:
            prefix, rest = name.split("-", 1)
            control = "%sc-%s-control" % (prefix, rest)
            assert control in names, (
                "%s has no control; the pair is what makes it measurable" % name
            )

    def test_base_files_exist_for_every_x_entry(self):
        """Each X entry needs a base_files dir named after the ENTRY.

        runner.py seeds from base_files/<entry.name>, not from a shared
        fixture name. A control whose directory is missing gets no source
        file, its diff fails to apply, and the run is recorded as an infra
        skip -- silently voiding the pair rather than failing loudly. That
        happened while these entries were being written.
        """
        from code_forge.eval.corpus import load_corpus

        manifest = Path(__file__).parent / "eval" / "corpus" / "corpus.yaml"
        corpus_dir = manifest.parent
        for entry in load_corpus(manifest):
            if not entry.name.startswith("X"):
                continue
            base = corpus_dir / "base_files" / entry.name
            assert base.is_dir(), "no base_files dir for %s" % entry.name
            assert any(base.iterdir()), "base_files/%s is empty" % entry.name


class TestEvalReviewTimeout:
    """The per-review timeout bounds a hang; it must not bound the work.

    At 300s it did the latter: one call on a reasoning backend measured
    77-295s, three passes run per round, and a review runs several rounds.
    Every entry came back as an infra skip, which reads as "no data" and
    quietly leaves both sides of the ratio.
    """

    def test_default_is_generous_enough_for_a_real_review(self):
        from code_forge.eval.runner import _review_timeout_s

        with patch.dict("os.environ", {}, clear=True):
            assert _review_timeout_s() >= 1800

    def test_env_override_is_honoured(self):
        from code_forge.eval.runner import _review_timeout_s

        with patch.dict("os.environ", {"FORGE_EVAL_REVIEW_TIMEOUT_S": "42"}):
            assert _review_timeout_s() == 42

    def test_unparseable_override_falls_back(self):
        from code_forge.eval.runner import _review_timeout_s

        with patch.dict("os.environ", {"FORGE_EVAL_REVIEW_TIMEOUT_S": "soon"}):
            assert _review_timeout_s() >= 1800

    def test_nonpositive_override_falls_back(self):
        """0 would abandon every review instantly and report all skips."""
        from code_forge.eval.runner import _review_timeout_s

        for bad in ("0", "-1"):
            with patch.dict("os.environ", {"FORGE_EVAL_REVIEW_TIMEOUT_S": bad}):
                assert _review_timeout_s() >= 1800

    def test_skip_message_reports_the_timeout_actually_used(self):
        """The message carried a hardcoded 300 while the value was a variable.

        A skip reason that names a number the run never used sends the next
        reader looking for a timeout that is not there. Both halves have to
        be checked against what the process actually received, so an
        override is used that matches neither the old constant nor the new
        default.
        """
        import subprocess as sp
        import tempfile

        from code_forge.eval import runner

        entry = CorpusEntry(
            name="timeout-probe", diff_file="x.diff",
            expected_verdict="flagged", axis_tags=[], expected_advisory=[],
        )
        seen = {}

        def fake_review(cmd, cwd, env, timeout_s):
            seen["timeout_s"] = timeout_s
            raise sp.TimeoutExpired(cmd, timeout_s)

        with tempfile.TemporaryDirectory() as td:
            # A patch git will really apply, so execution reaches the
            # review. Stubbing that step out instead would let this pass
            # with the call site gone, and an empty file is not enough --
            # git apply rejects it as having no valid patches.
            diff_path = Path(td) / "x.diff"
            diff_path.write_text(
                "diff --git a/probe.txt b/probe.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/probe.txt\n"
                "@@ -0,0 +1 @@\n"
                "+a\n"
            )
            with patch.dict("os.environ",
                            {"FORGE_EVAL_REVIEW_TIMEOUT_S": "77"}):
                with patch.object(runner, "_run_review",
                                  side_effect=fake_review):
                    flagged, reason = runner._run_single(
                        entry, diff_path, td, "stub-backend",
                    )

        assert seen.get("timeout_s") == 77, (
            "_run_review got timeout_s=%r, not the resolved value"
            % seen.get("timeout_s")
        )
        assert flagged is False
        assert reason == "infra: code-forge review timeout after 77s", reason


class TestAbandonedReviewLeavesNothingBehind:
    """A review that runs past its timeout is killed, not merely released.

    Both behaviors here need a real process to show anything. A mock can
    confirm which arguments were passed and nothing about whether memory
    stayed bounded or a grandchild died, which is the entire claim.
    """

    def test_output_does_not_accumulate_in_the_parent(self, tmp_path):
        """capture_output held the child's stream in RAM until it exited.

        Measured by reverting the fix and running this: the parent grew
        6.5 GB inside the two-second window below, on a host with less RAM
        than that. The real timeout is half an hour.
        """
        import resource
        import subprocess as sp

        from code_forge.eval.runner import _run_review

        spammer = tmp_path / "spammer.py"
        spammer.write_text(
            "import sys\n"
            "while True:\n"
            "    sys.stdout.write('x' * 4096)\n"
        )

        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with pytest.raises(sp.TimeoutExpired):
            _run_review([sys.executable, str(spammer)],
                        str(tmp_path), dict(os.environ), 2)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        grew_mb = (after - before) / 1024
        assert grew_mb < 100, (
            "parent RSS grew %.1f MB while the child spammed stdout; "
            "the output is being buffered in memory again" % grew_mb
        )

    def test_a_grandchild_does_not_outlive_the_timeout(self, tmp_path):
        """Killing the direct child alone leaves what it spawned running.

        A real review shells out, so the process holding the backend
        connection is usually not the one the timeout can see. Before the
        process group, the grandchild here survived and was reparented to
        init.
        """
        import subprocess as sp
        import time

        from code_forge.eval.runner import _run_review

        pidfile = tmp_path / "grandchild.pid"
        # The path travels in the environment rather than inside the source
        # text: quoting it into a -c string that is itself quoted is one
        # nesting level too many, and the version that got it wrong died on
        # a syntax error, which reads as "the timeout worked".
        grandchild = tmp_path / "grandchild.py"
        grandchild.write_text(
            "import os, time\n"
            "open(os.environ['FORGE_TEST_PIDFILE'], 'w').write(str(os.getpid()))\n"
            "time.sleep(300)\n"
        )
        parent = tmp_path / "parent.py"
        parent.write_text(
            "import os, subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, os.environ['FORGE_TEST_CHILD']])\n"
            "time.sleep(300)\n"
        )

        env = dict(os.environ)
        env["FORGE_TEST_PIDFILE"] = str(pidfile)
        env["FORGE_TEST_CHILD"] = str(grandchild)

        with pytest.raises(sp.TimeoutExpired):
            _run_review([sys.executable, str(parent)],
                        str(tmp_path), env, 2)

        # exists() goes true the instant the child opens the file, which is
        # before anything is in it -- measured empty on 200 of 200 tight-loop
        # trials. Waiting for content rather than for the entry keeps a
        # slower machine from failing this on int("") and reporting it as
        # broken test setup instead of as a surviving grandchild.
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.1)
        recorded = pidfile.read_text().strip() if pidfile.exists() else ""
        assert recorded, "grandchild never started; test proves nothing"
        pid = int(recorded)

        # SIGKILL is delivered asynchronously; give the kernel a moment
        # before concluding the process survived it.
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pytest.fail("grandchild %d outlived the timeout" % pid)

    def test_a_grandchild_in_its_own_session_is_reached_through_the_review(
            self, tmp_path):
        """A group signal stops at the edge of the group.

        The test above works because its grandchild stays in the group.
        The real one does not: llm_invoke starts the backend CLI with
        start_new_session, so the CLI leads a session of its own and is
        no longer in the group this timeout signals. killpg then returns
        success while the CLI keeps running -- nothing raises, nothing is
        logged, and the only evidence is a process still holding the
        connection the timeout existed to end.

        What can reach it is the review's own SIGTERM handler, which
        signals the CLI's group where this side cannot. SIGKILL cannot be
        caught, so the more forceful signal is the one that leaves the
        CLI alive; the parent below stands in for that arrangement,
        starting its child in a separate session and tearing it down on
        SIGTERM the way llm_invoke's handler does.
        """
        import subprocess as sp
        import time

        from code_forge.eval.runner import _run_review

        pidfile = tmp_path / "escaped.pid"
        grandchild = tmp_path / "escaped.py"
        grandchild.write_text(
            "import os, time\n"
            "open(os.environ['FORGE_TEST_PIDFILE'], 'w').write(str(os.getpid()))\n"
            "time.sleep(300)\n"
        )
        parent = tmp_path / "reviewer.py"
        parent.write_text(
            "import os, signal, subprocess, sys, time\n"
            "kid = subprocess.Popen([sys.executable,\n"
            "                        os.environ['FORGE_TEST_CHILD']],\n"
            "                       start_new_session=True)\n"
            "def _teardown(signum, frame):\n"
            "    os.killpg(os.getpgid(kid.pid), signal.SIGKILL)\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, _teardown)\n"
            "time.sleep(300)\n"
        )

        env = dict(os.environ)
        env["FORGE_TEST_PIDFILE"] = str(pidfile)
        env["FORGE_TEST_CHILD"] = str(grandchild)

        with pytest.raises(sp.TimeoutExpired):
            _run_review([sys.executable, str(parent)],
                        str(tmp_path), env, 2)

        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.1)
        recorded = pidfile.read_text().strip() if pidfile.exists() else ""
        assert recorded, "grandchild never started; test proves nothing"
        pid = int(recorded)

        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pytest.fail(
            "grandchild %d in its own session outlived the timeout" % pid
        )

    def test_a_child_dying_at_the_timeout_still_raises_timeout(self, tmp_path):
        """The caller needs TimeoutExpired, not whatever the cleanup hit.

        A child that exits in the same instant the timeout fires is reaped
        by Popen's own SIGCHLD handling, so every later lookup on its pid
        raises ProcessLookupError. Cleanup used to let that escape and
        replace the TimeoutExpired, which turns a recorded skip into a
        crashed eval run.

        The lookup is forced to fail rather than raced into failing: a
        child whose sleep merely matches the timeout hits the window only
        when the scheduler cooperates, and a test that needs luck to fail
        is green for the wrong reason.

        The mock only covers the lookup, not the child's own exit, so
        nothing here actually signals it -- the final wait blocks for
        real until it exits on its own. Two seconds keeps that real wait
        short while still clearing the one-second timeout below; the
        first version of this test slept 30s here for no reason tied to
        what it verifies, and paid all of it as real wall clock on every
        run of the suite.
        """
        import subprocess as sp

        from code_forge.eval.runner import _run_review

        sleeper = tmp_path / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(2)\n")

        with patch("code_forge.eval.runner.os.getpgid",
                   side_effect=ProcessLookupError(3, "No such process")):
            with pytest.raises(sp.TimeoutExpired):
                _run_review([sys.executable, str(sleeper)],
                            str(tmp_path), dict(os.environ), 1)

    def test_cleanup_on_windows_does_not_reach_for_process_groups(
            self, tmp_path):
        """Those names are missing on Windows, not merely unsupported.

        os.getpgid and os.killpg do not exist there, so touching them
        raises AttributeError -- which is neither of the errors the POSIX
        path tolerates and would escape in place of the caller's
        TimeoutExpired, turning a recorded skip into a crashed run. There
        is no process group to signal on that platform, so the direct
        kill is all of it.

        Only the two os names are covered here. signal.SIGKILL is missing
        on that platform too and is left in place by this test, so it is
        checked separately below -- an earlier version of this docstring
        claimed it, and three reviewers found the gap the claim hid.
        """
        import subprocess as sp

        from code_forge.eval.runner import _run_review

        sleeper = tmp_path / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(2)\n")

        # Standing in for the platform: the names are patched away rather
        # than merely renamed, so anything still reaching for them raises
        # exactly what Windows would raise.
        with patch("code_forge.eval.runner.os.name", "nt"):
            with patch("code_forge.eval.runner.os.getpgid",
                       side_effect=AttributeError(
                           "module 'os' has no attribute 'getpgid'")):
                with patch("code_forge.eval.runner.os.killpg",
                           side_effect=AttributeError(
                               "module 'os' has no attribute 'killpg'")):
                    with pytest.raises(sp.TimeoutExpired):
                        _run_review([sys.executable, str(sleeper)],
                                    str(tmp_path), dict(os.environ), 1)

    def test_the_windows_teardown_never_names_the_signal_it_lacks(
            self, monkeypatch):
        """A signal named at a call site is resolved before the call.

        The teardown reaches SIGKILL through a helper that declines to
        use it when there is no group to send it to, and on Windows there
        never is. That guard reads as sufficient and is not: an argument
        is evaluated while the call is being assembled, so a call site
        spelling signal.SIGKILL raises AttributeError there before the
        helper it is being passed to gets to run. The escaping error then
        replaces the TimeoutExpired the caller needed, which is the same
        crashed-run failure the guard was put in for.

        Deleting the name is the whole test, because the bug is a name
        being read. It goes through _kill_process_group rather than the
        helper directly, since the helper never had the defect -- calling
        it straight would stay green with the call site still broken.

        The process is a double: a real one would reach for SIGKILL from
        inside subprocess.kill on this platform and fail for a reason
        that is not the one under test.
        """
        import subprocess as sp

        from code_forge.eval.runner import _kill_process_group

        proc = MagicMock()
        proc.pid = 4321
        proc.poll.return_value = None
        # Outlives the SIGTERM grace, which is what carries execution
        # down to the line that names SIGKILL.
        proc.wait.side_effect = [sp.TimeoutExpired("review", 10), None]

        monkeypatch.setattr("code_forge.eval.runner.os.name", "nt")
        monkeypatch.delattr(signal, "SIGKILL")

        _kill_process_group(proc)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_an_unkillable_child_does_not_hold_the_run_open(self):
        """SIGKILL does not reach a process stopped in the kernel.

        A child in uninterruptible sleep stays unkillable for as long as
        whatever it is blocked on stays unresponsive, and the wait that
        reaps it is the last thing standing between one leaked zombie and
        a run of hundreds of reviews that never returns. So the wait is
        bounded and the timeout is swallowed: the zombie is the cheaper
        of the two, and it is the caller's TimeoutExpired that has to
        reach the top, not this one.

        The group lookup is stubbed rather than left to run, because the
        double's pid is a number and not a process: asking the system
        about it can name a real group belonging to something else, and
        the next line signals whatever it named.
        """
        import subprocess as sp

        from code_forge.eval.runner import _kill_process_group

        proc = MagicMock()
        proc.pid = 4321
        proc.poll.return_value = None
        # Survives the grace, then survives SIGKILL as well.
        proc.wait.side_effect = sp.TimeoutExpired("review", 5)

        with patch("code_forge.eval.runner.group_of", return_value=None):
            _kill_process_group(proc)

        proc.kill.assert_called_once()
        assert proc.wait.call_count == 2, "the post-SIGKILL reap did not run"
        # Not raising is not enough to show here. A double cannot block
        # the way the real call would, so it reports the hang as an
        # escaping error, and catching that around an unbounded wait
        # would look identical from outside while still hanging in
        # production. The bound itself is the thing to assert.
        assert proc.wait.call_args_list[1].kwargs.get("timeout"), (
            "the reap after SIGKILL was not given a timeout"
        )

    def test_a_permission_error_on_the_fallback_kill_does_not_crash(
            self, tmp_path):
        """The fallback has no fallback of its own, so it cannot raise.

        A refused group signal drops to killing the child directly; if
        that is refused too, or the child is already gone by then, there
        is nothing left to try. Letting either exception escape here
        would hand the caller that exception in place of the
        TimeoutExpired it needs to record a skip -- the same failure
        mode the group-leader check exists to prevent, one line lower.
        """
        import subprocess as sp

        from code_forge.eval.runner import _run_review

        sleeper = tmp_path / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(2)\n")

        with patch("code_forge.eval.runner.os.killpg",
                   side_effect=PermissionError(1, "Operation not permitted")):
            with patch("subprocess.Popen.kill",
                       side_effect=ProcessLookupError(3, "No such process")):
                with pytest.raises(sp.TimeoutExpired):
                    _run_review([sys.executable, str(sleeper)],
                                str(tmp_path), dict(os.environ), 1)

    def test_stderr_comes_back_bounded(self, tmp_path):
        """Reading the whole file back would undo the bounded write.

        The temp file lets the child write without filling memory, and
        then reading all of it allocates a string of exactly that size --
        the same OOM, moved to after the exit. Only the tail comes back.
        """
        from code_forge.eval.runner import _STDERR_TAIL_BYTES, _run_review

        noisy = tmp_path / "noisy.py"
        noisy.write_text(
            "import sys\n"
            "for _ in range(300):\n"
            "    sys.stderr.write('x' * 4096)\n"
            "sys.stderr.write('THE-REAL-ERROR')\n"
        )

        returncode, stderr_text = _run_review(
            [sys.executable, str(noisy)], str(tmp_path), dict(os.environ), 30)

        assert returncode == 0
        assert len(stderr_text) <= _STDERR_TAIL_BYTES, (
            "stderr came back at %d bytes, past the %d cap"
            % (len(stderr_text), _STDERR_TAIL_BYTES)
        )
        assert stderr_text.endswith("THE-REAL-ERROR"), (
            "the tail is the half worth keeping; got %r" % stderr_text[-40:]
        )

    def test_a_child_that_is_not_a_group_leader_is_killed_alone(self, tmp_path):
        """The group signal is only safe against a group of its own.

        Aimed at a group this process belongs to, the SIGKILL arrives
        here too: that is how the run that omitted start_new_session
        ended, with the test runner dying instead of a test failing. The
        check that prevents it cannot be an assert, because -O removes
        asserts and would leave the signal with nothing in front of it.
        """
        import subprocess as sp
        import time

        from code_forge.eval.runner import _run_review

        sleeper = tmp_path / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(30)\n")

        # A pgid that is not the child's own pid is what the leader check
        # is for; killpg on it would signal whatever group that is.
        with patch("code_forge.eval.runner.os.getpgid",
                   return_value=os.getpgid(0)):
            with patch("code_forge.eval.runner.os.killpg") as killpg:
                started = time.monotonic()
                with pytest.raises(sp.TimeoutExpired):
                    _run_review([sys.executable, str(sleeper)],
                                str(tmp_path), dict(os.environ), 1)
                elapsed = time.monotonic() - started

        assert not killpg.called, (
            "killpg was aimed at pgid %r, which is this process's own "
            "group" % os.getpgid(0)
        )
        assert elapsed < 10, (
            "cleanup took %.1fs, so the child was neither group-killed "
            "nor killed on its own and the reap waited it out" % elapsed
        )

    def test_a_refused_group_kill_still_kills_the_child(self, tmp_path):
        """A container can refuse the group signal and allow a plain one.

        What that costs is visible in the clock rather than in a return
        value: the reap at the end waits for whatever the kill left
        alive, so with nothing else killing the child it sits there for
        the length of the review it was supposed to abandon.
        """
        import subprocess as sp
        import time

        from code_forge.eval.runner import _run_review

        sleeper = tmp_path / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(30)\n")

        started = time.monotonic()
        with patch("code_forge.eval.runner.os.killpg",
                   side_effect=PermissionError(1, "Operation not permitted")):
            with pytest.raises(sp.TimeoutExpired):
                _run_review([sys.executable, str(sleeper)],
                            str(tmp_path), dict(os.environ), 1)
        elapsed = time.monotonic() - started

        assert elapsed < 10, (
            "cleanup took %.1fs against a 1s timeout, so the child "
            "outlived the refused group kill and the reap waited for "
            "its own sleep to end" % elapsed
        )

# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the gate-check subcommand."""

import json
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest
import yaml

from code_forge.exit_codes import EXIT_FAIL, EXIT_PASS
import subprocess

from code_forge.gate_check import (
    compute_baseline_delta,
    fnmatch_to_grep,
    is_ci_mode,
    load_gate_config,
    match_source_patterns,
    run_gate_check,
    translate_exit_code,
    validate_command_safety,
    validate_presubmit_command,
    validate_retry_config,
)


class TestFailureWaiverEvidence:
    @pytest.mark.parametrize(
        "shape",
        [
            "ordinary",
            "exit",
            "failed",
            "maxfail_reached",
            "maxfail_below",
            "passing",
            "collection_failed",
            "sessionstart_failed",
            "configure_failed",
        ],
    )
    def test_caught_runner_abort_refuses_complete_phase_reports(self, tmp_path, shape):
        passing = shape == "passing"
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n"
        source += "    pass\n" if passing else "    assert False\n"
        args = ["--maxfail=1"] if shape in ("maxfail_reached", "passing") else []
        if shape == "maxfail_below":
            args = ["--maxfail=2"]
        self.public(tmp_path, source, {"test_sample.py::test_known": "failed"}, args=args)
        plugin = ""
        if shape in ("exit", "failed"):
            plugin = "import pytest\nfrom _pytest.main import Failed\nfrom pathlib import Path\n"
            plugin += (
                "def pytest_runtest_logfinish(nodeid, location):\n"
                "    Path('aborted').write_text('after reports')\n"
            )
            plugin += (
                "    pytest.exit('abort after reports', returncode=1)\n"
                if shape == "exit"
                else "    raise Failed('abort after reports')\n"
            )
        elif shape in ("collection_failed", "sessionstart_failed", "configure_failed"):
            plugin = "import pytest\nfrom _pytest.main import Failed\nfrom pathlib import Path\n"
            if shape == "configure_failed":
                plugin += (
                    "@pytest.hookimpl(trylast=True)\ndef pytest_configure(config):\n"
                    "    session = config.pluginmanager.getplugin('session')\n"
                    "    config.hook.pytest_sessionstart(session=session)\n"
                )
            else:
                name = "pytest_collection" if shape == "collection_failed" else "pytest_sessionstart"
                plugin += (
                    f"@pytest.hookimpl(wrapper=True, trylast=True)\ndef {name}(session):\n    yield\n"
                )
            if shape != "collection_failed":
                plugin += "    session.config.hook.pytest_collection(session=session)\n"
            plugin += "    session.config.hook.pytest_runtestloop(session=session)\n"
            if shape != "collection_failed":
                plugin += "    session.config.hook.pytest_sessionfinish(session=session, exitstatus=1)\n"
            plugin += (
                "    Path('aborted').write_text('after reports')\n"
                "    raise Failed('outer dispatch aborted')\n"
            )
        (tmp_path / "conftest.py").write_text(plugin)
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        command = ["python3", "-B", "-m", "pytest", "-q", *args, "test_sample.py"]
        original = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=15)
        assert original.returncode == (0 if passing else 1), original.stderr
        assert (tmp_path / "executed").read_text() == "once"
        expected = EXIT_PASS if shape in ("ordinary", "maxfail_below", "passing") else EXIT_FAIL
        (tmp_path / "executed").unlink()
        error = StringIO()
        result = run_gate_check(env=env, cwd=tmp_path, stdout=StringIO(), stderr=error)
        assert result == expected, error.getvalue()
        assert (tmp_path / "executed").read_text() == "once"
        (tmp_path / "executed").unlink()
        public = self.cli(tmp_path)
        assert public.returncode == expected, public.stderr
        assert (tmp_path / "executed").read_text() == "once"
        if expected == EXIT_FAIL:
            assert "insufficient structured pytest evidence" in public.stderr
        elif not passing:
            assert "all failures are known" in public.stderr
        if plugin:
            assert (tmp_path / "aborted").read_text() == "after reports"

    def test_empty_cache_prefix_keeps_public_known_failure_waiver(self, tmp_path):
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n    assert False\n"
        result, error = self.public(
            tmp_path,
            source,
            {"test_sample.py::test_known": "failed"},
            extra_env={"PYTHONPYCACHEPREFIX": ""},
        )
        assert result == EXIT_PASS, error
        (tmp_path / "executed").unlink()
        public = self.cli(tmp_path, {"PYTHONPYCACHEPREFIX": ""})
        assert public.returncode == EXIT_PASS, public.stderr
        assert "all failures are known" in public.stderr
        assert (tmp_path / "executed").read_text() == "once"

    @pytest.mark.parametrize("relative", [False, True])
    @pytest.mark.parametrize("bytecode", [False, True])
    @pytest.mark.parametrize("passing", [False, True])
    def test_external_prefix_avoids_unsupported_bootstrap_cache(
        self, tmp_path, relative, bytecode, passing
    ):
        source = (
            "from pathlib import Path\nimport sys\ndef test_known():\n"
            "    Path('executed').write_text('once')\n"
            "    Path('runtime-prefix').write_text(str(sys.pycache_prefix))\n"
        )
        source += "    pass\n" if passing else "    assert False\n"
        command = ["python3", *([] if bytecode else ["-B"]), "-m", "pytest", "-q", "test_sample.py"]
        self.public(tmp_path, source, {"test_sample.py::test_known": "failed"}, command=command)
        cache = tmp_path / "caller-cache"
        cache.mkdir()
        sentinel = cache / "caller-file"
        sentinel.write_text("retain caller state")
        prefix = cache.name if relative else str(cache)
        (tmp_path / "executed").unlink()
        with tempfile.TemporaryDirectory(prefix="capture-", dir=tmp_path.parent) as temp:
            public = self.cli(
                tmp_path,
                {"PYTHONPYCACHEPREFIX": prefix, "PYTHONDONTWRITEBYTECODE": "", "TMPDIR": temp},
            )
            assert public.returncode == (EXIT_PASS if passing else EXIT_FAIL), public.stderr
            assert (tmp_path / "executed").read_text() == "once"
            assert (tmp_path / "runtime-prefix").read_text() == prefix
            if not passing:
                assert "insufficient structured pytest evidence" in public.stderr
            assert "capture cleanup failed" not in public.stderr
            assert sentinel.read_text() == "retain caller state"
            assert not list(Path(temp).iterdir())
            assert not cache.joinpath(*Path(temp).parts[1:]).exists()
            assert not [path for path in cache.rglob("*") if path.name.startswith("_forge_gate_")]

    @pytest.mark.parametrize(
        "shape",
        ["ordinary", "session_exit", "internal_exit"]
        + [
            kind + "_wrapper_" + order + "_" + position
            for kind in ("session", "internal")
            for order in ("first", "last")
            for position in ("before", "after")
        ],
    )
    def test_caught_lifecycle_exit_cannot_waive_known_failure(self, tmp_path, shape):
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n    assert False\n"
        self.public(tmp_path, source, {"test_sample.py::test_known": "failed"})
        plugin = ""
        if shape == "session_exit":
            plugin = (
                "import pytest\nfrom pathlib import Path\n"
                "@pytest.hookimpl(trylast=True)\ndef pytest_sessionfinish(session, exitstatus):\n"
                "    Path('aborted').write_text('session')\n"
                "    pytest.exit('aborted session', returncode=1)\n"
            )
        elif shape == "internal_exit":
            plugin = (
                "import pytest\nfrom pathlib import Path\n"
                "def pytest_runtest_logfinish(nodeid, location):\n"
                "    raise RuntimeError('actual harness failure')\n"
                "@pytest.hookimpl(tryfirst=True)\ndef pytest_internalerror(excrepr, excinfo):\n"
                "    Path('aborted').write_text('internal')\n"
                "    pytest.exit('caught internal', returncode=1)\n"
            )
        elif "_wrapper_" in shape:
            kind, _, order, position = shape.split("_")
            name = "pytest_sessionfinish" if kind == "session" else "pytest_internalerror"
            arguments = "session, exitstatus" if kind == "session" else "excrepr, excinfo"
            plugin = "import pytest\nfrom pathlib import Path\n"
            if kind == "internal":
                plugin += (
                    "def pytest_runtest_logfinish(nodeid, location):\n"
                    "    raise RuntimeError('actual wrapper harness failure')\n"
                )
            plugin += f"@pytest.hookimpl(wrapper=True, try{order}=True)\ndef {name}({arguments}):\n"
            abort = "    Path('aborted').write_text('wrapper')\n    pytest.exit('aborted wrapper', returncode=1)\n"
            if position == "before":
                plugin += abort + "    yield\n"
            else:
                plugin += "    result = yield\n" + abort + "    return result\n"
        (tmp_path / "conftest.py").write_text(plugin)
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        command = ["python3", "-B", "-m", "pytest", "-q", "test_sample.py"]
        original = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=15)
        assert original.returncode == 1, original.stderr
        assert (tmp_path / "executed").read_text() == "once"
        if shape != "ordinary":
            assert (tmp_path / "aborted").is_file()
            (tmp_path / "aborted").unlink()
        (tmp_path / "executed").unlink()
        public = self.cli(tmp_path)
        assert (tmp_path / "executed").read_text() == "once"
        assert public.returncode == (EXIT_PASS if shape == "ordinary" else EXIT_FAIL), public.stderr
        if shape == "ordinary":
            assert "all failures are known" in public.stderr
        else:
            assert (tmp_path / "aborted").is_file()
            assert "insufficient structured pytest evidence" in public.stderr

    @pytest.mark.parametrize("hook", ["pytest_sessionfinish", "pytest_internalerror"])
    @pytest.mark.parametrize("passing", [False, True])
    def test_replaced_dispatch_identity_preserves_execution(self, tmp_path, hook, passing):
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n"
        source += "    pass\n" if passing else "    assert False\n"
        self.public(tmp_path, source, {"test_sample.py::test_known": "failed"})
        (tmp_path / "conftest.py").write_text(
            "def pytest_configure(config):\n"
            f"    config.hook.{hook}._hookexec = config.pluginmanager._hookexec\n"
        )
        (tmp_path / "executed").unlink()
        error = StringIO()
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        result = run_gate_check(env=env, cwd=tmp_path, stdout=StringIO(), stderr=error)
        assert result == (EXIT_PASS if passing else EXIT_FAIL), error.getvalue()
        assert (tmp_path / "executed").read_text() == "once"
        (tmp_path / "executed").unlink()
        public = self.cli(tmp_path)
        assert (tmp_path / "executed").read_text() == "once"
        assert public.returncode == (EXIT_PASS if passing else EXIT_FAIL), public.stderr
        if not passing:
            assert "insufficient structured pytest evidence" in public.stderr

    @pytest.mark.parametrize("passing", [False, True])
    def test_final_capture_close_error_preserves_runner_outcome(self, tmp_path, monkeypatch, passing):
        import errno
        from code_forge import gate_check, _gate_pytest

        original_prepare = gate_check.prepare_pytest_capture
        original_close = os.close
        owned = {}

        def prepare(*args, **kwargs):
            capture = original_prepare(*args, **kwargs)
            owned["capture"] = capture
            owned["fd"] = capture.fd
            return capture

        def close_then_error(fd):
            original_close(fd)
            if fd == owned.get("fd"):
                owned["fault"] = True
                raise OSError(errno.EIO, "measured final capture close error")

        monkeypatch.setattr(gate_check, "prepare_pytest_capture", prepare)
        monkeypatch.setattr(_gate_pytest.os, "close", close_then_error)
        source = "def test_known():\n    " + ("pass\n" if passing else "assert False\n")
        result, error = self.public(tmp_path, source, {"test_sample.py::test_known": "failed"})
        assert result == (EXIT_PASS if passing else EXIT_FAIL), error
        assert "capture cleanup failed" in error
        assert owned["fault"] and owned["capture"].fd is None
        assert owned["capture"].authority is False
        assert not owned["capture"].directory.exists()
        with pytest.raises(OSError) as closed:
            os.fstat(owned["fd"])
        assert closed.value.errno == errno.EBADF

    def cli(self, tmp_path, extra_env=None):
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH", "")]
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", "-B", "-m", "code_forge", "gate-check", "--no-color"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_hardlinked_reporter_keeps_public_known_failure_waiver(self, tmp_path):
        from code_forge import _gate_pytest

        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n    raise AssertionError('known')\n"
        self.public(tmp_path, source, {"test_sample.py::test_known": "failed"})
        original = Path(__file__).resolve().parents[1] / "src" / "code_forge"
        package = tmp_path / "package" / "code_forge"
        for path in original.rglob("*.py"):
            target = package / path.relative_to(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        reporter = package / "_gate_pytest.py"
        reporter.unlink()
        copy = tmp_path / "reporter-source.py"
        copy.write_bytes(Path(_gate_pytest.__file__).read_bytes())
        os.link(copy, reporter)
        assert reporter.stat().st_nlink == 2
        (tmp_path / "conftest.py").write_text(
            "import json\nfrom pathlib import Path\nfrom code_forge import _gate_pytest\n"
            "def pytest_sessionstart(session):\n"
            "    p=Path(_gate_pytest.__file__).resolve()\n"
            "    Path('source-origin.json').write_text(json.dumps({'path':str(p),'links':p.stat().st_nlink}))\n"
        )
        (tmp_path / "executed").unlink()
        public = self.cli(
            tmp_path,
            {"PYTHONPATH": os.pathsep.join([str(package.parent), os.environ.get("PYTHONPATH", "")])},
        )
        assert public.returncode == EXIT_PASS, public.stderr
        assert "all failures are known" in public.stderr
        assert (tmp_path / "executed").read_text() == "once"
        assert json.loads((tmp_path / "source-origin.json").read_text()) == {
            "path": str(reporter.resolve()),
            "links": 2,
        }

    @pytest.mark.parametrize("passing", [False, True])
    def test_optimized_pytest_preserves_outcome_and_closes_owned_capture(self, tmp_path, passing):
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n"
        if not passing:
            source += "    raise AssertionError('known')\n"
        self.public(tmp_path, source, {"test_sample.py::test_known": "failed"})
        owned = tmp_path / "gate-tmp"
        owned.mkdir()
        (tmp_path / "executed").unlink()
        public = self.cli(
            tmp_path, {"PYTHONOPTIMIZE": "1", "PYTHONDONTWRITEBYTECODE": "", "TMPDIR": str(owned)}
        )
        assert public.returncode == (EXIT_PASS if passing else EXIT_FAIL), public.stderr
        assert (tmp_path / "executed").read_text() == "once"
        if not passing:
            assert "insufficient structured pytest evidence" in public.stderr
        assert "capture cleanup failed" not in public.stderr
        assert not list(owned.iterdir())

    @pytest.mark.parametrize("filename", ["-n_test.py", "--dist=x.py"])
    @pytest.mark.parametrize("bytecode", [False, True])
    def test_dash_filename_after_terminator_keeps_known_failure_waiver(
        self, tmp_path, filename, bytecode
    ):
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n    assert False\n"
        (tmp_path / filename).write_text(source)
        command = ["python3", *([] if bytecode else ["-B"]), "-m", "pytest", "-q", "--", filename]
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        original = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=15)
        bare_rejected = original.returncode == 4
        if bare_rejected:
            assert (sys.implementation.cache_tag, pytest.__version__) == ("cpython-312", "9.1.1")
            assert b"unrecognized arguments" in original.stderr
            assert not (tmp_path / "executed").exists()
        else:
            assert original.returncode == 1, original.stderr
            assert (tmp_path / "executed").read_text() == "once"
        self.public(
            tmp_path,
            "def test_placeholder(): pass\n",
            {filename + "::test_known": "failed"},
            command=command,
        )
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH", "")]
        )
        if not bare_rejected:
            (tmp_path / "executed").unlink()
        public = subprocess.run(
            ["python3", "-B", "-m", "code_forge", "gate-check", "--no-color"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if bare_rejected:
            assert public.returncode == EXIT_FAIL, public.stderr
            assert "unrecognized arguments" in public.stderr
            assert not (tmp_path / "executed").exists()
            command[-1] = "./" + filename
            original = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=15)
            assert original.returncode == 1, original.stderr
            assert (filename + "::test_known").encode() in original.stdout
            assert (tmp_path / "executed").read_text() == "once"
            config = tmp_path / ".code-forge" / "gate.yaml"
            data = yaml.safe_load(config.read_text())
            data["test"]["command"] = command
            config.write_text(yaml.safe_dump(data))
            (tmp_path / "executed").unlink()
            public = self.cli(tmp_path)
        assert public.returncode == EXIT_PASS, public.stderr
        assert (tmp_path / "executed").read_text() == "once"
        assert "all failures are known" in public.stderr

    def test_runner_option_before_terminator_still_refuses(self, tmp_path):
        (tmp_path / "runner_option.py").write_text(
            "def pytest_addoption(parser):\n    parser.addoption('--dist')\n"
        )
        source = "from pathlib import Path\ndef test_known():\n    Path('executed-before').write_text('once')\n    assert False\n"
        (tmp_path / "test_sample.py").write_text(source)
        command = [
            "python3",
            "-B",
            "-m",
            "pytest",
            "-p",
            "runner_option",
            "--dist=load",
            "-q",
            "--",
            "test_sample.py",
        ]
        plugin_path = os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])
        env = {**os.environ, "PYTHONPATH": plugin_path}
        original = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=15)
        assert original.returncode == 1, original.stderr
        assert (tmp_path / "executed-before").read_text() == "once"
        result, error = self.public(
            tmp_path,
            source,
            {"test_sample.py::test_known": "failed"},
            command=command,
            extra_env={"PYTHONPATH": plugin_path},
        )
        assert result == EXIT_FAIL
        assert "insufficient structured pytest evidence" in error

    def test_importing_plugin_after_terminator_cannot_hide_nested_pytest(self, tmp_path):
        package = tmp_path / "earlier"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "py.py").write_text(
            "from pathlib import Path\nimport pytest\n"
            "Path('plugin-imported').write_text('yes')\n"
            "pytest.main(['--collect-only', '-q', 'nested.py'])\n"
        )
        (tmp_path / "nested.py").write_text("def test_nested(): pass\n")
        filename = "-pearlier.py"
        (tmp_path / filename).write_text("def test_known():\n    assert False\n")
        command = ["python3", "-B", "-m", "pytest", "-q", "--", filename]
        result, error = self.public(
            tmp_path,
            "def test_placeholder():\n    assert False\n",
            {filename + "::test_known": "failed"},
            command=command,
            extra_env={"PYTHONPATH": os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])},
        )
        assert (tmp_path / "plugin-imported").read_text() == "yes"
        assert result == EXIT_FAIL, error
        assert "insufficient structured pytest evidence" in error

    @pytest.mark.parametrize("passing", [False, True])
    @pytest.mark.parametrize(
        "shape",
        [
            "ordinary",
            "instance",
            "instance_identity",
            "bound",
            "class",
            "class_shadow",
            "other",
            "pretend",
            "subclass",
        ],
    )
    def test_early_cleanup_identity_preserves_execution(self, tmp_path, monkeypatch, shape, passing):
        from code_forge import gate_check, _gate_pytest

        plugin = """from pathlib import Path
from types import MethodType
import copy
import pytest
@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    original = early_config._ensure_unconfigure
    def noop(*args, **kwargs):
        assert shape != 'instance_identity' or early_config._ensure_unconfigure is noop
        path = Path('replacement-calls')
        path.write_text((path.read_text() if path.exists() else '') + 'x')
    shape = SHAPE
    if shape in ('instance', 'instance_identity'):
        early_config._ensure_unconfigure = noop
    elif shape == 'bound':
        early_config._ensure_unconfigure = MethodType(noop, early_config)
    elif shape in ('class', 'class_shadow'):
        type(early_config)._ensure_unconfigure = noop
        if shape == 'class_shadow':
            early_config._ensure_unconfigure = original
    elif shape == 'other':
        early_config._ensure_unconfigure = MethodType(original.__func__, copy.copy(early_config))
    elif shape == 'pretend':
        class Pretend:
            __func__ = staticmethod(original.__func__)
            __self__ = early_config
            __call__ = staticmethod(noop)
        early_config._ensure_unconfigure = Pretend()
    elif shape == 'subclass':
        early_config.__class__ = type('DerivedConfig', (type(early_config),), {})
def pytest_unconfigure(config):
    Path('real-unconfigure-ran').write_text('ran')
"""
        (tmp_path / "earlycleanup.py").write_text(plugin.replace("SHAPE", repr(shape)))
        source = "from pathlib import Path\ndef test_known():\n    Path('executed').write_text('once')\n"
        source += "    assert " + repr(passing) + "\n"
        (tmp_path / "test_sample.py").write_text(source)
        command = ["python3", "-B", "-m", "pytest", "-q", "test_sample.py"]
        env = dict(os.environ)
        extra = {
            "PYTEST_PLUGINS": "earlycleanup",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": os.pathsep.join([str(tmp_path), str(Path(pytest.__file__).parents[1])]),
        }
        env.update(extra)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        original = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=30)
        assert original.returncode == (0 if passing else 1), original.stderr
        assert (tmp_path / "executed").read_text() == "once"
        for name in ("executed", "replacement-calls", "real-unconfigure-ran"):
            (tmp_path / name).unlink(missing_ok=True)
        measured = []
        run = gate_check.run_captured_pytest

        def observe(*args, **kwargs):
            result = run(*args, **kwargs)
            verdict = _gate_pytest.read_pytest_evidence(
                kwargs["capture"], child_pid=result.pid, returncode=result.returncode
            )
            capture = kwargs["capture"]
            violation = capture.directory / "violation"
            measured.append(
                (result, verdict, capture, violation.read_text() if violation.exists() else "")
            )
            return result

        monkeypatch.setattr(gate_check, "run_captured_pytest", observe)
        result, diagnostic = self.public(
            tmp_path,
            source,
            {"test_sample.py::test_known": "failed"},
            command=command,
            extra_env=extra,
        )
        assert len(measured) == 1
        child, verdict, capture, refusal = measured[0]
        assert child.returncode == original.returncode, (child.stdout, child.stderr)
        assert child.reaped and child.pipes_closed and capture.fd is None
        assert not capture.directory.exists() and not capture.cleanup_error
        assert (tmp_path / "executed").read_text() == "once"
        if shape in ("instance", "instance_identity", "bound", "class", "pretend"):
            assert (tmp_path / "replacement-calls").is_file(), (child.stdout, child.stderr)
            assert (tmp_path / "replacement-calls").read_text() == "xx"
        assert (tmp_path / "real-unconfigure-ran").exists() == (
            shape in ("ordinary", "class_shadow", "subclass")
        )
        expected = EXIT_PASS if passing or shape == "ordinary" else EXIT_FAIL
        assert result == expected, diagnostic
        if not passing:
            assert verdict.valid == (shape == "ordinary"), verdict.reason
        assert refusal == ("" if shape == "ordinary" else "replaced incoming cleanup boundary")

    @pytest.mark.parametrize(
        "damage", ["missing", "json", "fifo", "symlink", "reporter", "reporter-read"]
    )
    def test_bootstrap_io_refuses_without_changing_pytest(self, tmp_path, monkeypatch, damage):
        from code_forge import gate_check

        marker = tmp_path / "executed"
        sentinel = tmp_path / "sentinel"
        sentinel.write_bytes(b"preserved")
        captures = []
        prepare = gate_check.prepare_pytest_capture
        run = gate_check.run_captured_pytest
        observed = []

        def changed(*args, **kwargs):
            if damage in ("reporter", "reporter-read"):
                reporter = tmp_path / "reporter.py"
                reporter.write_bytes(kwargs["reporter_path"].read_bytes())
                if damage == "reporter-read":
                    with reporter.open("a") as stream:
                        stream.write(
                            "\n_saved_digest=_digest\ndef _digest(path,**kwargs):\n    if str(path)==__file__: raise OSError(5,'owned origin unavailable')\n    return _saved_digest(path,**kwargs)\n"
                        )
                kwargs["reporter_path"] = reporter
            capture = prepare(*args, **kwargs)
            captures.append(capture)
            binding = capture.directory / "binding.json"
            if damage == "reporter":
                reporter.unlink()
            elif damage == "reporter-read":
                pass
            else:
                binding.unlink()
                if damage == "json":
                    binding.write_bytes(b"{")
                elif damage == "fifo":
                    os.mkfifo(binding)
                elif damage == "symlink":
                    binding.symlink_to(sentinel)
            return capture

        def measured(*args, **kwargs):
            result = run(*args, **kwargs)
            observed.append(result)
            return result

        monkeypatch.setattr(gate_check, "prepare_pytest_capture", changed)
        monkeypatch.setattr(gate_check, "run_captured_pytest", measured)
        source = (
            "from pathlib import Path\ndef test_known():\n    Path("
            + repr(str(marker))
            + ").write_text('once')\n    assert False\n"
        )
        result, error = self.public(
            tmp_path,
            source,
            {"test_sample.py::test_known": "failed"},
            command=["python3", "-B", "-m", "pytest", "-q", "test_sample.py"],
        )
        assert observed[0].returncode == 1, error
        assert observed[0].reaped and observed[0].pipes_closed
        assert marker.is_file(), error
        assert marker.read_text() == "once"
        assert result == EXIT_FAIL, error
        assert sentinel.read_bytes() == b"preserved"
        capture = captures[0]
        if capture.directory.exists():
            # Remove only this fixture's substituted entry, then retry the owned manifest.
            (capture.directory / "binding.json").unlink()
            capture.fd = os.open(capture.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            assert capture.close()

    @pytest.mark.parametrize("success", [False, True])
    def test_public_e2big_fallback_preserves_original_result(self, tmp_path, monkeypatch, success):
        import errno
        from code_forge import _gate_pytest

        original = _gate_pytest.subprocess.Popen
        attempted = []

        def spawn(command, *args, **kwargs):
            if isinstance(command, list) and any(arg.startswith("_forge_gate_") for arg in command):
                attempted.append(command)
                raise OSError(errno.E2BIG, "instrumentation overhead")
            return original(command, *args, **kwargs)

        monkeypatch.setattr(_gate_pytest.subprocess, "Popen", spawn)
        marker = tmp_path / "executed"
        source = (
            "from pathlib import Path\ndef test_known():\n    Path("
            + repr(str(marker))
            + ").write_text('once')\n    assert "
            + str(success)
            + "\n"
        )
        result, error = self.public(tmp_path, source, {"test_sample.py::test_known": "failed"})
        assert len(attempted) == 1
        assert marker.is_file(), error
        assert marker.read_text() == "once"
        assert result == (EXIT_PASS if success else EXIT_FAIL), error

    @pytest.mark.parametrize(
        "target,success",
        [
            ("violation", False),
            ("begin.json", False),
            ("final.json", False),
            ("begin.json", True),
            ("final.json", True),
        ],
    )
    def test_producer_owned_io_preserves_execution(self, tmp_path, monkeypatch, target, success):
        from code_forge import gate_check

        if not Path("/dev/full").exists():
            pytest.skip("real ENOSPC device unavailable")
        marker = tmp_path / "executed"
        filename = os.fsdecode(b"test_non_utf8_\x80.py") if target == "violation" else "test_sample.py"
        source = (
            "from pathlib import Path\ndef test_known():\n    Path("
            + repr(str(marker))
            + ").write_text('executed')\n    assert "
            + str(success)
            + "\n"
        )
        (tmp_path / filename).write_text(source)
        plugin = (
            "import json, os\nfrom pathlib import Path\noriginal=Path.open\n"
            "transport=json.loads(os.environ['FORGE_GATE_BINDING']) if 'FORGE_GATE_BINDING' in os.environ else None\n"
            "binding=json.loads(Path(transport['path']).read_text()) if transport is not None else None\n"
            "def owned_open(path,*args,**kwargs):\n    stream=original(path,*args,**kwargs)\n"
            "    if binding is not None and str(path)==str(Path(binding['directory'])/"
            + repr(target)
            + "):\n"
            "        full=os.open('/dev/full',os.O_WRONLY|os.O_CLOEXEC)\n"
            "        try: os.dup2(full,stream.fileno())\n        finally: os.close(full)\n"
            "    return stream\nPath.open=owned_open\n"
        )
        (tmp_path / "owned_full.py").write_text(plugin)
        extra = {
            "PYTEST_PLUGINS": "owned_full",
            "PYTHONPATH": str(tmp_path) + os.pathsep + str(Path(pytest.__file__).parents[1]),
        }
        command = [
            "python3",
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-s",
            "--tb=line",
            ".",
        ]
        original = subprocess.run(
            command,
            cwd=tmp_path,
            env=dict(os.environ, **extra),
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert original.returncode == (0 if success else 1)
        assert marker.read_text() == "executed"
        marker.unlink()
        observed = []
        run = gate_check.run_captured_pytest

        def capture(*args, **kwargs):
            result = run(*args, **kwargs)
            observed.append(result)
            return result

        monkeypatch.setattr(gate_check, "run_captured_pytest", capture)
        baseline = {filename + "::test_known": "failed"}
        result, error = self.public(
            tmp_path,
            source if filename == "test_sample.py" else "def test_placeholder(): pass\n",
            baseline,
            command=command,
            extra_env=extra,
        )
        assert observed[0].returncode == original.returncode, error
        assert observed[0].reaped and observed[0].pipes_closed
        assert marker.is_file(), error
        assert marker.read_text() == "executed"
        assert result == (EXIT_PASS if success else EXIT_FAIL), error

    def test_large_executable_argv_preserves_success(self, tmp_path):
        command = ["python3", "-B", "-m", "pytest", "--version"] + [
            "test_example.py::test_known[" + str(i) + ":" + "x" * 45 + "]" for i in range(1400)
        ]
        original = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=10, check=False)
        assert original.returncode == 0
        result, error = self.public(tmp_path, "def test_known(): pass\n", {}, command=command)
        assert result == EXIT_PASS, error

    def test_large_executable_argv_known_failure(self, tmp_path):
        source = "def test_known():\n    assert False\n"
        (tmp_path / "test_sample.py").write_text(source)
        command = ["python3", "-B", "-m", "pytest", "-q", *(["--color=no"] * 10000), "test_sample.py"]
        original = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=10, check=False)
        assert original.returncode == 1
        result, error = self.public(
            tmp_path, source, {"test_sample.py::test_known": "failed"}, command=command
        )
        assert result == EXIT_PASS, error

    def test_default_helper_keeps_passing_and_bootstrap_behavior(self):
        assert compute_baseline_delta("FAILED test.py::x", None) == (False, [])
        assert compute_baseline_delta("3 passed", {}) == (False, [])
        assert compute_baseline_delta("ERROR test.py::x", {"test_results": {}}) == (False, [])

    def public(
        self,
        tmp_path,
        source,
        baseline,
        *,
        command=None,
        extra_env=None,
        args=(),
        write_null_baseline=False,
    ):
        def git(*args):
            return subprocess.run(
                ["git", *args], cwd=tmp_path, capture_output=True, text=True, timeout=2, check=True
            )

        inherited = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        for env in (dict(os.environ), inherited):
            outside = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                timeout=2,
                check=False,
            )
            assert outside.returncode != 0, outside.stdout
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "--allow-empty", "-qm", "base")
        (tmp_path / "test_sample.py").write_text(source)
        directory = tmp_path / ".code-forge"
        directory.mkdir()
        if command is None:
            command = ["python3", "-m", "pytest", "-q", *args, "test_sample.py"]
        (directory / "gate.yaml").write_text(
            yaml.safe_dump({"test": {"command": command, "timeout_seconds": 30}})
        )
        if baseline is not None or write_null_baseline:
            (directory / "test_baseline.json").write_text(
                json.dumps({"schema_version": 1, "test_results": baseline})
            )
        git("add", "test_sample.py")
        env = dict(inherited)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        if extra_env:
            env.update(extra_env)
        error = StringIO()
        result = run_gate_check(env=env, cwd=tmp_path, stdout=StringIO(), stderr=error)
        return result, error.getvalue()

    def test_generic_stdout_cannot_waive(self, tmp_path):
        (tmp_path / "runner.py").write_text(
            "print('FAILED test_sample.py::test_known')\nraise SystemExit(1)\n"
        )
        result, error = self.public(
            tmp_path,
            "def test_known(): pass\n",
            {"test_sample.py::test_known": "failed"},
            command=["python3", "runner.py"],
        )
        assert result == EXIT_FAIL, error

    @pytest.mark.skipif(os.name != "posix", reason="POSIX byte filename")
    def test_non_utf8_filename_runs_and_refuses_waiver(self, tmp_path):
        filename = os.fsdecode(b"test_non_utf8_\x80.py")
        marker = tmp_path / "executed"
        source = (
            "from pathlib import Path\ndef test_unknown():\n    Path("
            + repr(str(marker))
            + ").write_text('executed')\n    assert False\n"
        )
        (tmp_path / filename).write_text(source)
        command = [
            "python3",
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-s",
            "--tb=line",
            ".",
        ]
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        original = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert original.returncode == 1, original.stderr
        assert marker.read_text() == "executed"
        marker.unlink()
        result, error = self.public(
            tmp_path,
            "def test_placeholder(): pass\n",
            {filename + "::test_unknown": "failed"},
            command=command,
        )
        assert result == EXIT_FAIL, error
        assert marker.exists(), error
        assert marker.read_text() == "executed"
        assert "internal error" not in error

    @pytest.mark.parametrize("status", ["passed", "skipped", "unknown", None, 1, {}, []])
    def test_only_exact_failed_status_is_known(self, status):
        block, nodes = compute_baseline_delta(
            "FAILED test.py::test_x\n", {"test_results": {"test.py::test_x": status}}
        )
        assert block and nodes == ["test.py::test_x"]

    @pytest.mark.parametrize(
        "args", [["-qq", "-s", "--tb=line"], ["-q"], ["-vv"], ["--force-short-summary"]]
    )
    def test_actual_known_failure_and_logs(self, tmp_path, args):
        result, error = self.public(
            tmp_path,
            "import sys\ndef test_known():\n    print('ERROR fabricated')\n    print('FAILED unknown', file=sys.stderr)\n    assert False\n",
            {"test_sample.py::test_known": "failed"},
            args=args,
            extra_env={"CI": "1", "BUILD_NUMBER": "42"},
        )
        assert result == EXIT_PASS, error

    @pytest.mark.parametrize(
        "baseline,opt_in,expected",
        [
            (None, "0", EXIT_FAIL),
            (None, "1", EXIT_PASS),
            ({}, "0", EXIT_FAIL),
            ({"test_sample.py::test_known": "failed"}, "0", EXIT_PASS),
        ],
    )
    def test_public_exit_one_policy(self, tmp_path, baseline, opt_in, expected):
        result, error = self.public(
            tmp_path,
            "def test_known():\n    assert False\n",
            baseline,
            extra_env={"FORGE_ALLOW_NO_BASELINE": opt_in},
        )
        assert result == expected, error

    @pytest.mark.parametrize(
        "source,args",
        [
            ("def test_known():\n    assert False\ndef test_unknown():\n    assert False\n", []),
            (
                "import pytest\n@pytest.fixture\ndef broken():\n    assert False\ndef test_known():\n    assert False\ndef test_error(broken):\n    pass\n",
                [],
            ),
            ("def test_known():\n    assert False\ndef test_later():\n    pass\n", ["-x"]),
        ],
    )
    def test_new_error_and_partial_block(self, tmp_path, source, args):
        result, error = self.public(
            tmp_path, source, {"test_sample.py::test_known": "failed"}, args=args
        )
        assert result == EXIT_FAIL, error

    def test_invalid_receipt_never_waives(self, tmp_path, monkeypatch):
        from code_forge import _gate_pytest, gate_check

        monkeypatch.setattr(
            gate_check,
            "read_pytest_evidence",
            lambda *a, **kw: _gate_pytest.EvidenceResult(
                False, "malformed receipt", ["test_sample.py::test_known"]
            ),
        )
        result, error = self.public(
            tmp_path, "def test_known():\n    assert False\n", {"test_sample.py::test_known": "failed"}
        )
        assert result == EXIT_FAIL, error

    @pytest.mark.parametrize("results", [[], None, "invalid"])
    def test_malformed_baseline_results_block(self, tmp_path, results):
        result, error = self.public(
            tmp_path,
            "def test_known():\n    assert False\n",
            results,
            extra_env={"FORGE_ALLOW_NO_BASELINE": "0"},
            write_null_baseline=True,
        )
        assert result == EXIT_FAIL, error

    @pytest.mark.parametrize("error", [False, True])
    def test_actual_custom_error_routing(self, tmp_path, error):
        node = "custom path::test[" + "long " * 140 + "]"
        plugin = (
            "import sys\ndef pytest_collection_modifyitems(items):\n    items[0]._nodeid="
            + repr(node)
            + "\ndef pytest_terminal_summary(terminalreporter):\n    print('================ short test summary info ================')\n    print('ERROR fabricated::node', file=sys.stderr)\n    print('============ 1 failed, 0 errors in 0.01s ============')\n"
        )
        (tmp_path / "conftest.py").write_text(plugin)
        source = "def test_known():\n    assert False\n"
        if error:
            source += "import pytest\n@pytest.fixture\ndef broken():\n    assert False\ndef test_error(broken):\n    pass\n"
        result, diagnostic = self.public(tmp_path, source, {node: "failed"})
        assert result == (EXIT_FAIL if error else EXIT_PASS), diagnostic

    def test_real_launch_permission_error_blocks(self, tmp_path):
        binary = tmp_path / "bin"
        binary.mkdir()
        (binary / "python3").write_text("#!/bin/sh\nexit 0\n")
        result, diagnostic = self.public(
            tmp_path,
            "def test_known():\n    assert False\n",
            {"test_sample.py::test_known": "failed"},
            extra_env={"PATH": str(binary)},
        )
        assert result == EXIT_FAIL and "test invocation failed" in diagnostic

    @pytest.mark.parametrize("passing", [False, True])
    def test_cleanup_failure_refuses_waiver_but_preserves_rc0(self, tmp_path, monkeypatch, passing):
        from code_forge import gate_check

        original = gate_check.prepare_pytest_capture
        captures = []

        def prepare(*args, **kwargs):
            capture = original(*args, **kwargs)
            (capture.directory / "unknown").write_bytes(b"preserved")
            captures.append(capture)
            return capture

        monkeypatch.setattr(gate_check, "prepare_pytest_capture", prepare)
        result, diagnostic = self.public(
            tmp_path,
            "def test_known():\n    assert " + str(passing) + "\n",
            {"test_sample.py::test_known": "failed"},
        )
        assert result == (EXIT_PASS if passing else EXIT_FAIL), diagnostic
        assert "capture cleanup failed" in diagnostic
        capture = captures[0]
        assert (capture.directory / "unknown").read_bytes() == b"preserved"
        for name in ("binding.json", "begin.json", "final.json", capture.module + ".py", "unknown"):
            (capture.directory / name).unlink()
        capture.directory.rmdir()


# --- Parse + Translate + FAIL-OPEN ---


class TestLoadGateConfig:
    def test_valid_config(self):
        """Loads gate.yaml and returns dict."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
  env:
    PYTHONPATH: "src"
  timeout_seconds: 120
  cwd: "."
  source_patterns: ["*.py"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["test"]["command"] == ["python3", "-m", "pytest"]
        assert config["test"]["env"]["PYTHONPATH"] == "src"

    def test_missing_file_raises(self):
        """FileNotFoundError when file absent."""

        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("gate.yaml not found")

        with pytest.raises(FileNotFoundError):
            load_gate_config("gate.yaml", fs_open=raise_fnf)

    def test_invalid_yaml_raises(self):
        """ValueError on malformed YAML."""
        m = mock_open(read_data="{ invalid yaml")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_missing_command_raises(self):
        """ValueError when test.command missing."""
        yaml_content = "test:\n  env: {}\n"
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="command.*required"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_non_string_command_element_in_load_gate_config_raises(self):
        """ValueError when test.command has non-string elements."""
        yaml_content = "test:\n  command: [pytest, 42]\n"
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="elements must be strings"):
            load_gate_config("gate.yaml", fs_open=m)

        yaml_content_bool = "test:\n  command: [pytest, false]\n"
        m_bool = mock_open(read_data=yaml_content_bool)
        with pytest.raises(ValueError, match="elements must be strings"):
            load_gate_config("gate.yaml", fs_open=m_bool)

    def test_missing_test_section_error_contains_snippet(self):
        """Error message includes a pasteable YAML snippet."""
        yaml_content = "backends:\n  x:\n    type: cli\n"
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="Add:") as exc_info:
            load_gate_config("gate.yaml", fs_open=m)
        msg = str(exc_info.value)
        assert "command:" in msg
        assert "timeout_seconds:" in msg

    def test_error_snippet_satisfies_load_gate_config(self):
        """The YAML snippet in the error actually parses and loads."""
        no_test = "backends:\n  x:\n    type: cli\n"
        m = mock_open(read_data=no_test)
        try:
            load_gate_config("gate.yaml", fs_open=m)
        except ValueError as e:
            msg = str(e)
            snippet = msg.split("Add:\n", 1)[1]
        full_yaml = no_test + snippet + "\n"
        m2 = mock_open(read_data=full_yaml)
        config = load_gate_config("gate.yaml", fs_open=m2)
        assert "test" in config
        assert config["test"]["command"] == ["pytest", "-q"]


class TestValidateCommandSafety:
    def test_known_runner_accepted(self):
        """Known runners (python3, cargo, go) accepted."""
        validate_command_safety(["python3", "-m", "pytest"])
        validate_command_safety(["cargo", "test"])
        validate_command_safety(["go", "test", "./..."])
        # Should not raise

    def test_unknown_runner_rejected(self):
        """Unknown runners (rm, bash) rejected."""
        with pytest.raises(ValueError, match="Unknown test runner"):
            validate_command_safety(["rm", "-rf", "/"])
        with pytest.raises(ValueError, match="Unknown test runner"):
            validate_command_safety(["bash", "-c", "echo"])

    def test_metachar_rejected(self):
        """Shell metacharacters (|, ;, &) rejected."""
        with pytest.raises(ValueError, match="metacharacter"):
            validate_command_safety(["python3", "-c", "import os; os.system('ls')"])
        with pytest.raises(ValueError, match="metacharacter"):
            validate_command_safety(["pytest", "tests/", "|", "grep", "PASS"])

    def test_non_string_element_rejected(self):
        """Non-string elements rejected."""
        with pytest.raises(ValueError, match="must be strings"):
            validate_command_safety(["pytest", 42])  # type: ignore[list-item]
        with pytest.raises(ValueError, match="must be strings"):
            validate_command_safety(["pytest", False])  # type: ignore[list-item]


class TestTranslateExitCode:
    def test_exit_0_allow(self):
        """Exit 0 -> 0 (allow)."""
        assert translate_exit_code(0) == 0

    def test_exit_1_block(self):
        """Exit 1 -> 1 (BLOCK - real failure)."""
        assert translate_exit_code(1) == 1

    def test_exit_2_warn(self):
        """Exit 2 -> 0 (warn, keyboard interrupt)."""
        assert translate_exit_code(2) == 0

    def test_exit_3_block(self):
        """Exit 3 -> 1 (BLOCK, internal error is not a completed test run)."""
        assert translate_exit_code(3) == 1

    def test_exit_4_block(self):
        """Exit 4 -> 1 (BLOCK - usage error)."""
        assert translate_exit_code(4) == 1

    def test_exit_5_block(self):
        """Exit 5 -> 1 (BLOCK - no tests collected)."""
        assert translate_exit_code(5) == 1

    def test_exit_99_block(self):
        """Exit 99 (unknown) -> 1 (BLOCK)."""
        assert translate_exit_code(99) == 1

    def test_timeout_block(self):
        """Timeout (represented as high exit code) -> 1 (BLOCK)."""
        assert translate_exit_code(124) == 1  # typical timeout exit


class TestFailOpenGuard:
    """FAIL-OPEN guard: config errors -> BLOCK (exit 1), never allow."""

    def test_missing_gate_yaml_blocks(self):
        """run_gate_check returns 1 (BLOCK) when gate.yaml missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            (cwd / ".code-forge").mkdir()
            # gate.yaml absent

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL
            assert "error" in stderr.getvalue().lower()

    def test_invalid_yaml_blocks(self):
        """run_gate_check returns 1 (BLOCK) on invalid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()
            (forge_dir / "gate.yaml").write_text("{ invalid")

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL

    def test_unsafe_command_blocks(self):
        """run_gate_check returns 1 (BLOCK) on unsafe command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["rm", "-rf", "/"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL

    def test_never_returns_exit_2(self):
        """Assert return != 2 for all error paths."""
        # This is a meta-test: run_gate_check MUST never return 2
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            (cwd / ".code-forge").mkdir()

            from io import StringIO

            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=StringIO())
            assert result != 2  # EXIT_CLI_ERROR
            assert result in (0, 1)  # Only PASS or FAIL


# --- CI Detection ---


class TestCIDetection:
    def test_forge_mode_ci(self):
        """FORGE_MODE=ci -> is_ci_mode True."""
        assert is_ci_mode({"FORGE_MODE": "ci"}) is True

    def test_forge_mode_ci_case_insensitive(self):
        """FORGE_MODE=CI -> True (case-insensitive)."""
        assert is_ci_mode({"FORGE_MODE": "CI"}) is True
        assert is_ci_mode({"FORGE_MODE": "Ci"}) is True

    def test_github_actions(self):
        """GITHUB_ACTIONS=true -> True."""
        assert is_ci_mode({"GITHUB_ACTIONS": "true"}) is True

    def test_gitlab_ci(self):
        """GITLAB_CI=true -> True."""
        assert is_ci_mode({"GITLAB_CI": "true"}) is True

    def test_jenkins_url(self):
        """JENKINS_URL=http://... -> True."""
        assert is_ci_mode({"JENKINS_URL": "http://jenkins"}) is True

    def test_build_url(self):
        """BUILD_URL=http://... -> True."""
        assert is_ci_mode({"BUILD_URL": "http://build"}) is True

    def test_ci_var(self):
        """CI=1 -> True."""
        assert is_ci_mode({"CI": "1"}) is True

    def test_no_ci_vars(self):
        """Empty env -> False."""
        assert is_ci_mode({}) is False

    def test_skip_tests_ignored_in_ci(self):
        """FORGE_SKIP_TESTS=1 + CI=1 -> tests still run."""
        # This is tested in run_gate_check integration tests
        # We verify is_ci_mode returns True so the skip logic is bypassed
        assert is_ci_mode({"CI": "1", "FORGE_SKIP_TESTS": "1"}) is True


# --- Baseline Delta ---


class TestBaselineDelta:
    def test_no_baseline_allows(self):
        """None baseline -> (False, []) -- allow (bootstrap)."""
        test_output = "FAILED tests/test_foo.py::test_bar\n"
        should_block, failures = compute_baseline_delta(test_output, None)
        assert should_block is False
        assert failures == []

    def test_known_failure_not_new(self):
        """Failure in baseline -> not new -> allow."""
        baseline = {"test_results": {"tests/test_foo.py::test_bar": "failed"}}
        test_output = "FAILED tests/test_foo.py::test_bar\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is False

    def test_new_failure_blocks(self):
        """Failure not in baseline -> NEW -> BLOCK."""
        baseline = {"test_results": {}}
        test_output = "FAILED tests/test_foo.py::test_new\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is True
        assert "tests/test_foo.py::test_new" in failures

    def test_new_test_passes_ok(self):
        """Test not in baseline, passes -> not a failure -> allow."""
        baseline = {"test_results": {}}
        test_output = "PASSED tests/test_foo.py::test_new\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is False

    def test_previously_passing_now_fails(self):
        """Regression: was passing, now fails -> NEW -> BLOCK."""
        baseline = {"test_results": {"tests/test_foo.py::test_bar": "passed"}}
        test_output = "FAILED tests/test_foo.py::test_bar\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is True
        assert "tests/test_foo.py::test_bar" in failures


# --- Source Pattern Matching ---


class TestSourcePatterns:
    def test_py_file_matches(self):
        """\"foo.py" matches ["*.py"]."""
        assert match_source_patterns(["foo.py"], ["*.py"]) is True

    def test_md_file_no_match(self):
        """\"README.md" does not match ["*.py"]."""
        assert match_source_patterns(["README.md"], ["*.py"]) is False

    def test_empty_patterns_matches_all(self):
        """[] patterns -> True (always run tests)."""
        assert match_source_patterns(["foo.py"], []) is True
        assert match_source_patterns(["README.md"], []) is True

    def test_no_staged_files_skips(self):
        """Empty file list -> False (skip tests)."""
        assert match_source_patterns([], ["*.py"]) is False
        assert match_source_patterns([], []) is False


# Integration Tests


class TestGateCheckIntegration:
    @pytest.fixture(autouse=True)
    def controlled_direct_boundary(self, monkeypatch):
        """Unit policy controls have explicit receipt authority; real runs live above."""
        from code_forge import _gate_pytest
        from code_forge import gate_check

        def captured(command, **kwargs):
            result = gate_check.subprocess.run(
                command,
                env=kwargs["test_env"],
                cwd=str(kwargs["test_cwd"]),
                timeout=kwargs["timeout_seconds"],
                capture_output=True,
                text=True,
                check=False,
            )
            result.pid = 123
            return result

        monkeypatch.setattr(gate_check, "run_captured_pytest", captured)
        monkeypatch.setattr(
            gate_check,
            "read_pytest_evidence",
            lambda *args, **kwargs: _gate_pytest.EvidenceResult(
                True, "controlled closed receipt", ["tests/test_foo.py::test_bar"]
            ),
        )

    """End-to-end tests for run_gate_check."""

    def test_skip_tests_in_local_mode(self):
        """FORGE_SKIP_TESTS=1 in local mode -> allow + warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={"FORGE_SKIP_TESTS": "1"},  # No CI vars
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_PASS
            assert "FORGE_SKIP_TESTS" in stderr.getvalue()

    def test_quiet_flag_suppresses_warnings(self):
        """args.quiet=True suppresses warning messages."""
        import types

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            args = types.SimpleNamespace(quiet=True)
            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=args,
                env={"FORGE_SKIP_TESTS": "1"},  # No CI vars
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_PASS
            # With quiet=True, the FORGE_SKIP_TESTS warning is suppressed
            assert stderr.getvalue() == ""

    @patch("code_forge.gate_check.subprocess.run")
    def test_skip_tests_ignored_in_ci_mode(self, mock_run):
        """FORGE_SKIP_TESTS=1 + CI=1 -> gate still runs (not skipped)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={"FORGE_SKIP_TESTS": "1", "CI": "1"},
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            # In CI mode, FORGE_SKIP_TESTS is ignored; test ran and passed
            assert result == EXIT_PASS
            assert "CI mode" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_test_pass_returns_pass(self, mock_run):
        """Test exit 0 -> gate-check returns PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Mock git diff --cached
            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test command
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO

            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=StringIO())
            assert result == EXIT_PASS

    @patch("code_forge.gate_check.subprocess.run")
    def test_test_fail_new_failure_returns_fail(self, mock_run):
        """Test exit 1 + NEW failure -> gate-check returns FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Empty baseline (all failures are new)
            baseline = {"schema_version": "1.0", "test_results": {}}
            (forge_dir / "test_baseline.json").write_text(json.dumps(baseline))

            # Mock subprocess
            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test command fails
                return Mock(returncode=1, stdout="FAILED tests/test_foo.py::test_bar\n", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL
            assert "NEW test failures" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_1_no_baseline_blocks_by_default(self, mock_run):
        """Test exit 1 + no baseline -> gate BLOCKS (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                return Mock(
                    returncode=1,
                    stdout="FAILED tests/test_foo.py::test_bar\n",
                    stderr="",
                )

            mock_run.side_effect = side_effect

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={},
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_FAIL
            assert "no baseline established" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_1_no_baseline_allows_with_opt_in(self, mock_run):
        """FORGE_ALLOW_NO_BASELINE=1 restores the old allow behavior."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                return Mock(
                    returncode=1,
                    stdout="FAILED tests/test_foo.py::test_bar\n",
                    stderr="",
                )

            mock_run.side_effect = side_effect

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={"FORGE_ALLOW_NO_BASELINE": "1"},
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_PASS
            assert "no baseline" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_4_blocks_regardless_of_baseline(self, mock_run):
        """Exit 4 (usage error) BLOCKs even with permissive baseline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Permissive baseline: known failure listed as failed
            baseline = {
                "schema_version": "1.0",
                "test_results": {"tests/test_foo.py::test_bar": "failed"},
            }
            (forge_dir / "test_baseline.json").write_text(json.dumps(baseline))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test runner exits with 4 (usage error)
                return Mock(returncode=4, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO

            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=StringIO())
            assert result == EXIT_FAIL, "exit 4 must BLOCK (1) regardless of baseline, got %d" % result

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_5_blocks_regardless_of_baseline(self, mock_run):
        """Exit 5 (no tests collected) BLOCKs even with empty baseline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Empty baseline: no known failures, so vacuous delta would PASS
            baseline = {"schema_version": "1.0", "test_results": {}}
            (forge_dir / "test_baseline.json").write_text(json.dumps(baseline))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test runner exits with 5 (no tests collected)
                return Mock(returncode=5, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO

            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=StringIO())
            assert result == EXIT_FAIL, "exit 5 must BLOCK (1) regardless of baseline, got %d" % result

    @patch("code_forge.gate_check.subprocess.run")
    def test_git_not_found_blocks(self, mock_run):
        """git not on PATH -> gate BLOCKs with clean error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    raise FileNotFoundError("git not found")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={},
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_FAIL
            assert "error" in stderr.getvalue().lower()

    @patch("code_forge.gate_check.subprocess.run")
    def test_runner_not_found_blocks(self, mock_run):
        """Test runner not on PATH -> gate BLOCKs with clean error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test runner not found
                raise FileNotFoundError("python3 not found")

            mock_run.side_effect = side_effect

            from io import StringIO

            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={},
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_FAIL
            assert "error" in stderr.getvalue().lower()


# --- Bug-inject tests ---


class TestBugInjectExitTranslation:
    """Break exit-code translation, verify tests catch it."""

    def test_all_block_codes_actually_block(self):
        """Every exit code that should BLOCK returns 1."""
        from code_forge.gate_check import translate_exit_code

        for code in [1, 4, 5, 99]:
            assert translate_exit_code(code) == 1, "exit %d should BLOCK (1)" % code


class TestBugInjectFailOpen:
    """Break FAIL-OPEN guard, verify tests catch it."""

    def test_config_error_must_block(self):
        """If config error returns 0, the gate fails open."""
        from io import StringIO

        with tempfile.TemporaryDirectory() as cwd:
            cwd = Path(cwd)
            (cwd / ".code-forge").mkdir()
            (cwd / ".code-forge" / "gate.yaml").write_text("{{invalid yaml")

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL, "config parse error must BLOCK (1), got %d" % result

    def test_missing_config_must_block(self):
        """If gate.yaml is missing, the gate must block."""
        from io import StringIO

        with tempfile.TemporaryDirectory() as cwd:
            cwd = Path(cwd)

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL, "missing gate.yaml must BLOCK (1), got %d" % result

    def test_unsafe_command_must_block(self):
        """If test.command has shell metacharacters, gate blocks."""
        from io import StringIO

        with tempfile.TemporaryDirectory() as cwd:
            cwd = Path(cwd)
            (cwd / ".code-forge").mkdir()
            (cwd / ".code-forge" / "gate.yaml").write_text(
                "---\ntest:\n  command: ['sh', '-c', 'rm -rf /']\n  timeout_seconds: 10\n  cwd: '.'\n"
            )

            stderr = StringIO()
            result = run_gate_check(args=None, env={}, cwd=cwd, stdout=StringIO(), stderr=stderr)
            assert result == EXIT_FAIL, "unsafe command must BLOCK (1), got %d" % result


# --- Presubmit Schema Validation ---


class TestPresubmitValidation:
    """Tests for presubmit section validation in load_gate_config
    and helper functions validate_presubmit_entry, validate_presubmit_command,
    and fnmatch_to_grep.
    """

    # -- load_gate_config: valid presubmit section --

    def test_valid_presubmit_list_loads_ok(self):
        """gate.yaml with valid presubmit list loads without error."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "presubmit" in config
        assert len(config["presubmit"]) == 1

    def test_valid_presubmit_with_when_exists_loads_ok(self):
        """gate.yaml with presubmit entry with valid when_exists string loads ok."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["scripts/checkpatch.pl", "--strict"]
    applies_to: "*.c"
    on: "patch"
    when_exists: "scripts/checkpatch.pl"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["presubmit"][0]["when_exists"] == "scripts/checkpatch.pl"

    def test_no_presubmit_section_loads_ok(self):
        """gate.yaml with no presubmit section loads ok (section is optional)."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "presubmit" not in config

    def test_on_diff_loads_ok(self):
        """gate.yaml with presubmit entry with on='diff' loads without error."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["presubmit"][0]["on"] == "diff"

    def test_on_patch_loads_ok(self):
        """gate.yaml with presubmit entry with on='patch' loads without error."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["scripts/checkpatch.pl"]
    applies_to: "*.c"
    on: "patch"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["presubmit"][0]["on"] == "patch"

    # -- load_gate_config: non_ascii field --

    def test_non_ascii_ai_smell_loads_ok(self):
        """gate.yaml with non_ascii: 'ai-smell' loads ok."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
non_ascii: "ai-smell"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["non_ascii"] == "ai-smell"

    def test_non_ascii_strict_loads_ok(self):
        """gate.yaml with non_ascii: 'strict' loads ok."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
non_ascii: "strict"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["non_ascii"] == "strict"

    def test_non_ascii_absent_defaults_to_ai_smell(self):
        """gate.yaml with no non_ascii field defaults to 'ai-smell'."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config.get("non_ascii", "ai-smell") == "ai-smell"

    def test_non_ascii_unknown_raises(self):
        """gate.yaml with non_ascii: 'unknown-value' raises ValueError (fail-closed)."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
non_ascii: "unknown-value"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="non_ascii"):
            load_gate_config("gate.yaml", fs_open=m)

    # -- load_gate_config: presubmit error cases --

    def test_presubmit_non_list_raises(self):
        """gate.yaml with presubmit as non-list raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  command: ["go", "vet", "./..."]
  applies_to: "*.go"
  on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="presubmit"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_missing_command_raises(self):
        """gate.yaml with presubmit entry missing 'command' raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="presubmit.*command"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_metachar_in_command_raises(self):
        """gate.yaml with presubmit entry command containing '|' raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./...", "|", "grep", "error"]
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_command_not_list_raises(self):
        """gate.yaml with presubmit entry command that is not a list raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: "go vet ./..."
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_on_message_raises(self):
        """gate.yaml with presubmit entry with on='message' raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "message"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="message"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_on_invalid_value_raises(self):
        """gate.yaml with presubmit entry with invalid 'on' value raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "staged"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_applies_to_non_string_raises(self):
        """gate.yaml with presubmit entry applies_to as non-string raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to:
      - "*.go"
      - "*.rs"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_applies_to_single_quote_raises(self):
        """gate.yaml with applies_to containing single-quote raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go'"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_applies_to_double_quote_raises(self):
        """gate.yaml with applies_to containing double-quote raises ValueError."""
        yaml_content = r"""
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: '*.go"'
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_when_exists_single_quote_raises(self):
        """gate.yaml with when_exists containing single-quote raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
    when_exists: "scripts/check'.pl"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_when_exists_double_quote_raises(self):
        """gate.yaml with when_exists containing double-quote raises ValueError."""
        yaml_content = r"""
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
    when_exists: 'scripts/check".pl'
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    # -- validate_presubmit_command --

    def test_validate_presubmit_command_rejects_metachar_in_each_element(self):
        """validate_presubmit_command rejects metacharacters in each element."""
        for meta in list("|;&$><`"):
            bad_command = ["go", "vet", "./..." + meta]
            with pytest.raises(ValueError):
                validate_presubmit_command(bad_command)

    def test_validate_presubmit_command_rejects_percent_in_command(self):
        """validate_presubmit_command rejects % in command elements.

        A % in a command arg causes TypeError at hook-generation time because
        _build_presubmit_block uses Python % string formatting with the command.
        """
        with pytest.raises(ValueError, match="Percent sign"):
            validate_presubmit_command(["checkpatch%", "--strict"])
        with pytest.raises(ValueError, match="Percent sign"):
            validate_presubmit_command(["lint", "--flag=%s"])

    # -- fnmatch_to_grep via real grep -E --

    def _grep_matches(self, pattern: str, text: str) -> bool:
        """Run real grep -E to test the pattern against text."""
        result = subprocess.run(
            ["grep", "-E", pattern],
            input=text,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def test_fnmatch_star_matches_zero_chars(self):
        """fnmatch_to_grep('test_*.py') matches 'test_.py' (star=zero chars OK)."""
        pattern = fnmatch_to_grep("test_*.py")
        assert self._grep_matches(pattern, "test_.py")

    def test_fnmatch_star_matches_nonempty(self):
        """fnmatch_to_grep('test_*.py') matches 'test_foo.py'."""
        pattern = fnmatch_to_grep("test_*.py")
        assert self._grep_matches(pattern, "test_foo.py")

    def test_fnmatch_star_no_match_wrong_ext(self):
        """fnmatch_to_grep('*.py') does NOT match 'foo.js' via real grep -E."""
        pattern = fnmatch_to_grep("*.py")
        assert not self._grep_matches(pattern, "foo.js")

    def test_fnmatch_star_matches_foo_py(self):
        """fnmatch_to_grep('*.py') matches 'foo.py' via real grep -E."""
        pattern = fnmatch_to_grep("*.py")
        assert self._grep_matches(pattern, "foo.py")

    def test_fnmatch_anchor_rejects_path_prefix(self):
        """fnmatch_to_grep('test_*.py') must NOT match 'src/test_foo.py'.

        Without the leading ^ anchor, grep -E 'test_.*\\.py$' matches the
        substring 'test_foo.py' inside 'src/test_foo.py'. The anchor ensures
        the pattern is applied to the full filename only.
        """
        pattern = fnmatch_to_grep("test_*.py")
        assert not self._grep_matches(pattern, "src/test_foo.py")


# --- graph_triage section validation ---


class TestGraphTriageValidation:
    """Tests for graph_triage section validation in load_gate_config."""

    def test_graph_triage_valid(self):
        """gate.yaml with valid graph_triage section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  enabled: true
  db_path: "/path/graph.db"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["graph_triage"]["enabled"] is True
        assert config["graph_triage"]["db_path"] == "/path/graph.db"

    def test_graph_triage_invalid_enabled(self):
        """graph_triage.enabled not bool raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  enabled: "yes"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="graph_triage"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_graph_triage_invalid_db_path(self):
        """graph_triage.db_path not string raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  db_path: 123
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="graph_triage"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_graph_triage_absent_ok(self):
        """gate.yaml without graph_triage section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "graph_triage" not in config

    def test_graph_triage_extra_keys_ok(self):
        """graph_triage section with unknown keys does not raise."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  enabled: true
  future_key: "some_value"
  another_key: 42
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["graph_triage"]["enabled"] is True
        assert config["graph_triage"]["future_key"] == "some_value"


# --- daemon_state validation (STATE-01g) ---


class TestDaemonStateValidation:
    """gate.yaml daemon_state section validation."""

    def test_daemon_state_valid(self):
        """Valid daemon_state section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
daemon_state:
  enabled: true
  subsystems: ["nftables", "routing"]
  patterns: ["flock", "pidfile"]
  conflicts:
    - subsystem: "killswitch"
      mutates: "nft mark"
      interferes_with: "health check probes"
  conflicts_file: "conflicts.yaml"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "daemon_state" in config

    def test_daemon_state_invalid_enabled(self):
        """daemon_state.enabled not bool raises ValueError."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="bool"):
            validate_daemon_state({"enabled": "yes"})

    def test_daemon_state_invalid_subsystems(self):
        """daemon_state.subsystems not list raises ValueError."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="list"):
            validate_daemon_state({"subsystems": "nftables"})

    def test_daemon_state_missing_triplet_field(self):
        """Conflict triplet missing 'mutates' raises ValueError with field name."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="mutates"):
            validate_daemon_state(
                {
                    "conflicts": [
                        {
                            "subsystem": "killswitch",
                            "interferes_with": "health check",
                            # missing "mutates"
                        },
                    ],
                }
            )

    def test_daemon_state_conflicts_file_string(self):
        """conflicts_file not string raises ValueError."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="string"):
            validate_daemon_state({"conflicts_file": 42})

    def test_daemon_state_absent_ok(self):
        """gate.yaml without daemon_state section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "daemon_state" not in config


class TestRetryConfig:
    """Tests for validate_retry_config and load_gate_config retry wiring."""

    def test_valid_full_config(self):
        """Both fields present and valid passes."""
        validate_retry_config({"max_attempts": 5, "initial_delay_s": 2})

    def test_valid_min_max_attempts(self):
        """max_attempts=1 (minimum) passes."""
        validate_retry_config({"max_attempts": 1})

    def test_valid_max_max_attempts(self):
        """max_attempts=10 (maximum) passes."""
        validate_retry_config({"max_attempts": 10})

    def test_valid_min_initial_delay(self):
        """initial_delay_s=0.1 (minimum) passes."""
        validate_retry_config({"initial_delay_s": 0.1})

    def test_valid_max_initial_delay(self):
        """initial_delay_s=30 (maximum) passes."""
        validate_retry_config({"initial_delay_s": 30})

    def test_valid_empty_dict(self):
        """Empty dict passes (all fields optional)."""
        validate_retry_config({})

    def test_max_attempts_below_min(self):
        """max_attempts=0 raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": 0})

    def test_max_attempts_above_max(self):
        """max_attempts=11 raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": 11})

    def test_max_attempts_wrong_type(self):
        """max_attempts='five' raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": "five"})

    def test_max_attempts_bool_rejected(self):
        """Bool is not int for max_attempts."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": True})

    def test_initial_delay_below_min(self):
        """initial_delay_s=0 raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": 0})

    def test_initial_delay_above_max(self):
        """initial_delay_s=31 raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": 31})

    def test_initial_delay_wrong_type(self):
        """initial_delay_s='two' raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": "two"})

    def test_not_a_dict(self):
        """Non-dict raises ValueError."""
        with pytest.raises(ValueError, match="mapping"):
            validate_retry_config("not a dict")

    def test_load_gate_config_with_retry(self):
        """load_gate_config calls validate_retry_config when retry present."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
retry:
  max_attempts: 3
  initial_delay_s: 1.5
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["retry"]["max_attempts"] == 3

    def test_load_gate_config_without_retry(self):
        """load_gate_config without retry does not raise."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "retry" not in config

    def test_load_gate_config_invalid_retry_rejected(self):
        """load_gate_config with invalid retry raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
retry:
  max_attempts: 0
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="max_attempts"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_initial_delay_bool_rejected(self):
        """Bool is not a number for initial_delay_s."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": True})

    def test_initial_delay_int_accepted(self):
        """Integer value for initial_delay_s is valid (int is a number)."""
        validate_retry_config({"initial_delay_s": 5})

    def test_retry_timeout_true_accepted(self):
        validate_retry_config({"retry_timeout": True})

    def test_retry_timeout_false_accepted(self):
        validate_retry_config({"retry_timeout": False})

    def test_retry_timeout_non_bool_rejected(self):
        with pytest.raises(ValueError, match="retry_timeout"):
            validate_retry_config({"retry_timeout": 1})

    def test_l1_pass_stagger_default_omitted(self):
        validate_retry_config({})

    def test_l1_pass_stagger_zero_accepted(self):
        validate_retry_config({"l1_pass_stagger_s": 0})

    def test_l1_pass_stagger_ten_accepted(self):
        validate_retry_config({"l1_pass_stagger_s": 10})

    def test_l1_pass_stagger_negative_rejected(self):
        with pytest.raises(ValueError, match="l1_pass_stagger_s"):
            validate_retry_config({"l1_pass_stagger_s": -1})

    def test_l1_pass_stagger_too_large_rejected(self):
        with pytest.raises(ValueError, match="l1_pass_stagger_s"):
            validate_retry_config({"l1_pass_stagger_s": 121})

    def test_l1_pass_stagger_bool_rejected(self):
        with pytest.raises(ValueError, match="l1_pass_stagger_s"):
            validate_retry_config({"l1_pass_stagger_s": True})

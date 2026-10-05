# SPDX-License-Identifier: Apache-2.0
"""The commit waiver consumes closed pytest outcomes, never terminal text."""

import json
import contextlib
import errno
import pwd
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import Mock

import pytest

from code_forge import _gate_pytest as evidence


def test_unsupported_legacy_marker_returns_without_hooks(monkeypatch):
    import _pytest.config as actual_config

    refused, decorated = [], []

    class MemoryRecorder:
        def __init__(self, *args):
            pass

        def refuse(self, reason):
            refused.append(reason)

    def legacy_marker(
        function=None,
        hookwrapper=False,
        optionalhook=False,
        tryfirst=False,
        trylast=False,
        specname=None,
    ):
        decorated.append(function)
        return function if function else lambda fn: fn

    legacy_pytest = ModuleType("pytest")
    legacy_pytest.__version__ = "7.1.3"
    legacy_pytest.hookimpl = legacy_marker
    package = ModuleType("_pytest")
    package.__path__ = []
    config = ModuleType("_pytest.config")
    original_prepare = actual_config._prepareconfig
    assert legacy_marker(original_prepare) is original_prepare
    assert legacy_marker(tryfirst=True)(original_prepare) is original_prepare
    with pytest.raises(TypeError, match="wrapper"):
        legacy_marker(wrapper=True)
    decorated.clear()
    config._prepareconfig = original_prepare
    package.config = config
    monkeypatch.setitem(sys.modules, "pytest", legacy_pytest)
    monkeypatch.setitem(sys.modules, "_pytest", package)
    monkeypatch.setitem(sys.modules, "_pytest.config", config)
    monkeypatch.setattr(evidence, "Recorder", MemoryRecorder)
    assert evidence.load_plugin({"module": "bootstrap"}, "unused") is None
    assert refused == ["unsupported pytest runtime 7.1.3"]
    assert decorated == [] and config._prepareconfig is original_prepare


@pytest.mark.parametrize("profile", ["version", "optimization", "cache"])
def test_unsupported_profile_does_not_install_hooks(tmp_path, monkeypatch, profile):
    import _pytest.config as config

    capture = evidence.prepare_pytest_capture(
        ["python3", "-B", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    binding = dict(capture.binding, parent_pid=os.getppid())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pytest", *capture.command[capture.prefix :]])
    original_prepare = config._prepareconfig
    if profile == "version":
        monkeypatch.setattr(pytest, "__version__", "unknown")
    elif profile == "optimization":
        flags = {name: getattr(sys.flags, name) for name in dir(sys.flags) if not name.startswith("_")}
        flags["optimize"] = 1
        monkeypatch.setattr(sys, "flags", SimpleNamespace(**flags))
    else:
        monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "other-cache"))

    marker = Mock(side_effect=AssertionError("Unsupported profile must not invoke a hook decorator"))
    monkeypatch.setattr(pytest, "hookimpl", marker)
    try:
        assert evidence.load_plugin(binding, binding["bootstrap_path"]) is None
        marker.assert_not_called()
        assert config._prepareconfig is original_prepare
        assert (capture.directory / "violation").read_text().startswith("unsupported pytest runtime")
    finally:
        assert capture.close()


def capture_run(
    tmp_path, source="def test_known():\n    assert False\n", args=(), env=None, bytecode=False
):
    (tmp_path / "test_sample.py").write_text(source, encoding="utf-8")
    child_env = dict(os.environ)
    child_env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), child_env.get("PATH", "")])
    if env:
        child_env.update(env)
    selected = shutil.which("python3", path=child_env["PATH"])
    if selected and Path(selected).resolve() == Path(sys.executable).resolve():
        site = str(Path(pytest.__file__).parents[1])
        inherited = child_env.get("PYTHONPATH", "")
        if site not in inherited.split(os.pathsep):
            child_env["PYTHONPATH"] = os.pathsep.join([inherited, site]) if inherited else site
    command = ["python3", *([] if bytecode else ["-B"]), "-m", "pytest", "-q", *args, "test_sample.py"]
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env=child_env, reporter_path=Path(evidence.__file__)
    )
    assert capture is not None
    result = evidence.run_captured_pytest(
        command, capture=capture, test_cwd=tmp_path, test_env=child_env, timeout_seconds=30
    )
    verdict = evidence.read_pytest_evidence(capture, child_pid=result.pid, returncode=result.returncode)
    return capture, result, verdict


def test_real_known_failure_is_complete(tmp_path):
    capture, result, verdict = capture_run(tmp_path)
    try:
        assert result.returncode == 1, result.stderr
        assert verdict.valid, verdict.reason
        assert verdict.failed_nodes == ["test_sample.py::test_known"]
        assert result.reaped and result.pipes_closed
    finally:
        assert capture.close()


def test_binding_file_transport_is_bounded_and_reread(tmp_path):
    capture, result, verdict = capture_run(tmp_path)
    try:
        binding_path = capture.directory / "binding.json"
        assert binding_path.is_file()
        assert len(json.dumps(capture.transport).encode()) <= evidence.MAX_TRANSPORT_BYTES
        assert "configured_argv" not in capture.transport
        assert verdict.valid
        original = binding_path.read_bytes()
        binding_path.write_bytes(original + b" ")
        assert not evidence.read_pytest_evidence(capture, child_pid=result.pid, returncode=1).valid
    finally:
        assert capture.close()


@pytest.mark.parametrize("case", ["binding", "envelope"])
def test_preparation_capacity_refusal_releases_owned_capture(tmp_path, monkeypatch, case):
    temporary = tmp_path
    deep_directories = []
    command = ["python3", "-B", "-m", "pytest"]
    if case == "binding":
        command.append("x" * (evidence.MAX_BYTES // 2 + 1000))
    else:
        assert os.pathconf(tmp_path, "PC_PATH_MAX") >= 4096
        while len(str(temporary)) < 3890:
            temporary = temporary / ("d" * max(1, min(180, 3889 - len(str(temporary)))))
            temporary.mkdir()
            deep_directories.append(temporary)
    created = []
    captures = []
    mkdtemp = evidence.tempfile.mkdtemp
    constructor = evidence.Capture

    def create(*args, **kwargs):
        kwargs["dir"] = str(temporary)
        directory = mkdtemp(*args, **kwargs)
        created.append(Path(directory))
        return directory

    def observe(*args, **kwargs):
        capture = constructor(*args, **kwargs)
        captures.append((capture, capture.fd))
        return capture

    monkeypatch.setattr(evidence.tempfile, "mkdtemp", create)
    monkeypatch.setattr(evidence, "Capture", observe)
    result = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env={}, reporter_path=Path(evidence.__file__)
    )
    assert result is None and len(captures) == 1 and len(created) == 1
    assert not created[0].exists()
    capture, fd = captures[0]
    assert capture.fd is None and not capture.cleanup_error
    with pytest.raises(OSError):
        os.fstat(fd)
    for directory in reversed(deep_directories):
        directory.rmdir()
    assert all(not directory.exists() for directory in deep_directories)


def test_parent_reread_rejects_rebound_binding(tmp_path):
    capture, result, verdict = capture_run(tmp_path)
    try:
        assert verdict.valid
        binding = dict(capture.binding, nonce="replacement")
        path = capture.directory / "binding.json"
        path.write_text(json.dumps(binding))
        digest, identity = evidence._digest(path)
        capture.transport.update(sha256=digest, identity=list(identity))
        verdict = evidence.read_pytest_evidence(capture, child_pid=result.pid, returncode=1)
        assert not verdict.valid and "binding file does not match" in verdict.reason
    finally:
        assert capture.close()


def test_parent_final_deadline_refuses_completed_evidence(tmp_path, monkeypatch):
    capture, result, verdict = capture_run(tmp_path)
    validate = evidence.validate_record
    clock = time.monotonic
    try:
        assert verdict.valid

        def completed(*args, **kwargs):
            measured = validate(*args, **kwargs)
            assert measured.valid
            expired = clock() + 3
            monkeypatch.setattr(evidence.time, "monotonic", lambda: expired)
            return measured

        monkeypatch.setattr(evidence, "validate_record", completed)
        verdict = evidence.read_pytest_evidence(capture, child_pid=result.pid, returncode=1)
        assert not verdict.valid and "deadline or directory" in verdict.reason
    finally:
        assert capture.close()


def test_physical_recorder_origin_io_remains_sticky_refusal(tmp_path, monkeypatch):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-B", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    digest = evidence._digest

    def unavailable(path, **kwargs):
        if Path(path).resolve() == Path(evidence.__file__).resolve():
            raise OSError(errno.EIO, "owned origin unavailable")
        return digest(path, **kwargs)

    monkeypatch.setattr(evidence, "_digest", unavailable)
    try:
        recorder = evidence.Recorder(capture.binding, capture.binding["bootstrap_path"])
        assert recorder.invalid == "reporter origin unavailable"
        assert (capture.directory / "violation").read_text() == recorder.invalid
        recorder.refuse("later refusal")
        assert recorder.invalid == "reporter origin unavailable"
    finally:
        assert capture.close()


@pytest.mark.parametrize("case", ["envelope", "oversized", "json", "fields", "values", "arguments"])
def test_bootstrap_transport_data_refuses(tmp_path, case):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-B", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    path = capture.directory / "binding.json"
    transport = dict(capture.transport)
    bootstrap = capture.binding["bootstrap_path"]
    try:
        if case == "envelope":
            transport["schema"] = True
        elif case == "oversized":
            assert (
                evidence.load_binding_transport("x" * (evidence.MAX_TRANSPORT_BYTES + 1), bootstrap)
                is None
            )
            return
        else:
            binding = dict(capture.binding)
            if case == "fields":
                binding.pop("nonce")
            elif case == "values":
                binding["parent_pid"] = False
            elif case == "arguments":
                binding["effective_argv"] = binding["configured_argv"]
            path.write_text("{" if case == "json" else json.dumps(binding))
            digest, identity = evidence._digest(path)
            transport.update(sha256=digest, identity=list(identity))
        assert evidence.load_binding_transport(json.dumps(transport), bootstrap) is None
    finally:
        assert capture.close()


@pytest.mark.parametrize("success", [False, True])
def test_e2big_fallback_runs_original_without_waiver(tmp_path, monkeypatch, success):
    original = evidence.subprocess.Popen
    attempts = []
    children = []
    marker = tmp_path / "executed"

    def spawn(command, **kwargs):
        attempts.append((list(command), dict(kwargs["env"])))
        if any(arg.startswith("_forge_gate_") for arg in command):
            raise OSError(errno.E2BIG, "instrumentation overhead")
        child = original(command, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(evidence.subprocess, "Popen", spawn)
    source = (
        "from pathlib import Path\ndef test_known():\n    Path("
        + repr(str(marker))
        + ").write_text('once')\n    assert "
        + str(success)
        + "\n"
    )
    capture, result, verdict = capture_run(tmp_path, source)
    try:
        assert len(attempts) == 2 and len(children) == 1
        assert attempts[1][0] == capture.binding["configured_argv"]
        assert "FORGE_GATE_BINDING" not in attempts[1][1]
        assert result.returncode == (0 if success else 1)
        assert marker.read_text() == "once"
        assert result.reaped and result.pipes_closed
        assert not verdict.valid
        if not success:
            record = valid_record()
            record.update(binding=capture.binding, pid=result.pid)
            (capture.directory / "begin.json").write_text(
                json.dumps({"binding": capture.binding, "pid": result.pid})
            )
            (capture.directory / "final.json").write_text(json.dumps(record))
            assert not evidence.read_pytest_evidence(capture, child_pid=result.pid, returncode=1).valid
    finally:
        assert capture.close()


def test_other_spawn_errors_do_not_fallback(tmp_path, monkeypatch):
    calls = []

    def spawn(*args, **kwargs):
        calls.append(args)
        raise OSError(errno.EACCES, "unavailable executable")

    command = ["python3", "-B", "-m", "pytest"]
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env={}, reporter_path=Path(evidence.__file__)
    )
    monkeypatch.setattr(evidence.subprocess, "Popen", spawn)
    try:
        with pytest.raises(OSError) as caught:
            evidence.run_captured_pytest(
                command, capture=capture, test_cwd=tmp_path, test_env={}, timeout_seconds=1
            )
        assert caught.value.errno == errno.EACCES and len(calls) == 1
    finally:
        assert capture.close()


@pytest.mark.parametrize(
    "source,args",
    [
        (
            "def test_known():\n    print('ERROR fake.py::test_bad')\n    assert False\n",
            ["-qq", "-s", "--tb=line"],
        ),
        (
            "import sys\ndef test_known():\n    print('FAILED fabricated', file=sys.stderr)\n    assert False\n",
            ["-vv"],
        ),
    ],
)
def test_logs_are_not_outcomes(tmp_path, source, args):
    capture, result, verdict = capture_run(tmp_path, source, args)
    try:
        assert result.returncode == 1
        assert verdict.valid, verdict.reason
        assert verdict.failed_nodes == ["test_sample.py::test_known"]
    finally:
        assert capture.close()


@pytest.mark.parametrize(
    "source,args",
    [
        (
            "import pytest\n@pytest.fixture\ndef broken():\n    assert False\ndef test_known():\n    assert False\ndef test_other(broken):\n    pass\n",
            [],
        ),
        (
            "import pytest\n@pytest.fixture\ndef broken():\n    yield\n    assert False\ndef test_known(broken):\n    assert False\n",
            [],
        ),
        ("def test_known():\n    assert False\ndef test_other():\n    pass\n", ["-x"]),
        (
            "import pytest, os\ndef test_known():\n    os.environ.pop('PYTEST_PLUGINS', None)\n    pytest.main(['--version'])\n    pytest.main(['-p', 'no:' + os.environ['FORGE_GATE_BOOTSTRAP'], '--collect-only', '-q'])\n    assert False\n",
            [],
        ),
    ],
)
def test_partial_errors_and_nested_runs_refuse(tmp_path, source, args):
    capture, result, verdict = capture_run(tmp_path, source, args)
    try:
        assert not verdict.valid, (result.stdout, result.stderr)
    finally:
        assert capture.close()


def valid_record():
    return {
        "schema": 1,
        "plugin": evidence.PLUGIN,
        "pytest_version": "9.1.0",
        "binding": {"nonce": "fixture"},
        "pid": 123,
        "sessions": 1,
        "collected": True,
        "rows": [["test.py::test_x", "passed", "failed", "passed"]],
        "errors": {"collection": 0, "setup": 0, "teardown": 0, "internal": 0},
        "collection_count": 1,
        "executed_count": 1,
        "failed_count": 1,
        "exitstatus": 1,
        "cmdline_return": 1,
        "normal_return": True,
        "unconfigured": True,
        "complete": True,
        "invalid": "",
    }


def test_escaped_surrogate_identity_refuses():
    record = valid_record()
    record["rows"][0][0] = "test_non_utf8_\udc80.py::test_unknown"
    decoded = evidence.decode_record(json.dumps(record).encode("utf-8"))
    verdict = evidence.validate_record(decoded, binding=record["binding"], child_pid=123, returncode=1)
    assert not verdict.valid


@pytest.mark.parametrize("version", ["8.4.2", "9.1.0", "9.1.1"])
def test_known_answer_record(version):
    record = valid_record()
    record["pytest_version"] = version
    result = evidence.validate_record(record, binding=record["binding"], child_pid=123, returncode=1)
    assert result.valid and result.failed_nodes == ["test.py::test_x"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("sessions", 2),
        ("sessions", True),
        ("pid", 124),
        ("pytest_version", "9.0.0"),
        ("complete", False),
        ("normal_return", False),
        ("unconfigured", False),
        ("collected", False),
        ("collection_count", -1),
        ("executed_count", 0),
        ("failed_count", 0),
        ("exitstatus", 2),
        ("cmdline_return", 0),
        ("invalid", "overflow"),
        ("rows", [["", "passed", "failed", "passed"]]),
        ("rows", [[None, "passed", "failed", "passed"]]),
        ("rows", [["test.py::test_x", "passed", None, "passed"]]),
        ("rows", [["test.py::test_x", "passed", "failed", None]]),
        ("errors", {"collection": 0, "setup": 1, "teardown": 0, "internal": 0}),
    ],
)
def test_invalid_records_refuse(field, value):
    record = valid_record()
    record[field] = value
    assert not evidence.validate_record(
        record, binding={"nonce": "fixture"}, child_pid=123, returncode=1
    ).valid


def test_duplicates_unknown_fields_and_binding_refuse():
    for edit in (
        lambda r: r.update(extra=1),
        lambda r: r["rows"].append(r["rows"][0]),
        lambda r: r.update(binding={"nonce": "stale"}),
    ):
        record = valid_record()
        edit(record)
        assert not evidence.validate_record(
            record, binding={"nonce": "fixture"}, child_pid=123, returncode=1
        ).valid


@pytest.mark.parametrize("size", [65535, 65536, 65537])
def test_nodeid_byte_bound(size):
    record = valid_record()
    record["rows"][0][0] = "x" * size
    result = evidence.validate_record(record, binding=record["binding"], child_pid=123, returncode=1)
    assert result.valid == (size <= 65536)


@pytest.mark.parametrize("size", [49999, 50000, 50001])
def test_inventory_bound(size):
    record = valid_record()
    record["rows"] = [[str(i), "passed", "failed", "passed"] for i in range(size)]
    record.update(collection_count=size, executed_count=size, failed_count=size)
    result = evidence.validate_record(record, binding=record["binding"], child_pid=123, returncode=1)
    assert result.valid == (size <= 50000)


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":Infinity}',
        '{"a":' + "1" * 100 + "}",
        "[" * 2000 + "]" * 2000,
        "{",
        '"x"',
    ],
)
def test_strict_json_refuses(raw):
    with pytest.raises(ValueError):
        evidence.decode_record(raw.encode())


def test_many_long_exact_identities(tmp_path):
    identity = "x" * 700 + " space"
    source = (
        "import pytest\n@pytest.mark.parametrize('n', range(65), ids=lambda n: "
        + repr(identity)
        + "+str(n))\ndef test_known(n):\n    assert False\n"
    )
    capture, result, verdict = capture_run(tmp_path, source, ["--tb=no"])
    try:
        assert result.returncode == 1
        assert verdict.valid, verdict.reason
        assert len(verdict.failed_nodes) == 65
        assert all(identity in node for node in verdict.failed_nodes)
    finally:
        assert capture.close()


def test_supported_prefix_only(tmp_path):
    for command in (["pytest"], ["python3", "-I", "-m", "pytest"], ["python", "script.py"]):
        assert (
            evidence.prepare_pytest_capture(
                command, test_cwd=tmp_path, test_env={}, reporter_path=Path(evidence.__file__)
            )
            is None
        )


@pytest.mark.parametrize(
    "plugin",
    [
        "import pytest\n@pytest.hookimpl(wrapper=True, tryfirst=True)\ndef pytest_cmdline_main(config):\n    yield\n    raise SystemExit(1)\n",
        "def pytest_configure(config):\n    original = config._ensure_unconfigure\n    calls = [0]\n    def cleanup(*args, **kwargs):\n        result = original(*args, **kwargs)\n        calls[0] += 1\n        if calls[0] == 2:\n            raise SystemExit(1)\n        return result\n    config._ensure_unconfigure = cleanup\n",
        "def pytest_configure(config):\n    original = config._ensure_unconfigure\n    def cleanup(*args, **kwargs):\n        return original(*args, **kwargs)\n    config._ensure_unconfigure = cleanup\n",
    ],
)
def test_late_lifecycle_exception_refuses(tmp_path, plugin):
    (tmp_path / "late_plugin.py").write_text(plugin)
    capture, result, verdict = capture_run(
        tmp_path, env={"PYTEST_PLUGINS": "late_plugin", "PYTHONPATH": str(tmp_path)}
    )
    try:
        assert result.returncode == 1
        assert not verdict.valid, (verdict.reason, result.stdout, result.stderr)
    finally:
        assert capture.close()


@pytest.mark.parametrize("size", [8 * 1024 * 1024 - 1, 8 * 1024 * 1024, 8 * 1024 * 1024 + 1])
def test_json_byte_bound(size):
    raw = b"{}" + b" " * (size - 2)
    if size <= 8 * 1024 * 1024:
        assert evidence.decode_record(raw) == {}
    else:
        with pytest.raises(ValueError):
            evidence.decode_record(raw)


def test_identity_unicode_and_whitespace_are_exact():
    record = valid_record()
    node = "custom path::test[ whitespace\n\u2603 ]"
    record["rows"][0][0] = node
    decoded = evidence.decode_record(json.dumps(record).encode())
    result = evidence.validate_record(decoded, binding=record["binding"], child_pid=123, returncode=1)
    assert result.valid and result.failed_nodes == [node]


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "hardlink", "oversize"])
def test_reader_substitutions_refuse(tmp_path, kind):
    command = ["python3", "-m", "pytest"]
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env={}, reporter_path=Path(evidence.__file__)
    )
    assert capture is not None
    record = valid_record()
    record["binding"] = capture.binding
    (capture.directory / "begin.json").write_text(json.dumps({"binding": capture.binding, "pid": 123}))
    path = capture.directory / "final.json"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(record))
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "hardlink":
        os.link(outside, path)
    else:
        path.write_bytes(b" " * (evidence.MAX_BYTES + 1))
    try:
        verdict = evidence.read_pytest_evidence(capture, child_pid=123, returncode=1)
        assert not verdict.valid
    finally:
        if kind == "directory":
            path.rmdir()
        else:
            path.unlink()
        assert capture.close()


def test_reader_complete_fixture_and_violation(tmp_path):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    assert capture is not None
    record = valid_record()
    record["binding"] = capture.binding
    (capture.directory / "begin.json").write_text(json.dumps({"binding": capture.binding, "pid": 123}))
    (capture.directory / "final.json").write_text(json.dumps(record))
    try:
        assert evidence.read_pytest_evidence(capture, child_pid=123, returncode=1).valid
        (capture.directory / "violation").write_text("duplicate")
        assert not evidence.read_pytest_evidence(capture, child_pid=123, returncode=1).valid
    finally:
        assert capture.close()


@pytest.fixture(scope="session")
def measured_runtimes(tmp_path_factory):
    candidates = {str(Path(sys.executable).resolve())}
    candidates.update(
        str(Path(found).resolve())
        for name in ("python3", "python3.9", "python3.12", "python3.14")
        if (found := shutil.which(name))
    )
    probe = (
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        "row={'executable':sys.executable,'physical_executable':str(Path(sys.executable).resolve()),"
        "'cache_tag':sys.implementation.cache_tag,'optimization':sys.flags.optimize,'cache_prefix':sys.pycache_prefix}\n"
        "try:\n import pytest,_pytest.config,_pytest.main,_pytest.assertion.rewrite\n"
        "except ModuleNotFoundError as error:\n"
        " if error.name!='pytest':raise\n row['pytest_unavailable']=True\n"
        "else:\n"
        " row.update(version=pytest.__version__,origin=str(Path(pytest.__file__).resolve()),"
        "source_sha256={name:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for name,module in "
        "[('config',_pytest.config),('main',_pytest.main),('rewrite',_pytest.assertion.rewrite)]})\n"
        "print(json.dumps(row))\n"
    )
    rows = []
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "LANG": "C.UTF-8",
    }
    for interpreter in sorted(candidates):
        result = subprocess.run(
            [interpreter, "-B", "-c", probe],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, (interpreter, result.stderr)
        rows.append(json.loads(result.stdout))
    (tmp_path_factory.getbasetemp() / "runtime-discovery.json").write_text(json.dumps(rows, indent=2))
    return rows


@pytest.fixture(
    params=sorted(evidence.QUALIFIED_RUNTIMES), ids=lambda pair: pair[0] + "-pytest" + pair[1]
)
def qualified_runtime(request, measured_runtimes):
    pair = request.param
    available = [row for row in measured_runtimes if (row["cache_tag"], row.get("version")) == pair]
    if not available:
        pytest.skip("qualified runtime pair unavailable: " + repr(pair))
    row = available[0]
    assert row["optimization"] == 0 and row["cache_prefix"] is None
    assert Path(row["origin"]).is_file()
    # These sources were read to qualify the private lifecycle and cache sites.
    sources = {
        ("cpython-39", "8.4.2"): (
            "9a0857d7dec27c5389986a98aecf61f5ab869e46e72dabb8e54695a4b9641eda",
            "1cfc8743fd192849d2309353de3eb8b42dcd83801e1d11b1169dbc75198333d7",
            "f231048ab925ef8585f309a1022470438ae2c77ffab1de0e98693e65547c316c",
        ),
        ("cpython-314", "9.1.0"): (
            "4e9aabb10b4229214f872f12d9cd88cea69c5d46d8f05d69a96745afd204b034",
            "8ee2725057ff1811e2ae9ebdf1befb7518195f8f333f9f8ca024d76a4b80780e",
            "17f1eefbd1c2fe5326cb7592ff3af92a773582cf9d01727b09d3ab864906ee50",
        ),
        ("cpython-312", "9.1.1"): (
            "98b05c2c37d09e1d5c05975efc2d9af98d4aff6ea47de410a8e93956420873c1",
            "8ee2725057ff1811e2ae9ebdf1befb7518195f8f333f9f8ca024d76a4b80780e",
            "17f1eefbd1c2fe5326cb7592ff3af92a773582cf9d01727b09d3ab864906ee50",
        ),
    }
    assert row["source_sha256"] == dict(zip(("config", "main", "rewrite"), sources[pair], strict=True))
    return row


@pytest.mark.parametrize(
    "case",
    [
        "positive",
        "positive_bytecode",
        "nested",
        "early_env",
        "early_ini",
        "late_wrapper",
        "late_cleanup",
        "no_session",
    ],
)
def test_real_runtime_qualification(tmp_path, qualified_runtime, case):
    interpreter = qualified_runtime["physical_executable"]
    version = qualified_runtime["version"]
    site = str(Path(qualified_runtime["origin"]).parents[1])
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "python3").symlink_to(interpreter)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join([str(tmp_path), site]),
    }
    source = "import pytest\ndef test_known():\n    print(pytest.__version__, pytest.__file__)\n    assert False\n"
    args = ["-s"]
    expected = case in ("positive", "positive_bytecode")
    if case == "nested":
        source = "import pytest,os\ndef test_known():\n    os.environ.pop('PYTEST_PLUGINS',None)\n    pytest.main(['-p','no:'+os.environ['FORGE_GATE_BOOTSTRAP'],'--collect-only','-q'])\n    assert False\n"
    elif case in ("early_env", "early_ini"):
        plugin = "import os,pytest\n_saved=os.environ.pop('PYTEST_ADDOPTS',None)\npytest.main(['--collect-only','-q','nested.py','-o','addopts='])\nif _saved is not None: os.environ['PYTEST_ADDOPTS']=_saved\n"
        (tmp_path / "earlier.py").write_text(plugin)
        (tmp_path / "nested.py").write_text("def test_nested():\n    pass\n")
        if case == "early_env":
            env["PYTEST_ADDOPTS"] = "-pearlier"
        else:
            (tmp_path / "pytest.ini").write_text("[pytest]\naddopts=-p earlier\n")
    elif case == "late_wrapper":
        (tmp_path / "later.py").write_text(
            "import pytest\n@pytest.hookimpl(wrapper=True,tryfirst=True)\ndef pytest_cmdline_main(config):\n    yield\n    raise SystemExit(1)\n"
        )
        env["PYTEST_PLUGINS"] = "later"
    elif case == "late_cleanup":
        (tmp_path / "later.py").write_text("def pytest_unconfigure(config):\n    raise SystemExit(1)\n")
        env["PYTEST_PLUGINS"] = "later"
    elif case == "no_session":
        args = ["--version"]
    capture, result, verdict = capture_run(
        tmp_path, source, args, env, bytecode=case == "positive_bytecode"
    )
    try:
        assert verdict.valid == expected, (verdict.reason, result.stdout, result.stderr)
        if expected:
            assert version in result.stdout and qualified_runtime["origin"] in result.stdout
            record = json.loads((capture.directory / "final.json").read_text())
            assert record["pytest_version"] == version
    finally:
        assert capture.close()


def test_unknown_cache_refuses_cleanup(tmp_path):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    cache = capture.directory / "__pycache__"
    cache.mkdir()
    unknown = cache / "foreign.pyc"
    unknown.write_bytes(b"foreign")
    assert not capture.close()
    assert "unknown bootstrap cache" in capture.cleanup_error
    assert unknown.read_bytes() == b"foreign"
    unknown.unlink()
    cache.rmdir()
    (capture.directory / (capture.module + ".py")).unlink()
    (capture.directory / "binding.json").unlink()
    capture.directory.rmdir()


@pytest.mark.parametrize(
    "tag,optimization,expected",
    [
        ("cpython-313", None, True),
        ("pypy310-pp73", None, True),
        ("cpython-314", 1, True),
        ("custom-runtime", 2, True),
        ("x" * 64, None, True),
        ("x" * 65, None, False),
    ],
)
def test_owned_compiler_cache_cleanup_is_separate_from_qualification(
    tmp_path, monkeypatch, tag, optimization, expected
):
    import importlib.util
    import py_compile

    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    bootstrap = capture.directory / (capture.module + ".py")
    with monkeypatch.context() as patch:
        patch.setattr(sys.implementation, "cache_tag", tag)
        cache = Path(importlib.util.cache_from_source(str(bootstrap), optimization=optimization))
    py_compile.compile(str(bootstrap), cfile=str(cache), doraise=True, optimize=optimization or 0)
    assert cache.is_file()
    try:
        assert capture.close() == expected, (capture.directory, capture.cleanup_error)
        assert capture.directory.exists() != expected
        if not expected:
            assert cache.is_file() and "unknown bootstrap cache" in capture.cleanup_error
    finally:
        if capture.directory.exists():
            cache.unlink()
            cache.parent.rmdir()
            bootstrap.unlink()
            (capture.directory / "binding.json").unlink()
            capture.directory.rmdir()


def test_cache_entry_limit_preserves_all_owned_bytes(tmp_path):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    directory = capture.directory / "__pycache__"
    directory.mkdir()
    names = [
        capture.module + ".custom.opt-X" + str(i) + ".pyc" for i in range(evidence.MAX_CACHE_ENTRIES + 1)
    ]
    for name in names:
        (directory / name).write_bytes(b"preserved")
    try:
        assert not capture.close()
        assert "unknown bootstrap cache" in capture.cleanup_error
        assert all((directory / name).read_bytes() == b"preserved" for name in names)
    finally:
        for name in names:
            (directory / name).unlink()
        directory.rmdir()
        (capture.directory / (capture.module + ".py")).unlink()
        (capture.directory / "binding.json").unlink()
        capture.directory.rmdir()


def test_reporter_source_reader_accepts_real_system_owned_bytes():
    path = Path(os.__file__).resolve()
    if path.stat().st_uid == os.getuid():
        pytest.skip("foreign-owned interpreter source unavailable")
    with pytest.raises(ValueError, match="owned regular file"):
        evidence._safe_read(path)
    data, identity = evidence._safe_read(path, reporter_source=True)
    assert data == path.read_bytes()
    assert identity[2] == path.stat().st_uid


def test_reporter_source_reader_skips_an_unavailable_foreign_owner(tmp_path, monkeypatch):
    owned = tmp_path / "os.py"
    owned.write_bytes(b"# owned interpreter source\n")
    monkeypatch.setattr(os, "__file__", str(owned))
    monkeypatch.setattr(
        evidence,
        "_safe_read",
        lambda *_args, **_kwargs: pytest.fail("unavailable ownership control read source"),
    )
    with pytest.raises(pytest.skip.Exception, match="foreign-owned interpreter source unavailable"):
        test_reporter_source_reader_accepts_real_system_owned_bytes()


@pytest.mark.parametrize("passing", [True, False])
@pytest.mark.parametrize("replacement", ["fifo", "changed"])
def test_public_reporter_replacement_keeps_workload_outcome(tmp_path, monkeypatch, passing, replacement):
    from io import StringIO

    from code_forge import gate_check

    work = tmp_path / "repo"
    work.mkdir()
    for argv in (
        ["git", "init", "-q"],
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "--allow-empty",
            "-qm",
            "base",
        ],
    ):
        subprocess.run(argv, cwd=work, capture_output=True, check=True, timeout=5)
    (work / "test_sample.py").write_text(
        "from pathlib import Path\ndef test_known():\n"
        "    Path('executed').write_text('once')\n    " + ("pass\n" if passing else "assert False\n")
    )
    directory = work / ".code-forge"
    directory.mkdir()
    (directory / "gate.yaml").write_text(
        json.dumps(
            {
                "test": {
                    "command": ["python3", "-B", "-m", "pytest", "-q", "test_sample.py"],
                    "timeout_seconds": 3,
                }
            }
        )
    )
    (directory / "test_baseline.json").write_text(
        json.dumps({"schema_version": 1, "test_results": {"test_sample.py::test_known": "failed"}})
    )
    subprocess.run(
        ["git", "add", "test_sample.py"], cwd=work, capture_output=True, check=True, timeout=5
    )
    reporter = tmp_path / "reporter.py"
    reporter.write_bytes(Path(evidence.__file__).read_bytes())
    prepared = []
    original_prepare = gate_check.prepare_pytest_capture

    def prepare(*args, **kwargs):
        kwargs["reporter_path"] = reporter
        capture = original_prepare(*args, **kwargs)
        assert capture is not None
        prepared.append(capture)
        if replacement == "fifo":
            reporter.unlink()
            os.mkfifo(reporter)
        else:
            reporter.write_text("from pathlib import Path\nPath('replacement-executed').touch()\n")
        return capture

    monkeypatch.setattr(gate_check, "prepare_pytest_capture", prepare)
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    error = StringIO()
    result = gate_check.run_gate_check(env=env, cwd=work, stdout=StringIO(), stderr=error)
    assert result == (gate_check.EXIT_PASS if passing else gate_check.EXIT_FAIL), error.getvalue()
    assert (work / "executed").is_file(), "reporter replacement prevented workload execution"
    assert (work / "executed").read_text() == "once"
    assert not (work / "replacement-executed").exists()
    if not passing:
        assert "insufficient structured pytest evidence" in error.getvalue()
        assert "all failures are known" not in error.getvalue()
    assert len(prepared) == 1 and prepared[0].fd is None
    assert not prepared[0].directory.exists()


def test_expanded_reporter_snapshot_declines_before_bootstrap_write(tmp_path, monkeypatch):
    reporter = tmp_path / "reporter.py"
    reporter.write_bytes(b"#" + b"\x7f" * (evidence.MAX_BYTES // 2))
    directories = []
    original_mkdtemp = evidence.tempfile.mkdtemp

    def make_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        directory = original_mkdtemp(*args, **kwargs)
        directories.append(Path(directory))
        return directory

    monkeypatch.setattr(evidence.tempfile, "mkdtemp", make_directory)
    capture = evidence.prepare_pytest_capture(
        ["python3", "-B", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=reporter,
    )
    assert capture is None
    assert len(directories) == 1 and not directories[0].exists()
    for descriptor in Path("/proc/self/fd").iterdir():
        with contextlib.suppress(FileNotFoundError):
            assert str(directories[0]) not in os.readlink(descriptor)


@pytest.mark.parametrize("passing", [True, False])
@pytest.mark.parametrize("bytecode", [True, False])
@pytest.mark.parametrize("replacement", ["fifo", "changed"])
def test_qualified_reporter_snapshot_keeps_original_results(
    tmp_path, qualified_runtime, passing, bytecode, replacement
):
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "python3").symlink_to(qualified_runtime["physical_executable"])
    env = {
        "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(qualified_runtime["origin"]).parents[1]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    (tmp_path / "test_sample.py").write_text(
        "import pytest\nfrom pathlib import Path\ndef test_known():\n"
        "    print(pytest.__version__, pytest.__file__)\n"
        "    Path('executed').write_text('once')\n    " + ("pass\n" if passing else "assert False\n")
    )
    reporter = tmp_path / "reporter.py"
    reporter.write_bytes(Path(evidence.__file__).read_bytes())
    command = [
        "python3",
        *([] if bytecode else ["-B"]),
        "-m",
        "pytest",
        "-q",
        "-s",
        "test_sample.py",
    ]
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env=env, reporter_path=reporter
    )
    assert capture is not None
    if replacement == "fifo":
        reporter.unlink()
        os.mkfifo(reporter)
    else:
        reporter.write_text("from pathlib import Path\nPath('replacement-executed').touch()\n")
    try:
        result = evidence.run_captured_pytest(
            command, capture=capture, test_cwd=tmp_path, test_env=env, timeout_seconds=3
        )
        assert result.returncode == (0 if passing else 1), result.stderr
        assert (tmp_path / "executed").is_file(), "reporter replacement prevented workload execution"
        assert (tmp_path / "executed").read_text() == "once"
        assert not (tmp_path / "replacement-executed").exists()
        assert qualified_runtime["version"] in result.stdout
        assert qualified_runtime["origin"] in result.stdout
        assert result.reaped and result.pipes_closed
        verdict = evidence.read_pytest_evidence(
            capture, child_pid=result.pid, returncode=result.returncode
        )
        assert not verdict.valid
    finally:
        assert capture.close()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "oversize"])
def test_reporter_source_policy_keeps_regular_bounded_nofollow_reader(tmp_path, kind):
    path = tmp_path / "source.py"
    if kind == "symlink":
        target = tmp_path / "target.py"
        target.write_bytes(b"preserved")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b" " * (evidence.MAX_BYTES + 1))
    with pytest.raises((OSError, ValueError)):
        evidence._safe_read(path, reporter_source=True)


def test_real_receipt_hardlink_still_refuses_after_source_admission(tmp_path):
    capture, result, verdict = capture_run(tmp_path)
    assert verdict.valid, verdict.reason
    receipt = capture.directory / "final.json"
    second = tmp_path / "receipt-copy.json"
    os.link(receipt, second)
    try:
        verdict = evidence.read_pytest_evidence(
            capture, child_pid=result.pid, returncode=result.returncode
        )
        assert not verdict.valid
        assert "owned regular file" in verdict.reason
    finally:
        second.unlink()
        assert capture.close()


def test_real_timeout_reaps_child_and_closes_pipes(tmp_path, monkeypatch):
    (tmp_path / "test_sleep.py").write_text("import time\ndef test_sleep():\n    time.sleep(10)\n")
    command = ["python3", "-B", "-m", "pytest", "-q", "test_sleep.py"]
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env=env, reporter_path=Path(evidence.__file__)
    )
    original = subprocess.Popen
    children = []

    def observe(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(evidence.subprocess, "Popen", observe)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            evidence.run_captured_pytest(
                command, capture=capture, test_cwd=tmp_path, test_env=env, timeout_seconds=1
            )
        assert len(children) == 1
        assert children[0].poll() is not None
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        assert capture.close()


@pytest.mark.parametrize("kind", ["setup", "teardown", "collection"])
def test_real_error_counters(tmp_path, kind):
    source = "def test_known():\n    assert False\n"
    args = []
    if kind == "setup":
        source += "import pytest\n@pytest.fixture\ndef broken():\n    assert False\ndef test_error(broken):\n    pass\n"
    elif kind == "teardown":
        source = "import pytest\n@pytest.fixture\ndef broken():\n    yield\n    assert False\ndef test_known(broken):\n    assert False\n"
    else:
        (tmp_path / "test_bad.py").write_text("raise RuntimeError('collection')\n")
        args = ["--continue-on-collection-errors", "test_bad.py"]
    capture, result, verdict = capture_run(tmp_path, source, args)
    try:
        assert result.returncode == 1
        assert not verdict.valid
        final = json.loads((capture.directory / "final.json").read_text())
        assert final["errors"][kind] == 1
    finally:
        assert capture.close()


def test_duplicate_publication_is_sticky(tmp_path):
    recorder = object.__new__(evidence.Recorder)
    recorder.directory = tmp_path
    recorder.invalid = ""
    recorder.publish("begin.json", {"first": 1})
    recorder.publish("begin.json", {"second": 2})
    assert json.loads((tmp_path / "begin.json").read_text()) == {"first": 1}
    assert recorder.invalid == "duplicate evidence publication"
    assert (tmp_path / "violation").is_file()


def test_duplicate_phase_reports_refuse(tmp_path):
    plugin = "_once=False\ndef pytest_runtest_logreport(report):\n    global _once\n    if report.when=='call' and not _once:\n        _once=True\n        _config.hook.pytest_runtest_logreport(report=report)\ndef pytest_configure(config):\n    global _config\n    _config=config\n"
    (tmp_path / "duplicate.py").write_text(plugin)
    capture, result, verdict = capture_run(
        tmp_path, env={"PYTEST_PLUGINS": "duplicate", "PYTHONPATH": str(tmp_path)}
    )
    try:
        assert result.returncode == 1
        assert not verdict.valid
    finally:
        assert capture.close()


def test_skip_and_xfail_lifecycles_close(tmp_path):
    source = "import pytest\ndef test_known():\n    assert False\n@pytest.mark.skip\ndef test_skip():\n    pass\n@pytest.mark.xfail\ndef test_xfail():\n    assert False\n@pytest.mark.xfail(strict=True)\ndef test_xpass():\n    pass\n"
    capture, result, verdict = capture_run(tmp_path, source)
    try:
        assert result.returncode == 1
        assert verdict.valid, verdict.reason
        assert verdict.failed_nodes == ["test_sample.py::test_known", "test_sample.py::test_xpass"]
    finally:
        assert capture.close()


@pytest.mark.parametrize("nested", [False, True])
def test_real_entrypoint_autoload(tmp_path, nested):
    """Private fixture metadata exercises pytest11 discovery without installation."""
    metadata = tmp_path / "forge_gate_control-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: forge_gate_control\nVersion: 1.0\n")
    (metadata / "entry_points.txt").write_text("[pytest11]\nforge_gate_control = autoload_control\n")
    plugin = "from pathlib import Path\nPath('autoload.marker').write_text('loaded')\n"
    if nested:
        (tmp_path / "nested.py").write_text("def test_nested():\n    pass\n")
        plugin += "import os,pytest\nos.environ.pop('PYTEST_PLUGINS',None)\npytest.main(['-p','no:forge_gate_control','--collect-only','-q','nested.py'])\n"
    (tmp_path / "autoload_control.py").write_text(plugin)
    capture, result, verdict = capture_run(tmp_path, env={"PYTHONPATH": str(tmp_path)})
    try:
        assert (tmp_path / "autoload.marker").is_file(), result.stderr
        assert (tmp_path / "autoload.marker").read_text() == "loaded"
        assert result.returncode == 1
        assert verdict.valid == (not nested), verdict.reason
    finally:
        assert capture.close()


def test_reporter_change_midrun_refuses(tmp_path):
    reporter = tmp_path / "_gate_pytest.py"
    reporter.write_bytes(Path(evidence.__file__).read_bytes())
    command = ["python3", "-B", "-m", "pytest", "-q", "test_sample.py"]
    source = (
        "from pathlib import Path\ndef test_known():\n    path=Path("
        + repr(str(reporter))
        + ")\n    path.write_text(path.read_text()+'\\n')\n    assert False\n"
    )
    (tmp_path / "test_sample.py").write_text(source)
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env=env, reporter_path=reporter
    )
    try:
        result = evidence.run_captured_pytest(
            command, capture=capture, test_cwd=tmp_path, test_env=env, timeout_seconds=30
        )
        verdict = evidence.read_pytest_evidence(
            capture, child_pid=result.pid, returncode=result.returncode
        )
        assert not verdict.valid and "reporter changed" in verdict.reason
    finally:
        assert capture.close()


def test_prepare_failure_closes_owned_directory_fd(tmp_path, monkeypatch):
    original_open = os.open
    original_digest = evidence._digest
    directories = []

    def observe(path, flags, **kwargs):
        fd = original_open(path, flags, **kwargs)
        if flags & os.O_DIRECTORY:
            directories.append(fd)
        return fd

    def fail_bootstrap(path, **kwargs):
        if Path(path).name != "_gate_pytest.py":
            raise ValueError("bootstrap hash unavailable")
        return original_digest(path, **kwargs)

    monkeypatch.setattr(evidence.os, "open", observe)
    monkeypatch.setattr(evidence, "_digest", fail_bootstrap)
    result = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    assert result is None
    assert directories
    for fd in directories:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("case", ["expired", "unsupported", "midread", "changed", "short"])
def test_safe_reader_failure_modes(tmp_path, monkeypatch, case):
    path = tmp_path / "record"
    path.write_bytes(b"record")
    deadline = None
    if case == "expired":
        deadline = time.monotonic() - 1
    elif case == "unsupported":
        monkeypatch.delattr(evidence.os, "O_NOFOLLOW")
    elif case == "midread":
        clock = iter([0, 2])
        monkeypatch.setattr(evidence.time, "monotonic", lambda: next(clock))
        deadline = 1
    elif case == "changed":
        original = os.read

        def changed(fd, size):
            data = original(fd, size)
            path.write_bytes(b"changed")
            return data

        monkeypatch.setattr(evidence.os, "read", changed)
    else:
        chunks = iter([b"rec", b""])
        monkeypatch.setattr(evidence.os, "read", lambda *args: next(chunks))
    with pytest.raises(ValueError):
        evidence._safe_read(path, deadline=deadline)


@pytest.mark.parametrize("case", ["directory", "unknown", "cache_owner", "late_directory"])
def test_cleanup_preserves_unknown_or_changed_entries(tmp_path, monkeypatch, case):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    directory = capture.directory
    moved = tmp_path / "moved"
    extra = directory / "unknown"
    if case == "directory":
        directory.rename(moved)
        directory.mkdir()
    elif case == "unknown":
        extra.write_bytes(b"preserved")
    elif case == "cache_owner":
        (directory / "__pycache__").mkdir()
        original = os.fstat

        def foreign(fd):
            info = original(fd)
            if Path("/proc/self/fd/" + str(fd)).resolve().name == "__pycache__":
                return SimpleNamespace(st_uid=os.getuid() + 1)
            return info

        monkeypatch.setattr(evidence.os, "fstat", foreign)
    else:
        original = os.unlink

        def replaced(path, **kwargs):
            original(path, **kwargs)
            directory.rename(moved)
            directory.mkdir()

        monkeypatch.setattr(evidence.os, "unlink", replaced)
    assert not capture.close()
    assert not capture.close()
    monkeypatch.undo()
    if case in ("directory", "late_directory"):
        directory.rmdir()
        if case == "directory":
            (moved / (capture.module + ".py")).unlink()
            (moved / "binding.json").unlink()
        moved.rmdir()
    else:
        if case == "unknown":
            assert extra.read_bytes() == b"preserved"
            extra.unlink()
        else:
            (directory / "__pycache__").rmdir()
        (directory / (capture.module + ".py")).unlink()
        (directory / "binding.json").unlink()
        directory.rmdir()


def test_preparation_missing_source_and_open_failure(tmp_path, monkeypatch):
    kwargs = {"test_cwd": tmp_path, "test_env": {}}
    assert (
        evidence.prepare_pytest_capture(
            ["python3", "-m", "pytest"], reporter_path=tmp_path / "missing.py", **kwargs
        )
        is None
    )
    original = os.open

    def fail_directory(path, flags, **kwargs):
        if flags & os.O_DIRECTORY:
            raise OSError("directory unavailable")
        return original(path, flags, **kwargs)

    monkeypatch.setattr(evidence.os, "open", fail_directory)
    assert (
        evidence.prepare_pytest_capture(
            ["python3", "-m", "pytest"], reporter_path=Path(evidence.__file__), **kwargs
        )
        is None
    )


def test_capture_constructor_failure_closes_owned_directory(tmp_path, monkeypatch):
    opened = []

    def unavailable(directory, fd, *args):
        opened.append((directory, fd))
        os.fstat(fd)
        raise ValueError("capture allocation unavailable")

    monkeypatch.setattr(evidence, "Capture", unavailable)
    assert (
        evidence.prepare_pytest_capture(
            ["python3", "-m", "pytest"],
            test_cwd=tmp_path,
            test_env={},
            reporter_path=Path(evidence.__file__),
        )
        is None
    )
    assert len(opened) == 1
    directory, fd = opened[0]
    assert not directory.exists()
    with pytest.raises(OSError):
        os.fstat(fd)


def test_capture_rejects_changed_invocation(tmp_path):
    command = ["python3", "-m", "pytest"]
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env={}, reporter_path=Path(evidence.__file__)
    )
    try:
        with pytest.raises(ValueError, match="invocation changed"):
            evidence.run_captured_pytest(
                command + ["-q"], capture=capture, test_cwd=tmp_path, test_env={}, timeout_seconds=1
            )
    finally:
        assert capture.close() and capture.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("errors", {}),
        ("rows", [[]]),
        ("rows", [["x", "skipped", "passed", "passed"]]),
        ("rows", [["x", "passed", "passed", "passed"]]),
    ],
)
def test_additional_invalid_record_shapes(field, value):
    record = valid_record()
    record[field] = value
    if field == "rows" and value == [["x", "passed", "passed", "passed"]]:
        record["failed_count"] = 0
    assert not evidence.validate_record(
        record, binding=record["binding"], child_pid=123, returncode=1
    ).valid


def test_invalid_utf8_refuses():
    with pytest.raises(ValueError, match="malformed"):
        evidence.decode_record(b"\xff")


def test_reader_binding_and_late_deadline(tmp_path, monkeypatch):
    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(evidence.__file__),
    )
    record = valid_record()
    record["binding"] = capture.binding
    begin = capture.directory / "begin.json"
    final = capture.directory / "final.json"
    begin.write_text(json.dumps({"binding": capture.binding, "pid": 124}))
    final.write_text(json.dumps(record))
    try:
        assert not evidence.read_pytest_evidence(capture, child_pid=True, returncode=1).valid
        assert not evidence.read_pytest_evidence(capture, child_pid=123, returncode=1).valid
        begin.write_text(json.dumps({"binding": capture.binding, "pid": 123}))
        clock = [0]
        calls = [0]
        original = evidence.decode_record

        def decode(data):
            result = original(data)
            calls[0] += 1
            if calls[0] == 2:
                clock[0] = 3
            return result

        monkeypatch.setattr(evidence.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(evidence, "decode_record", decode)
        result = evidence.read_pytest_evidence(capture, child_pid=123, returncode=1)
        assert not result.valid and "deadline" in result.reason
    finally:
        assert capture.close()


def _hook_fixture(tmp_path, monkeypatch, *, install_dispatch=True):
    command = ["python3", "-m", "pytest"]
    capture = evidence.prepare_pytest_capture(
        command, test_cwd=tmp_path, test_env={}, reporter_path=Path(evidence.__file__)
    )
    monkeypatch.chdir(tmp_path)
    binding = dict(capture.binding, parent_pid=os.getppid())
    monkeypatch.setattr(sys, "argv", ["pytest", *capture.command[capture.prefix :]])
    import _pytest.config as config

    monkeypatch.setattr(config, "_prepareconfig", config._prepareconfig)
    hooks = evidence.load_plugin(binding, binding["bootstrap_path"])
    if install_dispatch:
        hooks.guard_dispatch(config.PytestPluginManager())
    recorder = next(
        cell.cell_contents
        for cell in hooks.pytest_collection_finish.__func__.__closure__
        if isinstance(cell.cell_contents, evidence.Recorder)
    )
    assert recorder.invalid == ""
    return capture, hooks, recorder, binding


@pytest.mark.parametrize("shape", ["receiver", "unbound", "duplicate", "subclass", "missing", "caller"])
def test_dispatch_admission_preserves_substituted_or_installed_callable(tmp_path, monkeypatch, shape):
    import _pytest.config as config
    from types import MethodType

    capture, hooks, recorder, _ = _hook_fixture(tmp_path, monkeypatch, install_dispatch=False)
    manager = config.PytestPluginManager()
    caller = manager.hook.pytest_internalerror
    if shape == "receiver":
        caller._hookexec = MethodType(config.PytestPluginManager._hookexec, config.PytestPluginManager())
    elif shape == "unbound":
        caller._hookexec = lambda *args: []
    elif shape == "subclass":

        class ChangedManager(config.PytestPluginManager):
            pass

        manager = ChangedManager()
        caller = manager.hook.pytest_internalerror
    elif shape == "missing":
        del caller._hookexec
    elif shape == "caller":
        manager.hook.pytest_internalerror = lambda **kwargs: []
        caller = manager.hook.pytest_internalerror
    else:
        hooks.guard_dispatch(manager)
    incoming = getattr(caller, "_hookexec", None)
    try:
        hooks.guard_dispatch(manager)
        assert recorder.invalid
        assert getattr(caller, "_hookexec", None) is incoming
        assert (capture.directory / "violation").is_file()
    finally:
        assert capture.close()


def test_nonempty_cache_prefix_declines_before_allocating(tmp_path, monkeypatch):
    allocator = Mock(side_effect=AssertionError("unsupported profile must not allocate capture"))
    monkeypatch.setattr(evidence.tempfile, "mkdtemp", allocator)
    capture = evidence.prepare_pytest_capture(
        ["python3", "-m", "pytest"],
        test_cwd=tmp_path,
        test_env={"PYTHONPYCACHEPREFIX": "caller-prefix"},
        reporter_path=Path(evidence.__file__),
    )
    assert capture is None
    allocator.assert_not_called()


@pytest.mark.parametrize("entry", ["call_historic", "_maybe_apply_history"])
@pytest.mark.parametrize("stage", ["admission", "publication"])
def test_historic_entry_substitution_refuses_without_replacing_it(tmp_path, monkeypatch, entry, stage):
    import _pytest.config as config
    from pluggy import HookCaller

    capture, hooks, recorder, _ = _hook_fixture(tmp_path, monkeypatch, install_dispatch=False)
    manager = config.PytestPluginManager()
    original = getattr(HookCaller, entry)

    def substituted(*args, **kwargs):
        return original(*args, **kwargs)

    try:
        if stage == "publication":
            hooks.guard_dispatch(manager)
            current = object.__new__(config.Config)
            current._configured = False
            current._cleanup_stack = contextlib.ExitStack()
            current.hook = hooks.relay
            generator = hooks.pytest_cmdline_main(current)
            next(generator)
            current._ensure_unconfigure()
            with pytest.raises(StopIteration):
                generator.send(1)
            monkeypatch.setattr(HookCaller, entry, substituted)
            current._ensure_unconfigure()
        else:
            monkeypatch.setattr(HookCaller, entry, substituted)
            hooks.guard_dispatch(manager)
        assert recorder.invalid == (
            "unsupported hook dispatch boundary"
            if stage == "admission"
            else "replaced hook dispatch boundary"
        )
        assert getattr(HookCaller, entry) is substituted
        assert (capture.directory / "violation").is_file()
        assert not (capture.directory / "final.json").exists()
        observed = []
        current = config.Config(manager)
        current._parser.addini("markers", "registered markers", "linelist")

        class Late:
            def pytest_configure(self, config):
                observed.append(config)

        assert manager.hook.pytest_configure.call_historic(kwargs={"config": current}) is None
        manager.register(Late(), "late")
        assert observed == [current]
    finally:
        assert capture.close()


@pytest.mark.parametrize("aborted", [False, True])
def test_historic_configure_replay_preserves_results_and_aborts(tmp_path, monkeypatch, aborted):
    import _pytest.config as config
    from _pytest.main import Failed

    capture, hooks, recorder, _ = _hook_fixture(tmp_path, monkeypatch, install_dispatch=False)
    manager = config.PytestPluginManager()
    hooks.guard_dispatch(manager)
    observed = []
    current = config.Config(manager)
    current._parser.addini("markers", "registered markers", "linelist")

    class Initial:
        def pytest_configure(self, config):
            observed.append(("initial", config))

    class Late:
        def pytest_configure(self, config):
            observed.append(("late", config))
            if aborted:
                raise Failed("late configure abort")

    try:
        manager.register(Initial(), "initial")
        assert manager.hook.pytest_configure.call_historic(kwargs={"config": current}) is None
        assert observed == [("initial", current)] and not recorder.invalid
        if aborted:
            with pytest.raises(Failed, match="late configure abort"):
                manager.register(Late(), "late")
            assert recorder.invalid == "aborted pytest_configure dispatch"
            assert (capture.directory / "violation").is_file()
        else:
            manager.register(Late(), "late")
            assert not recorder.invalid
        assert observed == [("initial", current), ("late", current)]
    finally:
        assert capture.close()


@pytest.mark.parametrize("shape", ["ordinary", "wrapper_before", "wrapper_after"])
def test_internal_dispatch_records_error_before_plugin_reporting(tmp_path, monkeypatch, shape):
    import _pytest.config as config

    capture, hooks, recorder, _ = _hook_fixture(tmp_path, monkeypatch, install_dispatch=False)
    manager = config.PytestPluginManager()
    hooks.guard_dispatch(manager)
    observed = []

    class Handler:
        def pytest_internalerror(self, excrepr, excinfo):
            observed.append(("ordinary", recorder.errors["internal"]))
            return True

    class Wrapper:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_internalerror(self, excrepr, excinfo):
            observed.append(("wrapper", recorder.errors["internal"]))
            if shape == "wrapper_before":
                pytest.exit("before reporting", returncode=1)
            yield
            pytest.exit("after reporting", returncode=1)

    manager.register(Handler(), "handler")
    if shape != "ordinary":
        manager.register(Wrapper(), "wrapper")
    try:
        if shape == "ordinary":
            assert manager.hook.pytest_internalerror(excrepr=None, excinfo=None) == [True]
            assert not recorder.invalid
        else:
            with pytest.raises(pytest.exit.Exception) as aborted:
                manager.hook.pytest_internalerror(excrepr=None, excinfo=None)
            assert aborted.value.returncode == 1
            assert recorder.invalid == "aborted pytest_internalerror dispatch"
        assert observed
        assert all(count == 1 for _, count in observed)
    finally:
        assert capture.close()


@pytest.mark.parametrize("shape", ["ordinary", "wrapper_before", "wrapper_after"])
def test_keyboard_dispatch_refuses_before_plugin_reporting(tmp_path, monkeypatch, shape):
    import _pytest.config as config
    from _pytest._code import ExceptionInfo

    capture, hooks, recorder, _ = _hook_fixture(tmp_path, monkeypatch, install_dispatch=False)
    manager = config.PytestPluginManager()
    hooks.guard_dispatch(manager)
    observed = []
    try:
        raise KeyboardInterrupt("actual interruption")
    except KeyboardInterrupt:
        excinfo = ExceptionInfo.from_current()

    class Handler:
        def pytest_keyboard_interrupt(self, excinfo):
            assert isinstance(excinfo.value, KeyboardInterrupt)
            observed.append(bool(recorder.invalid))
            return "handled"

    class Wrapper:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_keyboard_interrupt(self, excinfo):
            observed.append(bool(recorder.invalid))
            if shape == "wrapper_before":
                pytest.exit("before interrupt reporting", returncode=1)
            yield
            pytest.exit("after interrupt reporting", returncode=1)

    manager.register(Handler(), "handler")
    if shape != "ordinary":
        manager.register(Wrapper(), "wrapper")
    try:
        if shape == "ordinary":
            assert manager.hook.pytest_keyboard_interrupt(excinfo=excinfo) == ["handled"]
        else:
            with pytest.raises(pytest.exit.Exception) as aborted:
                manager.hook.pytest_keyboard_interrupt(excinfo=excinfo)
            assert aborted.value.returncode == 1
        assert observed and all(observed)
        assert recorder.invalid == "interrupted pytest runner"
        assert (capture.directory / "violation").is_file()
    finally:
        assert capture.close()


@pytest.mark.parametrize(
    "shape",
    [
        "ordinary",
        "exit",
        "failed",
        "maxfail_reached",
        "maxfail_below",
        "early_failed",
        "early_exit",
        "collection_failed",
        "sessionstart_failed",
        "configure_failed",
        "passing",
    ],
)
def test_qualified_runner_abort_paths(tmp_path, qualified_runtime, shape):
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "python3").symlink_to(qualified_runtime["physical_executable"])
    env = {
        "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(qualified_runtime["origin"]).parents[1]),
        "PYTHONDONTWRITEBYTECODE": "",
    }
    plugin = ""
    if shape in ("exit", "failed", "early_failed", "early_exit"):
        plugin = "import pytest\nfrom _pytest.main import Failed\n"
        if shape.startswith("early_"):
            plugin += "def pytest_sessionstart(session):\n"
        else:
            plugin += "def pytest_runtest_logfinish(nodeid, location):\n"
        plugin += (
            "    pytest.exit('runner abort', returncode=1)\n"
            if shape in ("exit", "early_exit")
            else "    raise Failed('runner abort')\n"
        )
    elif shape in ("collection_failed", "sessionstart_failed", "configure_failed"):
        plugin = "import pytest\nfrom _pytest.main import Failed\n"
        if shape == "configure_failed":
            plugin += (
                "@pytest.hookimpl(trylast=True)\ndef pytest_configure(config):\n"
                "    session = config.pluginmanager.getplugin('session')\n"
                "    config.hook.pytest_sessionstart(session=session)\n"
            )
        else:
            name = "pytest_collection" if shape == "collection_failed" else "pytest_sessionstart"
            plugin += f"@pytest.hookimpl(wrapper=True, trylast=True)\ndef {name}(session):\n    yield\n"
        if shape != "collection_failed":
            plugin += "    session.config.hook.pytest_collection(session=session)\n"
        plugin += "    session.config.hook.pytest_runtestloop(session=session)\n"
        if shape != "collection_failed":
            plugin += "    session.config.hook.pytest_sessionfinish(session=session, exitstatus=1)\n"
        plugin += "    raise Failed('outer dispatch aborted')\n"
    (tmp_path / "conftest.py").write_text(plugin)
    args = ["--maxfail=1"] if shape == "maxfail_reached" else []
    if shape == "maxfail_below":
        args = ["--maxfail=2"]
    source = "import pytest\ndef test_known():\n    print(pytest.__version__, pytest.__file__)\n    assert False\n"
    if shape == "passing":
        source = source.replace("assert False", "assert True")
        args += ["-s"]
    capture, result, verdict = capture_run(tmp_path, source, args, env)
    try:
        assert result.returncode == (0 if shape == "passing" else 1), result.stderr
        assert result.reaped and result.pipes_closed
        assert verdict.valid == (shape in ("ordinary", "maxfail_below")), (
            verdict.reason,
            result.stdout,
            result.stderr,
        )
        if verdict.valid or shape == "passing":
            assert qualified_runtime["version"] in result.stdout
            assert qualified_runtime["origin"] in result.stdout
    finally:
        assert capture.close()


@pytest.mark.parametrize(
    "case", ["origin", "parent", "argv", "runtime", "publication", "session", "events"]
)
def test_protocol_failure_events(tmp_path, monkeypatch, case):
    capture, hooks, recorder, binding = _hook_fixture(tmp_path, monkeypatch)
    try:
        if case in ("origin", "parent", "argv"):
            if case == "origin":
                binding["bootstrap_hash"] = "wrong"
            elif case == "parent":
                binding["parent_pid"] += 1
            else:
                monkeypatch.setattr(sys, "argv", ["pytest", "wrong"])
            failed = evidence.Recorder(binding, binding["bootstrap_path"])
            assert failed.invalid
        elif case == "runtime":
            monkeypatch.setattr(evidence, "QUALIFIED_RUNTIMES", frozenset())
            unsupported = evidence.load_plugin(binding, binding["bootstrap_path"])
            assert unsupported is None
            assert (capture.directory / "violation").is_file()
        elif case == "publication":
            recorder.publish("final.json", {"data": "x" * (8 * 1024 * 1024)})
            recorder.refuse("later")
            assert recorder.invalid == "evidence byte overflow"
            assert not (capture.directory / "final.json").exists()
        elif case == "session":
            import _pytest.config as pytest_config

            config = object.__new__(pytest_config.Config)
            config._configured = False
            config._cleanup_stack = contextlib.ExitStack()
            config.hook = hooks.relay
            generator = hooks.pytest_cmdline_main(config)
            next(generator)
            config._ensure_unconfigure()
            with pytest.raises(StopIteration):
                generator.send(1)
            config._ensure_unconfigure()
            config._ensure_unconfigure()
            duplicate = hooks.pytest_cmdline_main(config)
            next(duplicate)
            duplicate.close()
            assert recorder.invalid == "unexpected cleanup count"
        else:
            args = ["-p", binding["module"], "--forked", "-p"]
            hooks.pytest_load_initial_conftests(None, None, args)
            hooks.pytest_collection_finish(SimpleNamespace(items=[]))
            hooks.pytest_collection_finish(SimpleNamespace(items=[]))
            hooks.pytest_runtest_logreport(SimpleNamespace(nodeid="x", when="custom", outcome="passed"))
            hooks.relay.pytest_internalerror(excrepr=None, excinfo=None)
            assert recorder.invalid == "distributed or repeated runner"
            assert recorder.errors["internal"] == 1
    finally:
        assert capture.close()


@pytest.mark.parametrize("case", ["invalid", "oversized", "count", "identity_bytes", "duplicate"])
def test_recorder_inventory_bounds(tmp_path, monkeypatch, case):
    capture, hooks, recorder, _ = _hook_fixture(tmp_path, monkeypatch)
    nodes = ["x"]
    if case == "invalid":
        nodes = [""]
    elif case == "oversized":
        nodes = ["x" * 65537]
    elif case == "count":
        nodes = [str(i) for i in range(50001)]
    elif case == "identity_bytes":
        nodes = [("x" * 65520) + str(i) for i in range(129)]
    else:
        nodes = ["x", "x"]
    try:
        hooks.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid=n) for n in nodes]))
        assert recorder.invalid
        assert len(recorder.rows) <= 50000
    finally:
        assert capture.close()

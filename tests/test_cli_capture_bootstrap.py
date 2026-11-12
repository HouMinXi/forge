"""Synthetic isolated workers only; never unshare, trace or run product code."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOT = ROOT / "qualification/installed_cli/capture_bootstrap.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load("bootstrap_controller", ROOT / "scripts/capture_installed_cli.py")
bootstrap = load("bootstrap_tested", BOOT)


@pytest.fixture(autouse=True)
def preserve_test_main(monkeypatch):
    # Production keeps worker main through interpreter shutdown. Only tests restore.
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])


def execute(tmp_path, source, *, expected=None, evidence=True):
    tmp_path.chmod(0o700)
    worker = tmp_path / "worker.py"
    worker.write_text(source)
    startup = tmp_path / "startup"
    if evidence:
        startup.mkdir(mode=0o700)
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        BOOT.read_text(),
        str(worker),
        expected or hashlib.sha256(worker.read_bytes()).hexdigest(),
        str(startup),
        "--binding",
        str(tmp_path / "binding"),
        "--mode",
        "baseline",
        "--output",
        str(tmp_path / "output"),
    ]
    result = subprocess.run(
        command,
        env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, startup, worker


@pytest.mark.parametrize("code", [0, 7, 73])
def test_original_system_exit_and_direct_script_globals(tmp_path, code):
    baseline = subprocess.run(
        [sys.executable, "-I", "-B", "-c", "import json,sys;print(json.dumps(sys.path))"],
        env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
        capture_output=True,
        check=True,
    )
    path = tmp_path / "worker.py"
    source = f"""import sys
assert __name__ == "__main__"
import __main__
assert __main__.__dict__ is globals()
assert __file__ == {str(path)!r}
assert __package__ is None and __spec__ is None
assert sys.argv == {[str(path), "--binding", str(tmp_path / "binding"), "--mode", "baseline", "--output", str(tmp_path / "output")]!r}
assert sys.path == {json.loads(baseline.stdout)!r}
raise SystemExit({code})
"""
    result, startup, _ = execute(tmp_path, source)
    assert result.returncode == code
    assert entry.read_startup(startup) == dict(
        startup_evidence="OBSERVED",
        startup_stage="WORKER_EXECUTION_ENTERED",
        startup_error_category="NONE",
    )


@pytest.mark.parametrize(
    "source,stage,category",
    [
        ("import missing_forge_synthetic_module", "WORKER_EXECUTION_ENTERED", "IMPORT_ERROR"),
        ("def broken(:", "WORKER_SOURCE_VERIFIED", "SYNTAX_ERROR"),
        (
            "raise PermissionError(13, 'PRIVATE-PATH-CREDENTIAL')",
            "WORKER_EXECUTION_ENTERED",
            "PERMISSION_DENIED",
        ),
    ],
    ids=["import", "syntax", "permission"],
)
def test_early_failures_keep_original_exception_exit(tmp_path, source, stage, category):
    result, startup, _ = execute(tmp_path, source)
    assert result.returncode == 1
    observation = entry.read_startup(startup)
    assert observation["startup_stage"] == stage
    assert observation["startup_error_category"] == category
    assert "PRIVATE" not in json.dumps(observation)
    assert all(b"PRIVATE" not in file.read_bytes() for file in startup.iterdir())


def test_abrupt_signal_leaves_only_progress_not_success(tmp_path):
    result, startup, _ = execute(tmp_path, "import os,signal; os.kill(os.getpid(), signal.SIGTERM)")
    assert result.returncode == -signal.SIGTERM
    assert entry.read_startup(startup)["startup_error_category"] == "NONE"
    assert not (startup / "error.json").exists()


def test_no_metadata_directory_cannot_replace_status(tmp_path):
    result, startup, _ = execute(tmp_path, "raise SystemExit(7)", evidence=False)
    assert result.returncode == 7
    assert entry.read_startup(startup) == entry.startup_empty()


def test_hash_mismatch_blocks_unverified_worker(tmp_path):
    result, startup, _ = execute(tmp_path, "raise SystemExit(7)", expected="0" * 64)
    assert result.returncode == 1
    assert entry.read_startup(startup) == dict(
        startup_evidence="PARTIAL",
        startup_stage="BOOTSTRAP_ENTERED",
        startup_error_category="INVALID_DATA",
    )


def test_verified_worker_bytes_not_reopened(tmp_path, monkeypatch):
    path = tmp_path / "worker.py"
    original = b"raise SystemExit(7)"
    path.write_bytes(b"raise SystemExit(99)")
    directory = tmp_path / "startup"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(bootstrap, "worker_bytes", lambda _: original)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "-c",
            str(path),
            hashlib.sha256(original).hexdigest(),
            str(directory),
            "--binding",
            "unused",
            "--mode",
            "baseline",
            "--output",
            "unused",
        ],
    )
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 7


def marker(root, filename, stage, category="NONE"):
    path = root / filename
    path.write_text(json.dumps(dict(schema=1, stage=stage, error_category=category)) + "\n")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_reader_contiguous_phase_prefix(tmp_path, count):
    tmp_path.chmod(0o700)
    for name, stage in zip(entry.STARTUP_FILES[:count], entry.STARTUP_PHASES[:count], strict=True):
        marker(tmp_path, name, stage)
    value = entry.read_startup(tmp_path)
    assert value["startup_evidence"] == (
        "NOT_OBSERVED" if count == 0 else "OBSERVED" if count == 3 else "PARTIAL"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_file",
        "gap",
        "symlink",
        "hardlink",
        "fifo",
        "public_mode",
        "directory_mode",
        "suffix",
        "bool_schema",
        "bad_category",
        "error_without_phase",
        "error_wrong_phase",
    ],
)
def test_reader_rejects_invalid_private_tree(tmp_path, fault):
    tmp_path.chmod(0o700)
    path = marker(tmp_path, "entered.json", "BOOTSTRAP_ENTERED")
    if fault == "unknown_file":
        (tmp_path / "PRIVATE").write_text("secret")
    elif fault == "gap":
        marker(tmp_path, "executing.json", "WORKER_EXECUTION_ENTERED")
    elif fault == "symlink":
        path.unlink()
        path.symlink_to("/etc/passwd")
    elif fault == "hardlink":
        os.link(path, tmp_path.parent / (tmp_path.name + "-link"))
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif fault == "public_mode":
        path.chmod(0o644)
    elif fault == "directory_mode":
        tmp_path.chmod(0o755)
    elif fault == "suffix":
        path.write_bytes(path.read_bytes() + b"PRIVATE")
    elif fault == "bool_schema":
        path.write_text('{"schema":true,"stage":"BOOTSTRAP_ENTERED","error_category":"NONE"}\n')
    elif fault == "bad_category":
        marker(tmp_path, "error.json", "BOOTSTRAP_ENTERED", "PRIVATE")
    elif fault == "error_without_phase":
        path.unlink()
        marker(tmp_path, "error.json", "BOOTSTRAP_ENTERED", "OS_ERROR")
    else:
        marker(tmp_path, "error.json", "WORKER_SOURCE_VERIFIED", "OS_ERROR")
    assert entry.read_startup(tmp_path) == entry.startup_empty("INVALID")


def test_opened_directory_rechecked_after_path_validation(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    checked = entry.checked_directory

    def replace_check(path, **kwargs):
        checked(path, **kwargs)
        path.chmod(0o755)

    monkeypatch.setattr(entry, "checked_directory", replace_check)
    assert entry.read_startup(tmp_path) == entry.startup_empty("INVALID")


def test_directory_close_failure_cannot_escape_observation(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    original = os.close

    def fail_close(fd):
        original(fd)
        raise OSError("PRIVATE cleanup")

    monkeypatch.setattr(entry.os, "close", fail_close)
    assert entry.read_startup(tmp_path) == entry.startup_empty("INVALID")


def test_directory_enumeration_stops_at_fifth_entry(monkeypatch):
    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            for name in [*entry.STARTUP_FILES, "error.json", "extra"]:
                yield type("Row", (), {"name": name})()
            pytest.fail("enumeration exceeded fixed bound")

    monkeypatch.setattr(entry.os, "scandir", lambda _: Entries())
    with pytest.raises(ValueError, match="fixed bound"):
        entry.startup_names(-1)


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(9)], ids=["interrupt", "exit"])
def test_metadata_interruption_is_not_swallowed(tmp_path, monkeypatch, failure):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def interrupted(*args):
        raise failure

    monkeypatch.setattr(bootstrap.os, "write", interrupted)
    try:
        with pytest.raises(type(failure)):
            bootstrap.mark(fd, "entered.json", "BOOTSTRAP_ENTERED")
    finally:
        os.close(fd)


def test_metadata_write_error_keeps_worker_exit(tmp_path, monkeypatch):
    path = tmp_path / "worker.py"
    raw = b"raise SystemExit(7)"
    path.write_bytes(raw)
    evidence = tmp_path / "startup"
    evidence.mkdir(mode=0o700)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "-c",
            str(path),
            hashlib.sha256(raw).hexdigest(),
            str(evidence),
            "--binding",
            "unused",
            "--mode",
            "baseline",
            "--output",
            "unused",
        ],
    )

    def denied(*args):
        raise PermissionError(13, "PRIVATE")

    monkeypatch.setattr(bootstrap.os, "write", denied)
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 7
    assert entry.read_startup(evidence)["startup_evidence"] == "INVALID"


def test_early_failure_controller_projection_publisher_chain(tmp_path):
    result, startup, _ = execute(tmp_path, "import missing_private_synthetic_identifier")
    assert result.returncode == 1
    observation = entry.read_startup(startup)
    value = dict(
        schema=1,
        mode="baseline",
        shell_exit=73,
        qualification="NOT_ASSESSED",
        capture_scope="delivery_framing_only",
        ack_consistency="NOT_ASSESSED",
        stream_correlation="NOT_ASSESSED",
        outer_exception=None,
        record_errors=[],
        worker_returncode=1,
        worker=None,
        ownership=None,
        final_collector_health=None,
        **observation,
    )
    receipt = tmp_path / "result.json"
    receipt.write_text(json.dumps(value) + "\n")
    receipt.chmod(0o600)
    projection = load("startup_projection", ROOT / "scripts/cli_capture_public_summary.py")
    publication = load("startup_publication", ROOT / "scripts/cli_capture_publication.py")
    public = projection.summarize(str(receipt), "baseline", 73)
    assert public["startup_error_category"] == "IMPORT_ERROR"
    assert public["startup_evidence"] == "OBSERVED"
    assert public["worker_returncode"] == 1 and public["harness_shell_exit"] == 73
    assert public["cli_returncode"] is None
    assert publication.validate(public, "baseline.json") == public
    public_root = tmp_path / "public"
    public_root.mkdir(mode=0o700)
    capability = {
        "schema": "forge-cli-capture-capability-v1",
        "status": "PRESENT_NOT_QUALIFIED",
        "facts": dict.fromkeys(publication.FACTS, True),
        "live_admission": "NOT_RUN",
        "qualification": "NOT_ASSESSED",
        "raw_evidence": "NOT_PUBLISHED",
    }
    for name, envelope in (("capability.json", capability), ("baseline.json", public)):
        file = public_root / name
        file.write_text(json.dumps(envelope) + "\n")
        file.chmod(0o600)
    publication.publish(public_root, public_root / "published")
    artifact = (public_root / "published/baseline.json").read_text()
    assert json.loads(artifact) == public
    assert "missing_private" not in artifact and str(tmp_path) not in artifact
    assert "missing_private" not in json.dumps(public)
    assert str(tmp_path) not in json.dumps(public)


def test_worker_main_remains_visible_to_exit_callbacks(tmp_path):
    source = """import atexit
MARKER = "WORKER"
def check_main():
    import __main__, os
    assert __main__.MARKER == "WORKER"
    assert __main__.__dict__ is globals()
    os.write(1, b"worker-main-at-exit")
atexit.register(check_main)
raise SystemExit(7)
"""
    result, _, _ = execute(tmp_path, source)
    assert result.returncode == 7
    assert result.stdout == b"worker-main-at-exit"
    assert result.stderr == b""

"""Exact-byte release controls; synthetic wheels only, no install/build/runtime."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import signal
import struct
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/installed_wheel_publication.py"
WORKFLOW = ROOT / ".github/workflows/installed-wheel.yml"
CANARY = "PRIVATE-wheel-/home/private-user/unit.service-PID-7654321"
STARTED = 1793718000
FINISHED = STARTED + 10


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("installed_wheel_publication", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def fixed_publication_clock(gate, monkeypatch):
    monkeypatch.setattr(gate, "now_seconds", lambda: FINISHED)


@pytest.fixture
def source():
    return {
        "commit": "a" * 40,
        "epoch": 1767225601,
        "files": {
            "src/code_forge/__init__.py": b'"""Approved public package."""\n',
            "src/code_forge_launcher.py": b'"""Approved public launcher."""\n',
            "src/code_forge/gate.schema.json": b'{"type":"object"}\n',
            "src/code_forge/skills/public/SKILL.md": b"Public skill\n",
            "pyproject.toml": (ROOT / "pyproject.toml").read_bytes(),
            "README.md": b"# Public readme\n",
            "LICENSE": b"Public license\n",
        },
    }


def wheel_bytes(gate, source, *, stamp=STARTED, mutate=None, compression_level=None):
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in gate.expected_members(source):
            seconds = stamp if name.startswith(gate.DIST_INFO + "/") else source["epoch"]
            info = zipfile.ZipInfo(name, time.gmtime(seconds)[:6])
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            if mutate:
                info, data = mutate(info, data)
            if info is not None:
                archive.writestr(info, data, compresslevel=compression_level)
    return sink.getvalue()


def rewrite(raw, mutate):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(output, "w") as changed:
        for item in original.infolist():
            info, content = mutate(item, original.read(item.filename))
            if info is not None:
                changed.writestr(info, content)
    return output.getvalue()


def private_case(tmp_path, gate, source, *, raw=None):
    root = tmp_path / "runner"
    private = root / "forge-wheel-qualification.ABC123xy"
    private.mkdir(parents=True, mode=0o700)
    (private / "started").write_text(str(STARTED) + "\n")
    (private / "finished").write_text(str(FINISHED) + "\n")
    qualification = private / "qualification"
    dist = qualification / "dist"
    dist.mkdir(parents=True)
    raw = wheel_bytes(gate, source) if raw is None else raw
    (dist / gate.WHEEL_NAME).write_bytes(raw)
    proof = {
        "source_commit": source["commit"],
        "wheel": gate.WHEEL_NAME,
        "wheel_sha256": hashlib.sha256(raw).hexdigest(),
        "private_log": CANARY,
    }
    (qualification / "build-proof.json").write_text(json.dumps(proof))
    return root, private, tmp_path / "public", raw


def test_closed_member_map_metadata_and_record_are_public_source_derived(gate, source):
    members = gate.expected_members(source)
    assert [name for name, _, _ in members] == [
        "code_forge_launcher.py",
        "code_forge/__init__.py",
        "code_forge/gate.schema.json",
        "code_forge/skills/public/SKILL.md",
        gate.DIST_INFO + "/licenses/LICENSE",
        gate.DIST_INFO + "/METADATA",
        gate.DIST_INFO + "/WHEEL",
        gate.DIST_INFO + "/entry_points.txt",
        gate.DIST_INFO + "/top_level.txt",
        gate.DIST_INFO + "/RECORD",
    ]
    contents = {name: data for name, data, _ in members}
    assert contents[gate.DIST_INFO + "/licenses/LICENSE"] == source["files"]["LICENSE"]
    assert contents[gate.DIST_INFO + "/METADATA"].split(b"\n\n", 1)[1] == source["files"]["README.md"]
    assert b"Generator: setuptools (84.2.1)\n" in contents[gate.DIST_INFO + "/WHEEL"]
    assert b"Dynamic: license-file\n" in contents[gate.DIST_INFO + "/METADATA"]
    assert contents[gate.DIST_INFO + "/RECORD"].endswith((gate.DIST_INFO + "/RECORD,,\n").encode())
    assert all(CANARY.encode() not in data for _, data, _ in members)


@pytest.mark.parametrize("timestamp", [STARTED, STARTED + 2, FINISHED])
def test_canonical_bytes_accept_only_approved_public_build_timestamp_variation(gate, source, timestamp):
    raw = wheel_bytes(gate, source, stamp=timestamp)
    assert gate.validate_wheel(raw, source, STARTED, FINISHED) is None


def test_odd_started_second_allows_only_dos_floor(gate, source):
    raw = wheel_bytes(gate, source, stamp=STARTED)
    gate.validate_wheel(raw, source, STARTED + 1, FINISHED)
    with pytest.raises(ValueError):
        gate.validate_wheel(raw, source, STARTED + 2, FINISHED)


@pytest.mark.parametrize("timestamp", [STARTED - 2, FINISHED + 2, 315532800])
def test_outside_approved_build_clock_is_rejected(gate, source, timestamp):
    with pytest.raises(ValueError):
        gate.validate_wheel(wheel_bytes(gate, source, stamp=timestamp), source, STARTED, FINISHED)


@pytest.mark.parametrize("started,finished", [(STARTED, STARTED - 1), (STARTED, STARTED + 901)])
def test_invalid_build_interval_is_rejected(gate, source, started, finished):
    with pytest.raises(ValueError):
        gate.validate_wheel(wheel_bytes(gate, source), source, started, finished)


@pytest.mark.parametrize(
    "target",
    [
        "code_forge/__init__.py",
        "/LICENSE",
        "/METADATA",
        "/WHEEL",
        "/entry_points.txt",
        "/top_level.txt",
        "/RECORD",
    ],
)
def test_canary_in_any_payload_or_generated_member_is_rejected(gate, source, target):
    def mutate(info, content):
        return info, content + CANARY.encode() if info.filename.endswith(target) else content

    with pytest.raises(ValueError):
        gate.validate_wheel(wheel_bytes(gate, source, mutate=mutate), source, STARTED, FINISHED)


@pytest.mark.parametrize(
    "kind",
    [
        "comment",
        "extra",
        "mode",
        "creator",
        "internal_attr",
        "extract_version",
        "compression",
        "source_timestamp",
        "name",
        "missing",
    ],
)
def test_every_container_field_is_closed_not_just_extracted_content(gate, source, kind):
    def mutate(info, content):
        if info.filename != "code_forge/__init__.py":
            return info, content
        if kind == "comment":
            info.comment = CANARY.encode()
        elif kind == "extra":
            info.extra = b"\xfe\xca" + len(CANARY).to_bytes(2, "little") + CANARY.encode()
        elif kind == "mode":
            info.external_attr = 0o120777 << 16
        elif kind == "creator":
            info.create_system = 0
        elif kind == "internal_attr":
            info.internal_attr = 1
        elif kind == "extract_version":
            info.extract_version = 45
        elif kind == "compression":
            info.compress_type = zipfile.ZIP_STORED
        elif kind == "source_timestamp":
            info.date_time = time.gmtime(STARTED)[:6]
        elif kind == "name":
            info.filename = "../" + CANARY
        elif kind == "missing":
            return None, content
        return info, content

    with pytest.raises(ValueError):
        gate.validate_wheel(wheel_bytes(gate, source, mutate=mutate), source, STARTED, FINISHED)


def test_alternative_valid_deflate_is_not_accepted_as_equivalent_public_bytes(gate, source):
    raw = wheel_bytes(gate, source, compression_level=0)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.read("code_forge/__init__.py") == source["files"]["src/code_forge/__init__.py"]
    with pytest.raises(ValueError):
        gate.validate_wheel(raw, source, STARTED, FINISHED)


@pytest.mark.parametrize(
    "kind",
    [
        "prefix",
        "trailer",
        "archive_comment",
        "duplicate",
        "extra_member",
        "reordered",
        "empty",
        "truncated",
        "oversized",
    ],
)
def test_zip_slack_unknown_members_and_structure_fail_closed(gate, source, kind):
    raw = wheel_bytes(gate, source)
    if kind == "prefix":
        raw = CANARY.encode() + raw
    elif kind == "trailer":
        raw += CANARY.encode()
    elif kind in {"archive_comment", "duplicate", "extra_member"}:
        sink = io.BytesIO(raw)
        with zipfile.ZipFile(sink, "a") as archive:
            if kind == "archive_comment":
                archive.comment = CANARY.encode()
            else:
                name = "code_forge/__init__.py" if kind == "duplicate" else "private.log"
                with (
                    pytest.warns(UserWarning)
                    if kind == "duplicate"
                    else __import__("contextlib").nullcontext()
                ):
                    archive.writestr(name, CANARY)
        raw = sink.getvalue()
    elif kind == "reordered":
        sink = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(sink, "w") as changed:
            for info in reversed(original.infolist()):
                changed.writestr(info, original.read(info.filename))
        raw = sink.getvalue()
    elif kind == "empty":
        raw = b""
    elif kind == "truncated":
        raw = raw[:-1]
    elif kind == "oversized":
        raw += b"x" * gate.MAX_WHEEL
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        gate.validate_wheel(raw, source, STARTED, FINISHED)


def test_record_timestamp_must_follow_generated_metadata(gate, source):
    def mutate(info, data):
        if info.filename.endswith("/RECORD"):
            info.date_time = time.gmtime(STARTED)[:6]
        return info, data

    with pytest.raises(ValueError):
        gate.validate_wheel(
            wheel_bytes(gate, source, stamp=FINISHED, mutate=mutate), source, STARTED, FINISHED
        )


@pytest.mark.parametrize(
    "path",
    [
        "src/../private.py",
        "src/code_forge/private.pyc",
        "src/private.log",
        "src/code_forge/secret.bin",
        "src/code_forge/é.py",
    ],
)
def test_unreviewed_source_layout_is_rejected(gate, source, path):
    source["files"][path] = CANARY.encode()
    with pytest.raises(ValueError):
        gate.expected_members(source)


def test_unreviewed_backend_or_dependency_metadata_fails_closed(gate, source):
    original = source["files"]["pyproject.toml"]
    for old, new in (
        (b"pyyaml>=6.0", b"unreviewed>=1"),
        (b"semgrep>=1.176,<1.183", b"semgrep>=1.176,<1.184"),
        (b"semgrep>=1.176,<1.184", b"semgrep>=1.176,<1.185"),
        (b"platform_system != 'Darwin' or", b"platform_system != 'Darwin' and"),
        (b"platform_machine == 'x86_64'", b"platform_machine == 'arm64'"),
    ):
        assert old in original
        source["files"]["pyproject.toml"] = original.replace(old, new)
        with pytest.raises(KeyError):
            gate.expected_members(source)


def test_public_copy_is_exact_original_qualified_bytes_and_no_proof_escapes(tmp_path, gate, source):
    root, private, output, raw = private_case(tmp_path, gate, source)
    gate.publish(root, output, source)
    assert (output / gate.WHEEL_NAME).read_bytes() == raw
    assert list(output.iterdir()) == [output / gate.WHEEL_NAME]
    assert output.stat().st_mode & 0o077 == 0
    assert (output / gate.WHEEL_NAME).stat().st_mode & 0o077 == 0
    assert CANARY in (private / "qualification/build-proof.json").read_text()
    assert CANARY.encode() not in (output / gate.WHEEL_NAME).read_bytes()


@pytest.mark.parametrize("field", ["source_commit", "wheel", "wheel_sha256"])
def test_original_wheel_must_still_match_qualification_proof(tmp_path, gate, source, field):
    root, private, output, _ = private_case(tmp_path, gate, source)
    path = private / "qualification/build-proof.json"
    proof = json.loads(path.read_text())
    proof[field] = CANARY
    path.write_text(json.dumps(proof))
    with pytest.raises(ValueError):
        gate.publish(root, output, source)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["duplicate", "malformed", "nan", "oversized", "missing"])
def test_private_proof_errors_do_not_create_public_output(tmp_path, gate, source, kind):
    root, private, output, _ = private_case(tmp_path, gate, source)
    path = private / "qualification/build-proof.json"
    if kind == "missing":
        path.unlink()
    else:
        data = {
            "duplicate": path.read_text()[:-1] + ',"wheel":"x"}',
            "malformed": CANARY,
            "nan": path.read_text()[:-1] + ',"unknown":NaN}',
            "oversized": "x" * (gate.MAX_PROOF + 1),
        }[kind]
        path.write_text(data)
    with pytest.raises((OSError, ValueError)):
        gate.publish(root, output, source)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "extra", "missing", "changed_bytes"])
def test_wheel_filesystem_boundary_is_closed(tmp_path, gate, source, kind):
    root, private, output, _ = private_case(tmp_path, gate, source)
    path = private / "qualification/dist" / gate.WHEEL_NAME
    if kind in {"symlink", "fifo", "directory", "missing"}:
        path.unlink()
    if kind == "symlink":
        path.symlink_to(tmp_path / CANARY)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "extra":
        (path.parent / "extra.whl").write_text(CANARY)
    elif kind == "changed_bytes":
        path.write_text(CANARY)
    with pytest.raises((OSError, ValueError)):
        gate.publish(root, output, source)
    assert not output.exists()


@pytest.mark.parametrize("part", ["qualification", "qualification/dist"])
def test_intermediate_private_symlinks_are_not_followed(tmp_path, gate, source, part):
    root, private, output, _ = private_case(tmp_path, gate, source)
    path = private / part
    target = tmp_path / "moved"
    path.rename(target)
    path.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        gate.publish(root, output, source)
    assert not output.exists()


@pytest.mark.parametrize(
    "name",
    [
        "forge-wheel-qualification.short",
        "forge-wheel-qualification.extra.ABC123xy",
        "forge-wheel-qualification.éBC123xy",
    ],
)
def test_private_directory_name_is_exact_not_just_a_suffix(tmp_path, gate, source, name):
    root, private, output, _ = private_case(tmp_path, gate, source)
    private.rename(root / name)
    with pytest.raises(ValueError):
        gate.publish(root, output, source)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["stale", "symlink"])
def test_existing_public_output_is_never_reused(tmp_path, gate, source, kind):
    root, _, output, _ = private_case(tmp_path, gate, source)
    if kind == "stale":
        output.mkdir()
        (output / gate.WHEEL_NAME).write_text(CANARY)
    else:
        output.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(FileExistsError):
        gate.publish(root, output, source)


@pytest.mark.parametrize("field", ["st_size", "st_mtime_ns", "st_ctime_ns"])
def test_regular_reader_rejects_inflight_change_and_closes_fd(tmp_path, gate, monkeypatch, field):
    (tmp_path / "file").write_bytes(b"public")
    root = gate.open_directory(tmp_path)
    real_stat, real_close = gate.os.fstat, gate.os.close
    calls, closed = [], []

    def fstat(fd):
        value = real_stat(fd)
        calls.append(fd)
        attrs = {
            key: getattr(value, key) for key in ("st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        if len(calls) == 2:
            attrs[field] += 1
        return SimpleNamespace(**attrs)

    monkeypatch.setattr(gate.os, "fstat", fstat)
    monkeypatch.setattr(gate.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    try:
        with pytest.raises(ValueError):
            gate.read_regular(root, "file", 1024)
        assert len(calls) == 2 and closed == [calls[0]]
    finally:
        real_close(root)


def test_real_git_snapshot_is_read_only_and_matches_repository_public_files(gate):
    source = gate.source_from_git(ROOT)
    assert len(source["commit"]) == 40 and type(source["epoch"]) is int
    assert (
        source["files"]["src/code_forge_launcher.py"]
        == (ROOT / "src/code_forge_launcher.py").read_bytes()
    )
    assert all(
        name.startswith("src/") or name in {"pyproject.toml", "README.md", "LICENSE"}
        for name in source["files"]
    )


def release_step():
    value = yaml.safe_load(WORKFLOW.read_text())
    return next(
        step for step in value["jobs"]["wheel-runtime"]["steps"] if step.get("id") == "wheel_release"
    )


def test_workflow_uploads_only_exact_original_wheel_after_success():
    value = yaml.safe_load(WORKFLOW.read_text())
    steps = value["jobs"]["wheel-runtime"]["steps"]
    release = release_step()
    assert "if" not in release and "continue-on-error" not in release
    assert release["run"].startswith("exec >/dev/null 2>&1\n")
    assert 'exec >"$private/commands.log" 2>&1' in release["run"]
    assert "/usr/bin/python3 -I -S -B scripts/installed_wheel_publication.py" in release["run"]
    upload = next(
        step
        for step in steps
        if step.get("with", {}).get("name")
        == "installed-wheel-evidence-${{ matrix.runtime }}-${{ github.run_attempt }}"
    )
    assert upload["if"] == "${{ success() && steps.wheel_release.outcome == 'success' }}"
    assert (
        upload["with"]["path"]
        == "forge-installed-wheel-artifact/code_review_forge-2.9.0-py3-none-any.whl"
    )
    assert upload["with"]["if-no-files-found"] == "error"
    qualify = next(step for step in steps if step.get("id") == "qualify")["run"]
    assert qualify.index('date -u +%s >"$private/started"') < qualify.index(
        "python scripts/qualify_installed_wheel.py"
    )
    assert 'exit "$qualification_code"' in qualify
    assert qualify.index('exit "$qualification_code"') < qualify.index(
        'date -u +%s >"$private/finished"'
    )


@pytest.mark.parametrize(
    "body,code",
    [
        (f"raise RuntimeError({CANARY!r})", 1),
        ("import os,signal; os.kill(os.getpid(),signal.SIGKILL)", 137),
        ("import sys; sys.exit(17)", 17),
    ],
)
def test_actual_release_shell_keeps_helper_errors_and_kill_private(tmp_path, body, code):
    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / "scripts" / SCRIPT.name).write_text(
        f"import sys\nprint({CANARY!r},flush=True)\nprint({CANARY!r},file=sys.stderr,flush=True)\n"
        + body
        + "\n"
    )
    shell = tmp_path / "step.sh"
    shell.write_text(release_step()["run"])
    proc = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(shell)],
        cwd=workspace,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "SETUP_OUTCOME": "success",
            "QUALIFICATION_OUTCOME": "success",
        },
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode == code and proc.stdout == proc.stderr == b""
    assert not (workspace / "forge-installed-wheel-artifact").exists()
    logs = list(tmp_path.glob("forge-wheel-release.*/commands.log"))
    assert len(logs) == 1 and CANARY in logs[0].read_text()


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped", CANARY, ""])
def test_cli_cannot_release_without_successful_original_qualification(tmp_path, status):
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), str(tmp_path), str(tmp_path / "out")],
        env={**os.environ, "SETUP_OUTCOME": "success", "QUALIFICATION_OUTCOME": status},
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode != 0 and not (tmp_path / "out").exists()
    assert CANARY.encode() not in proc.stdout


@pytest.mark.parametrize("now", [STARTED - 1, FINISHED - 1, STARTED + 901])
def test_private_clock_must_be_current_and_not_future(tmp_path, gate, source, monkeypatch, now):
    root, _, output, _ = private_case(tmp_path, gate, source)
    monkeypatch.setattr(gate, "now_seconds", lambda: now)
    with pytest.raises(ValueError):
        gate.publish(root, output, source)
    assert not output.exists()


@pytest.mark.parametrize("field", ["SETUP_OUTCOME", "QUALIFICATION_OUTCOME", "JOB_STATUS"])
def test_cli_requires_all_runner_outcomes_before_reading_any_artifact(tmp_path, field):
    env = {
        **os.environ,
        "SETUP_OUTCOME": "success",
        "QUALIFICATION_OUTCOME": "success",
        "JOB_STATUS": "success",
        "RUNNER_TEMP": str(tmp_path),
    }
    env[field] = "failure"
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), str(tmp_path), "forge-installed-wheel-artifact"],
        env=env,
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode != 0 and b"qualification not successful" in proc.stderr
    assert not (tmp_path / "forge-installed-wheel-artifact").exists()


@pytest.mark.parametrize(
    "kind", ["wrong_private", "relative_private", "ancestor_symlink", "wrong_public", "wrong_cwd"]
)
def test_cli_rejects_paths_outside_the_exact_workflow_contract(tmp_path, kind):
    root = tmp_path / "runner"
    root.mkdir()
    private = str(root)
    public = "forge-installed-wheel-artifact"
    cwd = ROOT
    env = {
        **os.environ,
        "SETUP_OUTCOME": "success",
        "QUALIFICATION_OUTCOME": "success",
        "JOB_STATUS": "success",
        "RUNNER_TEMP": private,
    }
    if kind == "wrong_private":
        private = str(tmp_path / "other")
    elif kind == "relative_private":
        private = "relative"
        env["RUNNER_TEMP"] = private
    elif kind == "ancestor_symlink":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        (root / "inner").mkdir()
        private = str(link / "inner")
        env["RUNNER_TEMP"] = private
    elif kind == "wrong_public":
        public = str(tmp_path / "other")
    elif kind == "wrong_cwd":
        cwd = tmp_path
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), private, public],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode != 0 and b"workflow paths" in proc.stderr


def test_local_header_disagreement_is_rejected_even_when_central_directory_is_clean(gate, source):
    raw = bytearray(wheel_bytes(gate, source))
    raw[10] ^= 2  # Local-only DOS time change; central directory is unchanged.
    with pytest.raises(ValueError):
        gate.validate_wheel(bytes(raw), source, STARTED, FINISHED)


def test_unused_bytes_inside_a_deflate_span_cannot_escape_via_normal_zip_read(gate, source):
    raw = wheel_bytes(gate, source)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        first = archive.infolist()[0]
        central = archive.start_dir
    name_len, extra_len = struct.unpack_from("<HH", raw, first.header_offset + 26)
    end = first.header_offset + 30 + name_len + extra_len + first.compress_size
    hidden = CANARY.encode()
    changed = bytearray(raw[:end] + hidden + raw[end:])
    struct.pack_into("<I", changed, first.header_offset + 18, first.compress_size + len(hidden))
    cursor = central + len(hidden)
    while changed[cursor : cursor + 4] == b"PK\x01\x02":
        offset = struct.unpack_from("<I", changed, cursor + 42)[0]
        if offset == first.header_offset:
            struct.pack_into("<I", changed, cursor + 20, first.compress_size + len(hidden))
        else:
            struct.pack_into("<I", changed, cursor + 42, offset + len(hidden))
        lengths = struct.unpack_from("<HHH", changed, cursor + 28)
        cursor += 46 + sum(lengths)
    assert changed[cursor : cursor + 4] == b"PK\x05\x06"
    struct.pack_into("<I", changed, cursor + 16, central + len(hidden))
    with zipfile.ZipFile(io.BytesIO(changed)) as archive:
        assert archive.read(first.filename) == source["files"]["src/" + first.filename]
    with pytest.raises(ValueError):
        gate.validate_wheel(bytes(changed), source, STARTED, FINISHED)


def test_actual_release_timeout_after_partial_output_still_has_no_success(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / "scripts" / SCRIPT.name).write_text(
        "import pathlib,time\n"
        'p=pathlib.Path("forge-installed-wheel-artifact");p.mkdir()\n'
        f'(p/"code_review_forge-2.9.0-py3-none-any.whl").write_text({CANARY!r})\n'
        f"print({CANARY!r},flush=True)\n"
        "time.sleep(60)\n"
    )
    shell = tmp_path / "step.sh"
    shell.write_text(release_step()["run"])
    proc = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(shell)],
        cwd=workspace,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "SETUP_OUTCOME": "success",
            "QUALIFICATION_OUTCOME": "success",
            "JOB_STATUS": "success",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        marker = (
            workspace / "forge-installed-wheel-artifact" / "code_review_forge-2.9.0-py3-none-any.whl"
        )
        deadline = time.monotonic() + 10
        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=0.3)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode != 0 and stdout == stderr == b""
    assert (workspace / "forge-installed-wheel-artifact").is_dir()
    upload = next(
        item
        for item in yaml.safe_load(WORKFLOW.read_text())["jobs"]["wheel-runtime"]["steps"]
        if item.get("with", {}).get("name")
        == "installed-wheel-evidence-${{ matrix.runtime }}-${{ github.run_attempt }}"
    )
    assert upload["if"] == "${{ success() && steps.wheel_release.outcome == 'success' }}"

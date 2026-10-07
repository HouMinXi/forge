"""Owned command teardown uses identities, including escaped descendants."""

import io
import asyncio
import inspect
import json
import fcntl
import ctypes
from contextlib import ExitStack
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest
from code_forge import _mutation_process as process


@pytest.mark.parametrize("name", [b"\xffprobe", b"a ) b\nc", b"\xc2\xb5probe"])
def test_identity_preserves_fields_with_opaque_comm(name):
    libc = ctypes.CDLL(None, use_errno=True)
    original = ctypes.create_string_buffer(16)
    assert libc.prctl(16, original, 0, 0, 0) == 0
    before = process._identity(os.getpid())
    try:
        assert libc.prctl(15, ctypes.create_string_buffer(name), 0, 0, 0) == 0
        assert Path(f"/proc/{before.pid}/comm").read_bytes() == name + b"\n"
        assert process._identity(before.pid) == before
        assert isinstance(process._identity(before.pid).state, str)
    finally:
        assert libc.prctl(15, original, 0, 0, 0) == 0
    assert process._identity(before.pid) == before


@pytest.mark.parametrize("children, expected", [(b"", []), (b"111 55 111 \n", [55, 111])])
def test_children_read_numeric_bytes_without_default_text_decoding(monkeypatch, children, expected):
    parent = process._Identity(999, 1, 100, "S")
    monkeypatch.setattr(process, "_identity", lambda _: parent)
    monkeypatch.setattr(Path, "iterdir", lambda _: iter([Path("/proc/999/task/1000")]))
    seen = []

    def read_bytes(path):
        seen.append(path)
        return children

    def read_text(*args, **kwargs):
        raise AssertionError("numeric procfs data must not use default text decoding")

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)
    assert process._children(parent) == expected
    assert seen == [Path("/proc/999/task/1000/children")]


def _kernel_identity(pid):
    """Rescue observes kernel bytes independently of the reader under test."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b") ", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return {"pid": pid, "parent": int(fields[1]), "start_ticks": int(fields[19]),
            "state": fields[0].decode("ascii")}


def _rescue_kernel_identity(record):
    current = _kernel_identity(record["pid"])
    if current is None or current["start_ticks"] != record["start_ticks"]:
        return
    try:
        fd = os.pidfd_open(record["pid"])
    except ProcessLookupError:
        return
    try:
        current = _kernel_identity(record["pid"])
        if current is not None and current["start_ticks"] == record["start_ticks"]:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)


@pytest.mark.parametrize("finish", ["normal", "timeout"])
def test_opaque_descendant_is_cleaned_and_unrelated_child_is_untouched(tmp_path, finish):
    marker = tmp_path / "opaque.json"
    ready = tmp_path / "opaque.ready"
    script = f"""
import ctypes, json, os, time
from pathlib import Path
def identity(pid):
    fields = Path('/proc/' + str(pid) + '/stat').read_bytes().rsplit(b') ', 1)[1].split()
    return {{'pid': pid, 'start_ticks': int(fields[19])}}
driver = identity(os.getpid())
owner = identity(os.getppid())
if os.fork() == 0:
    libc = ctypes.CDLL(None, use_errno=True)
    original = ctypes.create_string_buffer(16)
    assert libc.prctl(16, original, 0, 0, 0) == 0
    try:
        pending = Path({str(marker)!r}).with_suffix('.tmp')
        pending.write_text(json.dumps({{'child': identity(os.getpid()),
            'driver': driver, 'owner': owner}}), encoding='utf-8')
        pending.replace({str(marker)!r})
        assert libc.prctl(15, ctypes.create_string_buffer(b'\\xffprobe'), 0, 0, 0) == 0
        Path({str(ready)!r}).touch()
        time.sleep(10)
    finally:
        assert libc.prctl(15, original, 0, 0, 0) == 0
else:
    deadline = time.monotonic() + 2
    while not Path({str(ready)!r}).exists() and time.monotonic() < deadline:
        time.sleep(.005)
    assert Path({str(ready)!r}).exists()
    {"time.sleep(10)" if finish == "timeout" else "pass"}
"""
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    before = None
    try:
        before = _kernel_identity(unrelated.pid)
        if finish == "timeout":
            with pytest.raises(subprocess.TimeoutExpired) as failure:
                process.run_owned_command([sys.executable, "-c", script], timeout=0.3)
            report = failure.value.ownership
        else:
            result = process.run_owned_command([sys.executable, "-c", script], timeout=3)
            assert result.returncode == 0
            report = result.ownership
        recorded = _wait_json(marker)
        child = recorded["child"]
        owned = {item["pid"]: item for item in report["owned"]}
        assert report["cleanup_complete"]
        assert owned[child["pid"]]["start_ticks"] == child["start_ticks"]
        assert owned[child["pid"]]["reaped_status"] is not None
        assert all(not item["remaining"] for item in report["owned"])
        assert _kernel_identity(child["pid"]) is None
        assert _kernel_identity(unrelated.pid)["start_ticks"] == before["start_ticks"]
        assert unrelated.poll() is None
    finally:
        with ExitStack() as cleanup:
            cleanup.callback(unrelated.wait, timeout=3)
            if before is None:
                cleanup.callback(unrelated.kill)
            else:
                cleanup.callback(_rescue_kernel_identity, before)
            if marker.exists():
                recorded = _wait_json(marker)
                for role in ("owner", "driver", "child"):
                    cleanup.callback(_rescue_kernel_identity, recorded[role])


@pytest.mark.parametrize("failure", ["marker", "rescue"])
def test_opaque_lifecycle_failure_cannot_skip_other_rescue(tmp_path, monkeypatch, failure):
    real_popen = subprocess.Popen
    real_wait_json = _wait_json
    real_rescue = _rescue_kernel_identity
    children = []
    records = []
    attempted = []

    def launch(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def completed(*args, **kwargs):
        marker = tmp_path / "opaque.json"
        if failure == "marker":
            marker.write_bytes(b"")
        else:
            for _ in range(3):
                child = launch([sys.executable, "-c", "import time; time.sleep(10)"])
                records.append(_kernel_identity(child.pid))
            marker.write_text(json.dumps(dict(zip(("child", "driver", "owner"), records, strict=True))),
                              encoding="utf-8")
        result = subprocess.CompletedProcess([], 0)
        result.ownership = {"cleanup_complete": True, "owned": []}
        return result

    def rescue(record):
        attempted.append(record["pid"])
        if records and record["pid"] == records[0]["pid"]:
            raise RuntimeError("injected rescue failure")
        real_rescue(record)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", launch)
            patch.setattr(process, "run_owned_command", completed)
            patch.setitem(globals(), "_wait_json", lambda path: real_wait_json(path, timeout=.05))
            patch.setitem(globals(), "_rescue_kernel_identity", rescue)
            with pytest.raises(AssertionError if failure == "marker" else RuntimeError):
                test_opaque_descendant_is_cleaned_and_unrelated_child_is_untouched(tmp_path, "normal")
        assert children[0].poll() == -signal.SIGKILL
        assert children[0].pid in attempted
        if records:
            assert {record["pid"] for record in records} <= set(attempted)
            for child in children[2:]:
                assert child.wait(timeout=2) == -signal.SIGKILL
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)


def test_ordinary_isolated_owner_never_resolves_optional_dependencies(tmp_path, monkeypatch):
    from code_forge import _mutation_imports as imports

    def unavailable():
        raise AssertionError("ordinary owner requested optional dependencies")

    monkeypatch.setattr(imports, "_requirements_api", unavailable)
    seen = []
    real = subprocess.Popen

    def launch(argv, **kwargs):
        seen.append(argv)
        return real(argv, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", launch)
    result = process.run_owned_command(
        [sys.executable, "-I", "-S", "-c", "print('ordinary selected owner')"],
        cwd=str(tmp_path),
        timeout=3,
        text=True,
    )
    assert result.stdout == "ordinary selected owner\n"
    assert result.returncode == 0 and result.ownership["cleanup_complete"]
    assert seen == [[sys.executable, "-I", "-S", "-c", imports.ISOLATED_BOOTSTRAP]]


@pytest.fixture
def genuine_rewritten_owner(tmp_path, monkeypatch):
    """Only installed pure rewrite API; no engine/stats/baseline is executed."""
    import ast
    import importlib.metadata
    import types

    try:
        importlib.metadata.distribution("mutmut")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("optional installed mutmut pure rewrite API unavailable")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "setup.cfg").write_text("[mutmut]\nsource_paths=src\n")
    from mutmut.mutation.file_mutation import mutate_file_contents

    raw = Path(process.__file__).read_text()
    lines = [
        i for i, line in enumerate(raw.splitlines(), 1) if line.strip() == "if os.getppid() != parent:"
    ]
    assert len(lines) == 1
    rewritten = mutate_file_contents(process.__file__, raw, covered_lines=set(lines))
    if isinstance(rewritten, tuple):
        generated, names = rewritten
    else:
        generated, names = rewritten.code, rewritten.mutant_names
    variants = [
        node.name
        for node in ast.parse(generated).body
        if isinstance(node, ast.FunctionDef)
        and node.name in names
        and any(
            isinstance(test, ast.Compare) and ast.unparse(test) == "os.getppid() == parent"
            for test in ast.walk(node)
        )
    ]
    assert len(variants) == 1
    path = tmp_path / "rewritten_owner.py"
    path.write_text(generated)
    name = "code_forge._owned_rewritten_supervisor"
    owner = types.ModuleType(name)
    owner.__file__ = str(path)
    owner.__package__ = "code_forge"
    monkeypatch.setitem(sys.modules, name, owner)
    monkeypatch.setenv("MUTANT_UNDER_TEST", "")
    exec(compile(generated, str(path), "exec"), owner.__dict__)  # noqa: S102 - actual pure rewrite of owned source
    return owner, name + "." + variants[0]


@pytest.mark.parametrize("selected", [False, True])
def test_actual_rewritten_public_owner_preserves_selected_supervisor(
    tmp_path, monkeypatch, genuine_rewritten_owner, selected
):
    owner, variant = genuine_rewritten_owner
    marker = tmp_path / "actual-driver"
    command = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        f"from pathlib import Path;Path({str(marker)!r}).write_text('actual selected owner');print('owned')",
    ]
    monkeypatch.setenv("MUTANT_UNDER_TEST", variant if selected else "")
    if selected:
        with pytest.raises(
            owner.MutationProcessError, match="caller exited before command launch"
        ) as failure:
            owner.run_owned_command(command, timeout=3, cwd=str(tmp_path), text=True)
        assert failure.value.cleanup_complete
        assert failure.value.report["owned"] == []
        assert not marker.exists()
    else:
        result = owner.run_owned_command(command, timeout=3, cwd=str(tmp_path), text=True)
        assert result.stdout == "owned\n" and result.returncode == 0
        assert result.ownership["cleanup_complete"]
        assert marker.read_text() == "actual selected owner"


@pytest.mark.parametrize(
    "damage",
    [
        "hash",
        "identity",
        "missing",
        "fifo",
        "name",
        "schema",
        "unbound",
        "foreign",
        "pin-schema",
        "pin-path",
        "module-schema",
        "distribution-schema",
    ],
)
def test_invalid_bootstrap_authority_refuses_before_actual_driver(tmp_path, monkeypatch, damage):
    from code_forge import _mutation_imports as imports

    selected = tmp_path / "owner.py"
    selected.write_bytes(Path(process.__file__).read_bytes())
    authority = imports.prepare_owner(str(selected), "code_forge._owned_authority_control")
    marker = tmp_path / "driver"
    if damage == "hash":
        authority["owner"]["sha256"] = "0" * 64
    elif damage == "identity":
        authority["owner"]["ino"] += 1
    elif damage in ("missing", "fifo"):
        selected.unlink()
        if damage == "fifo":
            os.mkfifo(selected)
    elif damage == "name":
        authority["module_name"] = "invalid-owner-name"
    elif damage == "schema":
        authority["modules"] = []
    elif damage == "unbound":
        authority["modules"]["unbound"] = dict(authority["owner"], kind="source", distribution="absent")
    elif damage == "foreign":
        authority["distributions"]["owned"] = dict(
            root=str(tmp_path / "narrow"), metadata=authority["owner"]
        )
    elif damage == "pin-schema":
        authority["owner"]["dev"] = True
    elif damage == "pin-path":
        authority["owner"]["path"] = "relative-owner.py"
    elif damage == "module-schema":
        authority["modules"]["invalid-name"] = authority["owner"]
    else:
        authority["distributions"]["owned"] = []
    monkeypatch.setattr(imports, "prepare_owner", lambda *_: authority)
    reason = {
        "hash": "mutation import bytes changed",
        "identity": "mutation import identity changed",
        "missing": "FileNotFoundError",
        "fifo": "mutation import identity changed",
        "name": "invalid selected mutation owner name",
        "schema": "malformed mutation dependency authority",
        "unbound": "unbound mutation module authority",
        "foreign": "foreign mutation metadata origin",
        "pin-schema": "malformed mutation import pin",
        "pin-path": "mutation import path must be absolute",
        "module-schema": "malformed mutation module authority",
        "distribution-schema": "malformed mutation distribution authority",
    }[damage]
    with pytest.raises(process.MutationProcessError, match=reason) as failure:
        process.run_owned_command(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                f"from pathlib import Path;Path({str(marker)!r}).write_text('unreachable')",
            ],
            timeout=3,
        )
    assert failure.value.cleanup_complete is False
    assert not marker.exists()


@pytest.mark.parametrize("import_name", ["pytest", "unlisted_owner_dependency"])
def test_isolated_owner_refuses_unlisted_installed_and_cwd_imports(tmp_path, monkeypatch, import_name):
    from code_forge import _mutation_imports as imports

    selected = tmp_path / "owner.py"
    selected.write_text(Path(process.__file__).read_text() + f"\nimport {import_name}\n")
    marker = tmp_path / "foreign-module"
    (tmp_path / "unlisted_owner_dependency.py").write_text(
        f"from pathlib import Path;Path({str(marker)!r}).write_text('foreign execution')\n"
    )
    real = imports.prepare_owner
    monkeypatch.setattr(
        imports, "prepare_owner", lambda *_: real(str(selected), "code_forge._owned_unlisted_control")
    )
    with pytest.raises(process.MutationProcessError, match="unlisted mutation owner import") as failure:
        process.run_owned_command(
            [sys.executable, "-I", "-S", "-c", "print('unreachable')"], timeout=3, cwd=str(tmp_path)
        )
    assert failure.value.cleanup_complete is False
    assert not marker.exists()


def test_declared_dependency_origin_cannot_move_to_foreign_owned_file(
    tmp_path, monkeypatch, genuine_rewritten_owner
):
    from code_forge import _mutation_imports as imports

    owner, _ = genuine_rewritten_owner
    real = imports.prepare_owner
    authority = real(owner.__file__, owner.__name__)
    foreign = tmp_path / "foreign-mutmut.py"
    foreign.write_text("raise AssertionError('foreign dependency executed')\n")
    pin, _ = imports._pin_file(foreign)
    authority["modules"]["mutmut"].update(pin)
    monkeypatch.setattr(imports, "prepare_owner", lambda *_: authority)
    with pytest.raises(owner.MutationProcessError, match="foreign mutation module origin") as failure:
        owner.run_owned_command([sys.executable, "-I", "-S", "-c", "print('unreachable')"], timeout=3)
    assert failure.value.cleanup_complete is False


@pytest.mark.parametrize("error", [OSError, ValueError, TypeError, ModuleNotFoundError, SyntaxError])
def test_owner_authority_error_refuses_before_start_and_keeps_cleanup_truth(monkeypatch, error):
    from code_forge import _mutation_imports as imports

    def refused(*_):
        raise error("owned authority control")

    starts = []
    monkeypatch.setattr(imports, "prepare_owner", refused)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: starts.append(args))
    with pytest.raises(process.MutationProcessError, match="import authority unavailable") as failure:
        process.run_owned_command([sys.executable, "-c", "print('unreachable')"], timeout=3)
    assert failure.value.cleanup_complete is True
    assert type(failure.value.__cause__) is error
    assert starts == []


def test_missing_installed_requirement_metadata_fails_closed_before_owner(
    monkeypatch, genuine_rewritten_owner
):
    import importlib.metadata

    owner, _ = genuine_rewritten_owner
    real = importlib.metadata.distribution
    seen = []

    def distribution(name):
        seen.append(name)
        if name == "packaging":
            raise importlib.metadata.PackageNotFoundError(name)
        return real(name)

    starts = []
    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: starts.append(args))
    with pytest.raises(owner.MutationProcessError, match="import authority unavailable") as failure:
        owner.run_owned_command([sys.executable, "-c", "print('unreachable')"], timeout=3)
    assert failure.value.cleanup_complete is True
    assert isinstance(failure.value.__cause__, importlib.metadata.PackageNotFoundError)
    assert seen == ["packaging"] and starts == []


def test_parent_packaging_shadow_is_refused_before_requirement_code(tmp_path, monkeypatch):
    import types
    from code_forge import _mutation_imports as imports

    foreign = types.ModuleType("packaging.requirements")
    foreign.__file__ = str(tmp_path / "foreign_requirements.py")
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    with pytest.raises(ValueError, match="foreign parent packaging module"):
        imports._requirements_api()


def test_uncached_parent_requirement_parser_uses_only_installed_pinned_origin():
    program = """
import json,sys
from code_forge import _mutation_imports as imports
before=list(sys.path)
assert not any(name=='packaging' or name.startswith('packaging.') for name in sys.modules)
Requirement=imports._requirements_api()
item=Requirement('markdown-it-py[linkify]>=2; python_version >= "3.12"')
assert item.extras=={'linkify'} and item.marker.evaluate({'extra':''})
assert sys.path==before
print(json.dumps({name:module.__file__ for name,module in sys.modules.items()
                 if name=='packaging' or name.startswith('packaging.')}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program], timeout=3, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    origins = json.loads(result.stdout)
    assert "packaging.requirements" in origins
    from code_forge import _mutation_imports as imports

    _, rows, _ = imports._distribution("packaging")
    assert all(path == rows[name]["path"] for name, path in origins.items())


def _owned_distribution(tmp_path, monkeypatch):
    import importlib.metadata
    import types

    root = tmp_path / "installed"
    metadata = root / "owned-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: owned\nVersion: 1.0\nRequires-Dist: packaging>=22\n"
    )
    (metadata / "RECORD").write_text("owned/__init__.py,,\n\n../outside.py,,\nowned/data.txt,,\n")
    (root / "owned").mkdir()
    (root / "owned/__init__.py").write_text("value = 'owned module'\n")
    distribution = types.SimpleNamespace(_path=metadata, locate_file=lambda name: root / name)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: distribution)
    return root, metadata, distribution


def test_distribution_inventory_uses_verified_metadata_and_record_bytes(tmp_path, monkeypatch):
    from code_forge import _mutation_imports as imports

    root, metadata, _ = _owned_distribution(tmp_path, monkeypatch)
    row, modules, requirements = imports._distribution("owned")
    assert row["root"] == str(root)
    assert row["version"] == "1.0" and requirements == ["packaging>=22"]
    assert set(modules) == {"owned"}
    assert modules["owned"]["path"] == str(root / "owned/__init__.py")
    assert modules["owned"]["kind"] == "package"
    assert row["metadata"]["path"] == str(metadata / "METADATA")
    assert row["record"]["path"] == str(metadata / "RECORD")


@pytest.mark.parametrize("leaf", ["METADATA", "RECORD"])
def test_nonregular_distribution_metadata_refuses_without_waiting(tmp_path, monkeypatch, leaf):
    from code_forge import _mutation_imports as imports

    _, metadata, _ = _owned_distribution(tmp_path, monkeypatch)
    path = metadata / leaf
    path.unlink()
    os.mkfifo(path)
    original = signal.getsignal(signal.SIGALRM)

    def expired(*_):
        raise TimeoutError("owned metadata FIFO blocked")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 0.25)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            imports._distribution("owned")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original)


@pytest.mark.parametrize(
    "damage", ["nonphysical", "metadata-origin", "name", "version", "module-origin"]
)
def test_distribution_authority_refuses_wrong_namespace_and_identity(tmp_path, monkeypatch, damage):
    from code_forge import _mutation_imports as imports

    root, metadata, distribution = _owned_distribution(tmp_path, monkeypatch)
    if damage == "nonphysical":
        distribution._path = str(metadata)
        expected = "physical metadata"
    elif damage == "metadata-origin":
        distribution.locate_file = lambda name: tmp_path / "foreign-root" / name
        expected = "metadata has a foreign origin"
    elif damage in ("name", "version"):
        (metadata / "METADATA").write_text(
            "Name: foreign\nVersion: 1.0\n" if damage == "name" else "Name: owned\n"
        )
        expected = "name/version mismatch"
    else:
        distribution.locate_file = lambda name: root if not name else tmp_path / "foreign-module.py"
        expected = "dependency has a foreign origin"
    with pytest.raises(TypeError if damage == "nonphysical" else ValueError, match=expected):
        imports._distribution("owned")


@pytest.mark.parametrize("change", ["in-place", "parent-replace"])
def test_pin_read_retains_descriptor_and_named_origin_identity(tmp_path, monkeypatch, change):
    import errno
    from code_forge import _mutation_imports as imports

    directory = tmp_path / "origin"
    directory.mkdir()
    source = directory / "selected.py"
    source.write_text("value = 1\n")
    original = os.read
    seen = []

    def changed(fd, count):
        block = original(fd, count)
        if block and not seen:
            seen.append(fd)
            if change == "in-place":
                with source.open("ab") as stream:
                    stream.write(b"\n")
            else:
                directory.rename(tmp_path / "retained-origin")
                directory.mkdir()
                source.write_text("value = 1\n")
        return block

    monkeypatch.setattr(os, "read", changed)
    with pytest.raises(ValueError, match="changed while reading"):
        imports._pin_file(source)
    assert len(seen) == 1
    with pytest.raises(OSError) as failure:
        os.fstat(seen[0])
    assert failure.value.errno == errno.EBADF


def test_pin_read_refuses_symlink_leaf_and_preserves_target(tmp_path):
    import errno
    from code_forge import _mutation_imports as imports

    foreign = tmp_path / "foreign.py"
    foreign.write_bytes(b"value = 'preserved'\n")
    before = foreign.stat()
    link = tmp_path / "named.py"
    link.symlink_to(foreign)
    with pytest.raises(OSError) as failure:
        imports._pin_file(link)
    assert failure.value.errno == errno.ELOOP
    assert foreign.read_bytes() == b"value = 'preserved'\n"
    assert (foreign.stat().st_dev, foreign.stat().st_ino) == (before.st_dev, before.st_ino)


def test_optional_rewrite_fixture_reports_declared_unavailability(tmp_path, monkeypatch):
    import importlib.metadata

    def absent(name):
        assert name == "mutmut"
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", absent)
    with pytest.raises(
        pytest.skip.Exception, match="optional installed mutmut pure rewrite API unavailable"
    ):
        genuine_rewritten_owner.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("purpose", ["metadata-locator", "foreign-stdlib"])
def test_owner_loader_retains_named_metadata_and_standard_library_authority(
    tmp_path, monkeypatch, purpose
):
    from code_forge import _mutation_imports as imports

    marker = tmp_path / "foreign-execution"
    if purpose == "metadata-locator":
        suffix = """
import mutmut
from importlib.metadata import distribution, distributions
if distribution('mutmut').locate_file('mutmut') != Path(mutmut.__file__).parent:
    raise ValueError('named metadata locator drift')
list(distributions())
"""
    else:
        foreign = tmp_path / "foreign-email"
        foreign.mkdir()
        (foreign / "_owned_foreign.py").write_text(
            f"from pathlib import Path;Path({str(marker)!r}).write_text('foreign stdlib execution')\n"
        )
        suffix = (
            f"\nimport email\nemail.__path__.insert(0, {str(foreign)!r})\nimport email._owned_foreign\n"
        )
    selected = tmp_path / "loader-control-owner.py"
    selected.write_text(Path(process.__file__).read_text() + suffix)
    real = imports.prepare_owner
    monkeypatch.setattr(
        imports, "prepare_owner", lambda *_: real(str(selected), "code_forge._owned_loader_control")
    )
    if purpose == "metadata-locator":
        result = process.run_owned_command(
            [sys.executable, "-I", "-S", "-c", "print('named metadata')"],
            timeout=3,
            cwd=str(tmp_path),
            text=True,
        )
        assert result.stdout == "named metadata\n" and result.ownership["cleanup_complete"]
    else:
        with pytest.raises(
            process.MutationProcessError, match="foreign standard-library origin"
        ) as failure:
            process.run_owned_command(
                [sys.executable, "-I", "-S", "-c", "print('unreachable')"], timeout=3, cwd=str(tmp_path)
            )
        assert failure.value.cleanup_complete is False
    assert not marker.exists()


def _wait_json(path, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
    raise AssertionError("owned process did not write its identity")


def _gone(pid, start_ticks, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        identity = process._identity(pid)
        if identity is None or identity.start_ticks != start_ticks:
            return True
        if identity.parent == os.getpid() and identity.state == "Z":
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
        time.sleep(0.01)
    return False


def _rescue_recorded_identity(pid, start_ticks, signum=signal.SIGKILL):
    current = process._identity(pid)
    if current is None or current.start_ticks != start_ticks:
        return
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        current = process._identity(pid)
        if current is not None and current.start_ticks == start_ticks:
            signal.pidfd_send_signal(fd, signum)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)
    _gone(pid, start_ticks, timeout=0.5)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_owned_result_preserves_streams_status_and_reaps(exit_code):
    result = process.run_owned_command(
        [
            sys.executable,
            "-c",
            f"import sys;print('native stdout');print('native stderr',file=sys.stderr);sys.exit({exit_code})",
        ],
        timeout=3,
        text=True,
    )
    assert result.returncode == exit_code
    assert result.stdout == "native stdout\n"
    assert result.stderr == "native stderr\n"
    assert result.ownership["cleanup_complete"]
    assert all(not record["remaining"] for record in result.ownership["owned"])


@pytest.mark.parametrize("finish", ["normal", "timeout"])
def test_double_fork_new_session_is_owned_and_unrelated_child_is_untouched(tmp_path, finish):
    marker = tmp_path / "escaped.json"
    script = f"""
import json, os, time
from pathlib import Path
if os.fork() == 0:
    os.setsid()
    if os.fork():
        os._exit(0)
    raw = Path('/proc/self/stat').read_text()
    ticks = int(raw[raw.rfind(')') + 2:].split()[19])
    Path({str(marker)!r}).write_text(json.dumps({{'pid': os.getpid(), 'start_ticks': ticks}}))
    time.sleep(30)
else:
    while not Path({str(marker)!r}).exists():
        time.sleep(.005)
    {"time.sleep(30)" if finish == "timeout" else "pass"}
"""
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    before = process._identity(unrelated.pid)
    try:
        started = time.monotonic()
        if finish == "timeout":
            with pytest.raises(subprocess.TimeoutExpired):
                process.run_owned_command([sys.executable, "-c", script], timeout=0.3)
        else:
            result = process.run_owned_command([sys.executable, "-c", script], timeout=3)
            assert result.returncode == 0 and result.ownership["cleanup_complete"]
        escaped = _wait_json(marker)
        assert _gone(escaped["pid"], escaped["start_ticks"])
        assert time.monotonic() - started < 3
        assert process._identity(unrelated.pid).start_ticks == before.start_ticks
        assert unrelated.poll() is None
        assert os.waitpid(unrelated.pid, os.WNOHANG) == (0, 0)
    finally:
        if marker.exists():
            escaped = json.loads(marker.read_text())
            _rescue_recorded_identity(escaped["pid"], escaped["start_ticks"])
        unrelated.terminate()
        unrelated.wait(timeout=3)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGKILL])
def test_parent_signal_cancellation_drains_private_owner(tmp_path, signum):
    marker = tmp_path / "child.json"
    payload = f"""
from pathlib import Path
import json, os, time
raw=Path('/proc/self/stat').read_text()
owner=Path('/proc/'+str(os.getppid())+'/stat').read_text()
Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'start_ticks':int(raw[raw.rfind(')')+2:].split()[19]),'owner':os.getppid(),'owner_ticks':int(owner[owner.rfind(')')+2:].split()[19])}}))
time.sleep(30)
"""
    caller = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from code_forge._mutation_process import run_owned_command;run_owned_command([{sys.executable!r},'-c',{payload!r}],timeout=30)",
        ]
    )
    try:
        child = _wait_json(marker)
        caller.send_signal(signum)
        assert caller.wait(timeout=3) != 0
        assert _gone(child["pid"], child["start_ticks"])
        assert _gone(child["owner"], child["owner_ticks"])
    finally:
        if caller.poll() is None:
            caller.terminate()
            caller.wait(timeout=3)
        if marker.exists():
            child = json.loads(marker.read_text())
            _rescue_recorded_identity(child["owner"], child["owner_ticks"], signal.SIGTERM)
            if not _gone(child["pid"], child["start_ticks"], timeout=0.5):
                _rescue_recorded_identity(child["pid"], child["start_ticks"])
            if not _gone(child["owner"], child["owner_ticks"], timeout=0.5):
                _rescue_recorded_identity(child["owner"], child["owner_ticks"])


def test_pid_reuse_is_never_signaled(monkeypatch):
    tree = process._OwnedTree()
    tree.identities = {999: process._Identity(999, tree.owner.pid, 100, "S")}
    tree.fds = {999: 10}
    monkeypatch.setattr(process, "_identity", lambda pid: process._Identity(pid, 1, 101, "S"))
    sent = []
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *args: sent.append(args))
    tree.signal(999, signal.SIGKILL)
    assert sent == []


def test_reaping_is_bound_to_owned_adopted_pidfd(monkeypatch):
    tree = process._OwnedTree()
    tree.identities = {999: process._Identity(999, tree.owner.pid, 100, "Z")}
    tree.fds = {999: 10}
    monkeypatch.setattr(process, "_identity", lambda pid: tree.identities[pid])
    waits = []
    monkeypatch.setattr(os, "waitid", lambda *args: waits.append(args))
    driver = type("Driver", (), {"pid": 111, "poll": lambda self: 0})()
    tree.reap(driver)
    assert waits == [(os.P_PIDFD, 10, os.WEXITED | os.WNOHANG)]


def test_missing_binary_keeps_file_not_found_semantics():
    with pytest.raises(FileNotFoundError):
        process.run_owned_command(["/definitely/missing/runner"], timeout=3)


def test_resource_limit_reaches_actual_command():
    result = process.run_owned_command(
        [sys.executable, "-c", "import resource;print(resource.getrlimit(resource.RLIMIT_AS)[0])"],
        timeout=3,
        text=True,
        memory_limit_bytes=1024**3,
    )
    assert result.stdout.strip() == str(1024**3)


def test_ignored_term_escalates_to_kill_and_returns_timeout_evidence(tmp_path):
    marker = tmp_path / "stubborn.json"
    payload = f"""
import json, os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
raw=Path('/proc/self/stat').read_text()
Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'start_ticks':int(raw[raw.rfind(')')+2:].split()[19])}}))
time.sleep(30)
"""
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired) as captured:
            process.run_owned_command([sys.executable, "-c", payload], timeout=0.3)
        child = _wait_json(marker)
        assert _gone(child["pid"], child["start_ticks"])
        assert captured.value.ownership["cleanup_complete"]
        assert captured.value.ownership["timed_out"]
        assert time.monotonic() - started < 2
    finally:
        if marker.exists():
            child = json.loads(marker.read_text())
            _rescue_recorded_identity(child["pid"], child["start_ticks"])


@pytest.mark.parametrize("changed", ["identity", "parent", "vanished"])
def test_pidfd_binding_rechecks_observed_child_identity(monkeypatch, changed):
    tree = process._OwnedTree()
    original = process._Identity(999, tree.owner.pid, 100, "S")
    current = (
        None
        if changed == "vanished"
        else process._Identity(
            999,
            888 if changed == "parent" else tree.owner.pid,
            101 if changed == "identity" else 100,
            "S",
        )
    )
    reads = iter([original, current])
    monkeypatch.setattr(process, "_children", lambda item: [999] if item == tree.owner else [])
    monkeypatch.setattr(process, "_identity", lambda pid: next(reads))
    monkeypatch.setattr(os, "pidfd_open", lambda pid: 123)
    closed = []
    monkeypatch.setattr(os, "close", closed.append)
    tree.discover()
    assert tree.identities == {} and tree.fds == {} and closed == [123]


def test_finished_pidfd_parent_is_not_traversed(monkeypatch):
    tree = process._OwnedTree()
    child = process._Identity(999, tree.owner.pid, 100, "S")
    tree.identities[999] = child
    tree.fds[999] = 123
    seen = []
    monkeypatch.setattr(
        process,
        "_children",
        lambda item: seen.append(item.pid) or ([999] if item == tree.owner else [888]),
    )
    monkeypatch.setattr(process, "_identity", lambda pid: child)
    monkeypatch.setattr(process, "_pidfd_ready", lambda _: True)
    tree.discover()
    assert seen == [tree.owner.pid] and 888 not in tree.identities


def test_cleanup_retry_shares_one_bounded_deadline(monkeypatch):
    tree = process._OwnedTree()
    ticks = iter([0, 0, 4, 6, 7, 8])
    monkeypatch.setattr(process.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(process.time, "sleep", lambda _: None)
    monkeypatch.setattr(tree, "discover", lambda: None)
    monkeypatch.setattr(tree, "reap", lambda driver: None)
    monkeypatch.setattr(tree, "live", lambda: [])
    monkeypatch.setattr(process, "_children", lambda _: [999])
    driver = type("Driver", (), {"returncode": None})()
    assert not tree.cleanup(driver)
    assert tree.cleanup_deadline == 5
    assert not tree.cleanup(driver)
    assert tree.cleanup_deadline == 5


@pytest.mark.parametrize(
    "case",
    [
        "unsupported",
        "prctl",
        "wrong-parent",
        "enumeration",
        "cap",
        "launch",
        "discover",
        "incomplete",
        "cleanup-exception",
        "cleanup-retry",
        "signal",
    ],
)
def test_supervisor_faults_never_invent_clean_completion(monkeypatch, case):
    from types import SimpleNamespace

    events = []
    request = {"argv": ["fixture"], "timeout": 1, "caller_pid": os.getppid()}
    fake_tree = SimpleNamespace(
        owner=process._Identity(999, os.getppid(), 100, "S"),
        verify_enumeration=lambda: None,
        discover=lambda: events.append("discover"),
        reap=lambda driver: events.append("reap"),
        cleanup=lambda driver: events.append("cleanup") or case != "incomplete",
        report=lambda: [{"pid": 111, "start_ticks": 200, "remaining": case == "incomplete"}],
        close=lambda: events.append("close"),
    )
    driver = SimpleNamespace(pid=111, returncode=0, poll=lambda: 0)
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda signum, handler: handlers.update({signum: handler}))
    monkeypatch.setattr(
        process.ctypes,
        "CDLL",
        lambda *_a, **_k: SimpleNamespace(prctl=lambda *_: -1 if case == "prctl" else 0),
    )
    monkeypatch.setattr(process, "_OwnedTree", lambda: fake_tree)

    def launch(*_a, **_k):
        events.append("launch")
        if case == "launch":
            raise FileNotFoundError("KNOWN_MISSING_RUNNER")
        return driver

    monkeypatch.setattr(process.subprocess, "Popen", launch)
    if case == "unsupported":
        monkeypatch.setattr(process.sys, "platform", "unsupported")
    elif case == "wrong-parent":
        request["caller_pid"] = 1 << 30
    elif case == "enumeration":
        fake_tree.verify_enumeration = lambda: (_ for _ in ()).throw(
            RuntimeError("KNOWN_ENUMERATION_FAILURE")
        )
    elif case == "cap":
        request["memory_limit_bytes"] = 1
        monkeypatch.setattr(
            process, "limit_address_space", lambda _: (_ for _ in ()).throw(OSError("KNOWN_CAP_FAILURE"))
        )
    elif case == "discover":
        driver.poll = lambda: None
        fake_tree.discover = lambda: (_ for _ in ()).throw(RuntimeError("KNOWN_DISCOVERY_FAILURE"))
    elif case == "cleanup-exception":
        fake_tree.cleanup = lambda _: (_ for _ in ()).throw(RuntimeError("KNOWN_CLEANUP_FAILURE"))
    elif case == "cleanup-retry":
        attempts = []

        def cleanup(_):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("KNOWN_FIRST_CLEANUP_FAILURE")
            return True

        fake_tree.cleanup = cleanup
    elif case == "signal":
        driver.poll = lambda: None
        fake_tree.discover = lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)
    report = process._supervise(request)
    if case == "signal":
        assert report["cancelled"] and not report["timed_out"] and report["cleanup_complete"]
    elif case == "incomplete":
        assert not report["cleanup_complete"] and report["owned"][0]["remaining"]
    else:
        assert report["error"]
    if case == "cleanup-exception":
        assert not report["cleanup_complete"] and "cleanup: KNOWN_CLEANUP_FAILURE" in report["error"]
    elif case != "incomplete":
        assert report["cleanup_complete"]
    if case in ("unsupported", "prctl", "wrong-parent", "enumeration", "cap"):
        assert "launch" not in events


@pytest.mark.parametrize(
    "case",
    [
        "invalid",
        "incomplete",
        "cancelled",
        "error",
        "missing",
        "timeout",
        "cancel-wait",
        "owner-stuck",
        "gone-owner",
        "invalid-shape",
    ],
)
def test_owner_report_and_wrapper_cancellation_are_fail_closed(monkeypatch, case):
    report = {"cleanup_complete": True, "returncode": 0, "stdout": "", "stderr": ""}
    if case == "incomplete":
        report.update(cleanup_complete=False, owned=[{"pid": 11, "start_ticks": 12, "remaining": True}])
    elif case == "cancelled":
        report["cancelled"] = True
    elif case in ("missing", "error"):
        report.update(
            error="KNOWN_OWNER_ERROR",
            error_kind="FileNotFoundError" if case == "missing" else "RuntimeError",
        )
    elif case == "timeout":
        report["timed_out"] = True
    elif case == "invalid-shape":
        report = []
    calls = []

    class Owner:
        pid = 123

        def communicate(self, *args, **kwargs):
            calls.append((args, kwargs))
            if case in ("cancel-wait", "owner-stuck", "gone-owner") and len(calls) == 1:
                raise KeyboardInterrupt()
            if case == "owner-stuck":
                raise subprocess.TimeoutExpired("owner", 7)
            return (
                b"invalid" if case == "invalid" else json.dumps(report).encode(),
                b"KNOWN_DIAGNOSTIC",
            )

        def send_signal(self, signum):
            assert signum == signal.SIGTERM
            if case == "gone-owner":
                raise ProcessLookupError()

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    expected = (
        FileNotFoundError
        if case == "missing"
        else subprocess.TimeoutExpired
        if case == "timeout"
        else KeyboardInterrupt
        if case in ("cancelled", "cancel-wait", "owner-stuck", "gone-owner")
        else process.MutationProcessError
    )
    with pytest.raises(expected) as captured:
        process.run_owned_command(["fixture"], timeout=1)
    if isinstance(captured.value, process.MutationProcessError):
        assert captured.value.cleanup_complete is (
            case not in ("invalid", "invalid-shape", "incomplete", "owner-stuck")
        )
    if case == "incomplete":
        assert "11:12" in str(captured.value)
    if isinstance(captured.value, KeyboardInterrupt):
        assert captured.value.cleanup_complete is (case != "owner-stuck")
        if case == "owner-stuck":
            assert isinstance(captured.value.cleanup_error, process.MutationProcessError)
    if len(calls) == 2:
        assert calls[1][1]["timeout"] == process._CLEANUP_SECONDS + 2


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("secondary_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("transport", [False, True])
@pytest.mark.parametrize("site", ["note-format", "metadata-call", "note-call", "note-lookup",
                                  "metadata-attribute", "secondary-report"])
def test_owner_diagnostic_interruption_keeps_first_cancellation(monkeypatch, exception, secondary_type,
                                                              transport, site):
    secondary = secondary_type("SECOND_CANCEL")
    fired = False

    def interrupt():
        nonlocal fired
        if not fired:
            fired = True
            raise secondary

    class Cancellation(exception):
        def __getattribute__(self, key):
            if site == "metadata-attribute" and key == "__dict__":
                interrupt()
            return super().__getattribute__(key)

    cancellation = Cancellation("FIRST_CANCEL")

    class Failure(RuntimeError if transport else PermissionError):
        def __str__(self):
            if site in ("note-format", "secondary-report"):
                interrupt()
            return "TRANSPORT_FAILED" if transport else "CLEANUP_FAILED"

    failure = Failure()
    calls = []

    class Owner:
        pid = 123

        def communicate(self, *_args, **_kwargs):
            calls.append("communicate")
            raise failure if transport and len(calls) == 1 else cancellation

        def send_signal(self, _signum):
            calls.append("signal")
            if not transport:
                raise failure

    binder = process._bind_cancellation_evidence

    def bind(error, **kwargs):
        if site == "metadata-call" and "cleanup_complete" in kwargs and "cleanup_evidence_error" not in kwargs:
            interrupt()
        if site == "note-call" and "note" in kwargs:
            interrupt()
        if site == "secondary-report" and "cleanup_evidence_error" in kwargs:
            raise OSError("SECONDARY_DIAGNOSTIC_FAILED")
        return binder(error, **kwargs)

    target = None
    if site == "note-lookup":
        lines, start = inspect.getsourcelines(process.run_owned_command)
        target = start + next(i for i, line in enumerate(lines)
                              if '_bind_cancellation_evidence(cancellation, note=' in line)

    def trace(frame, event, _arg):
        if (site == "note-lookup" and event == "line" and
                frame.f_code is process.run_owned_command.__code__ and frame.f_lineno == target):
            interrupt()
        return trace

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    monkeypatch.setattr(process, "_bind_cancellation_evidence", bind)
    previous_trace = sys.gettrace()
    if site == "note-lookup":
        sys.settrace(trace)
    try:
        with pytest.raises(Cancellation) as caught:
            process.run_owned_command(["fixture"], timeout=1)
    finally:
        sys.settrace(previous_trace)
    assert caught.value is cancellation and fired
    assert cancellation.cleanup_complete is False and cancellation.ownership == {}
    assert cancellation.cleanup_error is failure and cancellation.__cause__ is failure
    if site != "secondary-report":
        assert cancellation.cleanup_evidence_error is secondary
    assert calls == (["communicate", "signal", "communicate"] if transport else ["communicate", "signal"])


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("secondary_type", [KeyboardInterrupt, SystemExit])
def test_owner_success_proof_call_interruption_preserves_first(monkeypatch, exception, secondary_type):
    cancellation = exception("FIRST_CANCEL")
    secondary = secondary_type("SECOND_CANCEL_AT_PROOF")
    report = {"cleanup_complete": True, "cancelled": True}

    class Owner:
        pid = 123
        calls = 0

        def communicate(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise cancellation
            return json.dumps(report).encode(), b""

        def send_signal(self, _signum):
            pass

    binder = process._bind_cancellation_evidence

    def bind(error, **kwargs):
        if kwargs.get("cleanup_complete") is True:
            raise secondary
        return binder(error, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    monkeypatch.setattr(process, "_bind_cancellation_evidence", bind)
    with pytest.raises(exception) as caught:
        process.run_owned_command(["fixture"], timeout=1)
    assert caught.value is cancellation
    assert cancellation.cleanup_complete is False and cancellation.ownership == report
    assert cancellation.cleanup_error is secondary and cancellation.__cause__ is secondary


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("secondary_type", [KeyboardInterrupt, SystemExit])
def test_owner_first_report_cancellation_survives_secondary_report_failure(monkeypatch, exception, secondary_type):
    cancellation = exception("FIRST_CANCEL_AT_REPORT")
    secondary = secondary_type("SECOND_CANCEL_AT_BINDING")
    loads = process.json.loads
    binder = process._bind_cancellation_evidence

    class Owner:
        pid = 123

        def communicate(self, *_args, **_kwargs):
            return b"OWNER_REPORT_SENTINEL", b""

    def read_report(raw, *args, **kwargs):
        if raw == b"OWNER_REPORT_SENTINEL":
            raise cancellation
        return loads(raw, *args, **kwargs)

    def bind(error, **kwargs):
        if "cleanup_evidence_error" in kwargs:
            raise OSError("SECONDARY_REPORT_FAILED")
        binder(error, **kwargs)
        raise secondary

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    monkeypatch.setattr(process.json, "loads", read_report)
    monkeypatch.setattr(process, "_bind_cancellation_evidence", bind)
    with pytest.raises(exception) as caught:
        process.run_owned_command(["fixture"], timeout=1)
    assert caught.value is cancellation and cancellation.__cause__ is None
    assert cancellation.cleanup_complete is False and cancellation.ownership == {}


@pytest.mark.parametrize(
    "kwargs",
    [{"argv": []}, {"argv": ["fixture"], "check": True}, {"argv": ["fixture"], "capture_output": False}],
)
def test_owned_executor_refuses_unsupported_subprocess_modes(kwargs):
    with pytest.raises(ValueError):
        process.run_owned_command(timeout=1, **kwargs)


def test_inherited_address_cap_cannot_be_widened(monkeypatch):
    import resource

    monkeypatch.setattr(resource, "getrlimit", lambda _: (200, 300))
    limits = []
    monkeypatch.setattr(resource, "setrlimit", lambda *args: limits.append(args))
    process.limit_address_space(500)
    assert limits == [(resource.RLIMIT_AS, (300, 300))]


@pytest.mark.parametrize("case", ["changed-before", "changed-after", "task-gone", "thread-gone"])
def test_child_discovery_refuses_unstable_parent_and_tolerates_exit(monkeypatch, case):
    from pathlib import Path

    parent = process._Identity(999, 1, 100, "S")
    reads = iter(
        [None] if case == "changed-before" else [parent, None if case == "changed-after" else parent]
    )
    monkeypatch.setattr(process, "_identity", lambda _: next(reads))

    def tasks(_):
        if case == "task-gone":
            raise FileNotFoundError()
        return iter([Path("/proc/999/task/1000")])

    monkeypatch.setattr(process.Path, "iterdir", tasks)

    def read(_):
        if case == "thread-gone":
            raise FileNotFoundError()
        return b"111"

    monkeypatch.setattr(process.Path, "read_bytes", read)
    assert process._children(parent) == []


@pytest.mark.parametrize("error_type", [FileNotFoundError, ProcessLookupError])
def test_children_read_disappearance_retains_other_tasks(monkeypatch, error_type):
    parent = process._Identity(999, 1, 100, "S")
    monkeypatch.setattr(process, "_identity", lambda _: parent)
    tasks = [Path("/proc/999/task/1000"), Path("/proc/999/task/1001")]
    monkeypatch.setattr(Path, "iterdir", lambda _: iter(tasks))
    reads = []

    def read(path):
        reads.append(path)
        if path.parent == tasks[0]:
            raise error_type(3, "task disappeared", str(path))
        return b"222 111 222"

    monkeypatch.setattr(Path, "read_bytes", read)
    assert process._children(parent) == [111, 222]
    assert reads == [task / "children" for task in tasks]


@pytest.mark.parametrize("error_type", [FileNotFoundError, ProcessLookupError])
@pytest.mark.parametrize("lazy", [False, True])
def test_task_iteration_disappearance_discards_partial_children(monkeypatch, error_type, lazy):
    parent = process._Identity(999, 1, 100, "S")
    monkeypatch.setattr(process, "_identity", lambda _: parent)
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"111")

    def vanished():
        yield Path("/proc/999/task/1000")
        raise error_type(3, "task disappeared")

    def tasks(_):
        if not lazy:
            raise error_type(3, "task disappeared")
        return vanished()

    monkeypatch.setattr(Path, "iterdir", tasks)
    assert process._children(parent) == []


@pytest.mark.parametrize("site", ["read", "iteration"])
@pytest.mark.parametrize("error", [PermissionError(13, "denied"), OSError(5, "I/O error")])
def test_children_unexpected_filesystem_error_propagates(monkeypatch, site, error):
    parent = process._Identity(999, 1, 100, "S")
    monkeypatch.setattr(process, "_identity", lambda _: parent)
    monkeypatch.setattr(Path, "iterdir", lambda _: iter([Path("/proc/999/task/1000")]))
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"111")

    def fail(_):
        raise error

    monkeypatch.setattr(Path, "read_bytes" if site == "read" else "iterdir", fail)
    with pytest.raises(type(error)) as caught:
        process._children(parent)
    assert caught.value is error


@pytest.mark.parametrize("changed_at", ["before", "after"])
def test_children_reused_parent_start_ticks_rejects_children(monkeypatch, changed_at):
    parent = process._Identity(999, 1, 100, "S")
    reused = process._Identity(999, 1, 101, "S")
    identities = iter([reused] if changed_at == "before" else [parent, reused])
    monkeypatch.setattr(process, "_identity", lambda _: next(identities))
    monkeypatch.setattr(Path, "iterdir", lambda _: iter([Path("/proc/999/task/1000")]))
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"111")
    assert process._children(parent) == []


def test_owner_identity_is_required_before_launch(monkeypatch):
    monkeypatch.setattr(process, "_identity", lambda _: None)
    with pytest.raises(RuntimeError, match="cannot identify"):
        process._OwnedTree()


@pytest.mark.parametrize("case", ["missing", "unrelated", "pidfd-gone", "changed", "duplicate"])
def test_discovery_errors_never_adopt_an_unverified_child(monkeypatch, case):
    tree = process._OwnedTree()
    child = process._Identity(999, 888 if case == "unrelated" else tree.owner.pid, 100, "S")
    if case == "changed":
        tree.identities[999] = process._Identity(999, tree.owner.pid, 99, "S")
        tree.fds[999] = 123
    monkeypatch.setattr(
        process,
        "_children",
        lambda item: ([999, 999] if case == "duplicate" else [999]) if item == tree.owner else [],
    )
    monkeypatch.setattr(process, "_identity", lambda _: None if case == "missing" else child)
    monkeypatch.setattr(process, "_pidfd_ready", lambda _: False)

    def open_fd(_):
        if case == "pidfd-gone":
            raise ProcessLookupError()
        return 123

    monkeypatch.setattr(os, "pidfd_open", open_fd)
    if case == "changed":
        with pytest.raises(RuntimeError, match="identity changed"):
            tree.discover()
    else:
        tree.discover()
        assert set(tree.identities) == ({999} if case == "duplicate" else set())


def test_finished_adopted_child_is_not_reaped_again(monkeypatch):
    tree = process._OwnedTree()
    child = process._Identity(999, tree.owner.pid, 100, "Z")
    tree.identities[999] = child
    tree.fds[999] = 123
    monkeypatch.setattr(process, "_identity", lambda _: child)
    monkeypatch.setattr(os, "waitid", lambda *_: (_ for _ in ()).throw(ChildProcessError()))
    tree.reap(type("Driver", (), {"pid": 111, "poll": lambda self: 0})())
    assert tree.reaped == {}


def test_finished_identity_is_not_signaled_again(monkeypatch):
    tree = process._OwnedTree()
    child = process._Identity(999, tree.owner.pid, 100, "S")
    tree.identities[999] = child
    tree.fds[999] = 123
    monkeypatch.setattr(process, "_identity", lambda _: child)
    monkeypatch.setattr(
        signal, "pidfd_send_signal", lambda *_: (_ for _ in ()).throw(ProcessLookupError())
    )
    tree.signal(999, signal.SIGTERM)


def _proc_child():
    program = "import json,os,sys;from pathlib import Path;s=Path('/proc/self/stat').read_text();print(json.dumps({'pid':os.getpid(),'start_ticks':int(s[s.rfind(')')+2:].split()[19])}),flush=True);sys.stdin.read(1)"
    proc = subprocess.Popen(
        [sys.executable, "-c", program], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    data = json.loads(proc.stdout.readline())
    identity = process._identity(proc.pid)
    assert identity.start_ticks == data["start_ticks"]
    fd = os.pidfd_open(proc.pid)
    return proc, identity, fd


def _close_proc_child(proc, fd):
    if proc.poll() is None:
        proc.stdin.write("x")
        proc.stdin.flush()
    proc.wait(timeout=2)
    proc.stdin.close()
    proc.stdout.close()
    if fd is not None:
        os.close(fd)


def test_actual_stat_open_exit_read_is_a_disappeared_identity(monkeypatch):
    proc, identity, fd = _proc_child()
    target = f"/proc/{proc.pid}/stat"
    original = io.open
    opened = []

    def open_then_exit(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        if os.fspath(path) == target:
            opened.append(handle.fileno())
            proc.stdin.write("x")
            proc.stdin.flush()
            proc.wait(timeout=2)
            assert select.select([fd], [], [], 0)[0] == [fd]
        return handle

    try:
        monkeypatch.setattr(io, "open", open_then_exit)
        assert process._identity(identity.pid) is None
        assert opened
    finally:
        _close_proc_child(proc, fd)


def test_missing_stat_unreadable_bound_pidfd_remains_live(monkeypatch):
    proc, identity, fd = _proc_child()
    tree = process._OwnedTree()
    tree.identities[proc.pid] = identity
    tree.fds[proc.pid] = fd
    original = process._identity
    monkeypatch.setattr(process, "_identity", lambda pid: None if pid == proc.pid else original(pid))
    try:
        assert select.select([fd], [], [], 0)[0] == []
        assert tree.live() == [proc.pid]
    finally:
        _close_proc_child(proc, fd)


def test_absent_stat_ready_owned_pidfd_is_reaped_exactly(monkeypatch):
    proc, identity, fd = _proc_child()
    tree = process._OwnedTree()
    tree.identities[proc.pid] = identity
    tree.fds[proc.pid] = fd
    proc.stdin.write("x")
    proc.stdin.flush()
    deadline = time.monotonic() + 2
    while not select.select([fd], [], [], 0)[0] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert select.select([fd], [], [], 0)[0] == [fd]
    original = process._identity
    assert original(proc.pid).state == "Z"
    monkeypatch.setattr(process, "_identity", lambda pid: None if pid == proc.pid else original(pid))
    try:
        assert tree.live() == []
        driver = type("Driver", (), {"pid": -1, "poll": lambda self: 0})()
        tree.reap(driver)
        assert tree.reaped == {proc.pid: 0}
        assert original(proc.pid) is None
    finally:
        _close_proc_child(proc, tree.fds.get(proc.pid))


def test_stat_permission_failure_stays_infrastructure(monkeypatch):
    monkeypatch.setattr(
        Path, "read_bytes", lambda *_: (_ for _ in ()).throw(PermissionError(13, "denied"))
    )
    with pytest.raises(PermissionError):
        process._identity(os.getpid())


def test_unreadable_owned_pidfd_is_not_waited_without_stat(monkeypatch):
    proc, identity, fd = _proc_child()
    tree = process._OwnedTree()
    tree.identities[proc.pid] = identity
    tree.fds[proc.pid] = fd
    original = process._identity
    monkeypatch.setattr(process, "_identity", lambda pid: None if pid == proc.pid else original(pid))
    waitid = os.waitid
    waited = []

    def recorded_wait(*args):
        waited.append(args)
        return waitid(*args)

    monkeypatch.setattr(os, "waitid", recorded_wait)
    try:
        assert select.select([fd], [], [], 0)[0] == []
        tree.reap(type("Driver", (), {"pid": -1, "poll": lambda self: 0})())
        assert waited == [] and tree.reaped == {}
        assert original(proc.pid).start_ticks == identity.start_ticks
    finally:
        _close_proc_child(proc, fd)


def test_missing_stat_with_live_pidfd_cannot_complete_cleanup(monkeypatch):
    proc, identity, fd = _proc_child()
    tree = process._OwnedTree()
    tree.identities[proc.pid] = identity
    tree.fds[proc.pid] = fd
    original = process._identity
    monkeypatch.setattr(process, "_identity", lambda pid: None if pid == proc.pid else original(pid))
    monkeypatch.setattr(process, "_children", lambda _: [])
    monkeypatch.setattr(tree, "discover", lambda: None)
    tree.cleanup_deadline = time.monotonic() + 0.06
    try:
        driver = type("Driver", (), {"pid": -1, "returncode": 0, "poll": lambda self: 0})()
        assert not tree.cleanup(driver)
        assert original(proc.pid).start_ticks == identity.start_ticks
        assert select.select([fd], [], [], 0)[0] == []
    finally:
        _close_proc_child(proc, fd)


def test_named_identity_contradiction_is_not_waited(monkeypatch):
    proc, identity, fd = _proc_child()
    tree = process._OwnedTree()
    tree.identities[proc.pid] = identity
    tree.fds[proc.pid] = fd
    original = process._identity

    def changed_stat(pid):
        return (
            process._Identity(pid, identity.parent, identity.start_ticks + 1, "S")
            if pid == proc.pid
            else original(pid)
        )

    monkeypatch.setattr(process, "_identity", changed_stat)
    waitid = os.waitid
    waited = []

    def recorded_wait(*args):
        waited.append(args)
        return waitid(*args)

    monkeypatch.setattr(os, "waitid", recorded_wait)
    try:
        tree.reap(type("Driver", (), {"pid": -1, "poll": lambda self: 0})())
        assert waited == [] and tree.reaped == {}
        assert original(proc.pid).start_ticks == identity.start_ticks
    finally:
        _close_proc_child(proc, fd)


@pytest.mark.parametrize("site", ["discover", "live", "reap", "cleanup"])
def test_high_descriptor_pidfd_keeps_owned_lifecycle_checks(tmp_path, monkeypatch, site):
    import resource

    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    soft, hard = limits
    if hard != resource.RLIM_INFINITY and hard <= 1024:
        pytest.skip("RLIMIT_NOFILE hard cap cannot represent descriptor 1024")
    tree = process._OwnedTree()
    proc, identity, original_fd = _proc_child()
    tree.identities[proc.pid] = identity
    tree.fds[proc.pid] = original_fd
    original = process._identity
    try:
        try:
            if soft != resource.RLIM_INFINITY and soft <= 1024:
                resource.setrlimit(resource.RLIMIT_NOFILE, (1025, hard))
            fd = fcntl.fcntl(original_fd, fcntl.F_DUPFD_CLOEXEC, 1024)
            tree.fds[proc.pid] = fd
            os.close(original_fd)
            original_fd = None
        finally:
            if resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
                resource.setrlimit(resource.RLIMIT_NOFILE, limits)
        assert fd >= 1024
        if site in ("live", "reap"):
            monkeypatch.setattr(
                process, "_identity", lambda pid: None if pid == proc.pid else original(pid)
            )
        if site == "discover":
            monkeypatch.setattr(
                process, "_children", lambda item: [proc.pid] if item == tree.owner else []
            )
            tree.discover()
            assert tree.identities[proc.pid] == identity
        elif site == "live":
            assert tree.live() == [proc.pid]
        elif site == "reap":
            proc.stdin.write("x")
            proc.stdin.flush()
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            assert poller.poll(2000)
            tree.reap(type("Driver", (), {"pid": -1, "poll": lambda self: 0})())
            assert tree.reaped == {proc.pid: 0}
            assert proc.pid not in tree.fds
            assert original(proc.pid) is None
        else:
            assert tree.cleanup(proc)
            assert proc.returncode is not None
            assert proc.pid not in tree.fds
            assert tree.reaped[proc.pid] == proc.returncode
            assert original(proc.pid) is None
    finally:
        try:
            owned_fd = tree.fds.pop(proc.pid, None)
            _close_proc_child(proc, owned_fd)
        finally:
            if original_fd is not None and original_fd != owned_fd:
                os.close(original_fd)
            tree.close()


def test_finished_descriptors_retire_without_losing_identity_or_inventing_wait_status():
    tree = process._OwnedTree()
    driver = type("Driver", (), {"pid": -1, "poll": lambda self: 0})()
    for _ in range(12):
        proc, identity, fd = _proc_child()
        tree.identities[proc.pid] = identity
        tree.fds[proc.pid] = fd
        try:
            proc.stdin.write("x")
            proc.stdin.flush()
            proc.wait(timeout=2)
            tree.reap(driver)
            assert proc.pid not in tree.fds
            assert tree.identities[proc.pid] == identity
            assert proc.pid not in tree.reaped
            assert tree.live() == []
            with pytest.raises(OSError):
                fcntl.fcntl(fd, fcntl.F_GETFD)
        finally:
            if proc.pid in tree.fds:
                _close_proc_child(proc, tree.fds.pop(proc.pid))
            else:
                _close_proc_child(proc, None)
    assert len(tree.report()) == 12
    assert all(not row["remaining"] and row["reaped_status"] is None for row in tree.report())


def test_active_supervisor_retires_sequential_descendant_descriptors():
    program = """
import json, os, subprocess, sys, time
from pathlib import Path
counts = []
for _ in range(12):
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(.06)'])
    child.wait(timeout=2)
    time.sleep(.04)
    counts.append(len(list(Path('/proc/'+str(os.getppid())+'/fd').iterdir())))
print(json.dumps(counts))
"""
    result = process.run_owned_command([sys.executable, "-c", program], timeout=5, text=True)
    counts = json.loads(result.stdout)
    assert max(counts) <= counts[0] + 2
    assert len(result.ownership["owned"]) >= 12
    assert result.ownership["cleanup_complete"]
    assert all(not row["remaining"] for row in result.ownership["owned"])


def test_closed_pidfd_is_an_error_instead_of_ready():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        with pytest.raises(OSError, match="invalid mutation process descriptor"):
            process._pidfd_ready(read_fd)
    finally:
        os.close(write_fd)


def test_retired_identity_cannot_be_rebound_or_signaled(monkeypatch):
    tree = process._OwnedTree()
    identity = process._Identity(999, tree.owner.pid, 100, "S")
    tree.identities[999] = identity
    monkeypatch.setattr(process, "_identity", lambda _: identity)
    monkeypatch.setattr(process, "_children", lambda item: [999] if item == tree.owner else [])
    monkeypatch.setattr(os, "pidfd_open", lambda _: pytest.fail("retired identity reopened"))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("retired identity signaled"))
    tree.discover()
    tree.signal(999, signal.SIGTERM)
    monkeypatch.setattr(process, "_identity", lambda _: process._Identity(999, tree.owner.pid, 101, "S"))
    with pytest.raises(RuntimeError, match="identity changed"):
        tree.discover()


@pytest.mark.parametrize("pending_type", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("diagnostic_type", [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("site", ["metadata", "note"])
def test_evidence_binder_first_control_precedence(pending_type, diagnostic_type, site):
    diagnostic = diagnostic_type("DIAGNOSTIC_INTERRUPTION")
    fired = []

    class Pending(pending_type):
        def __getattribute__(self, name):
            if site == "metadata" and name == "__dict__" and not fired:
                fired.append(site)
                raise diagnostic
            return super().__getattribute__(name)

        def add_note(self, note):
            if site == "note" and not fired:
                fired.append(site)
                raise diagnostic
            return super().add_note(note)

    pending = Pending("PENDING_ERROR")
    first_control = isinstance(pending, Exception) and not isinstance(diagnostic, Exception)
    if first_control:
        with pytest.raises(diagnostic_type) as caught:
            process._bind_cancellation_evidence(pending, note="diagnostic", marker=True)
        assert caught.value is diagnostic
    else:
        process._bind_cancellation_evidence(pending, note="diagnostic", marker=True)
        assert pending.marker and pending.cleanup_complete is False
        assert pending.cleanup_evidence_error is diagnostic
    assert fired == [site]

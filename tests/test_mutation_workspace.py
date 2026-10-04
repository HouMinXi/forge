"""A fresh invocation owns its mirror and preserves pre-existing cache state."""

import json
import os
import signal
import stat
import subprocess
import sys
import time

import pytest
from code_forge import mutation
from code_forge._mutation_process import MutationProcessError
from code_forge._mutation_workspace import MutationWorkspace, MutationWorkspaceError
from tests.mutation_result_fixture import write_inventory


@pytest.mark.parametrize("phase", ["acquire", "prepare", "restore", "config-read", "config-install", "config-restore"])
def test_regular_node_replaced_by_fifo_is_refused_before_reading(tmp_path, monkeypatch, phase):
    cache = _cache(tmp_path)
    _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    if phase != "acquire":
        workspace.acquire()
    if phase in ("restore", "config-install", "config-restore"):
        workspace.prepare()
    if phase == "config-restore":
        workspace.install_configs(b"[mutmut]\nsource_paths=src/calc.py\n", mutation._scoped_pyproject)
    target = (
        tmp_path / workspace.quarantine / "cached/original.bin"
        if phase == "restore"
        else cache / "cached/original.bin"
    )
    caller = "_snapshot"
    if phase.startswith("config-"):
        target = tmp_path / "setup.cfg"
        caller = "_read_config"
        if phase != "config-read":
            target = tmp_path / ".code-forge" / workspace.config_backup / "setup.cfg.original-node"
            caller = "install_configs" if phase == "config-install" else "restore_configs"
    original_open = os.open
    retained = target.with_name(target.name + ".retained")
    changed = []
    opened = []
    expected = []
    fifo_operations = []
    original_read, original_fdopen, original_fchmod = os.read, os.fdopen, os.fchmod

    def replace_before_open(path, flags, *args, **kwargs):
        if path == target.name and sys._getframe(1).f_code.co_name == caller and not changed:
            info = target.stat()
            expected.append((target.read_bytes(), info.st_mode, info.st_dev, info.st_ino))
            target.rename(retained)
            os.mkfifo(target, 0o600)
            changed.append(target.stat())
        fd = original_open(path, flags, *args, **kwargs)
        if changed and path == target.name:
            opened.append(fd)
        return fd

    def observe_fifo(operation, original):
        def observed(fd, *args, **kwargs):
            if stat.S_ISFIFO(os.fstat(fd).st_mode):
                fifo_operations.append(operation)
            return original(fd, *args, **kwargs)

        return observed

    def expired(signum, frame):
        raise TimeoutError("regular-node FIFO open exceeded the unchanged alarm bound")

    monkeypatch.setattr(os, "open", replace_before_open)
    monkeypatch.setattr(os, "read", observe_fifo("read", original_read))
    monkeypatch.setattr(os, "fdopen", observe_fifo("fdopen", original_fdopen))
    monkeypatch.setattr(os, "fchmod", observe_fifo("fchmod", original_fchmod))
    handler = signal.signal(signal.SIGALRM, expired)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.25)
        with pytest.raises(MutationWorkspaceError, match="identity changed|node changed"):
            if phase == "config-read":
                workspace._read_config(workspace.root_fd, "setup.cfg")
            elif phase == "config-install":
                workspace.install_configs(b"[mutmut]\nsource_paths=src/calc.py\n", mutation._scoped_pyproject)
            elif phase == "config-restore":
                workspace.restore_configs()
            else:
                getattr(workspace, phase)()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        workspace._close_fds()
    assert len(changed) == 1
    assert fifo_operations == []
    info = retained.stat()
    assert (retained.read_bytes(), info.st_mode, info.st_dev, info.st_ino) == expected[0]
    current = target.stat()
    assert stat.S_ISFIFO(current.st_mode)
    assert (current.st_dev, current.st_ino, current.st_mode) == (
        changed[0].st_dev, changed[0].st_ino, changed[0].st_mode
    )
    for fd in opened:
        with pytest.raises(OSError) as error:
            os.fstat(fd)
        assert error.value.errno == 9


def _cache(root):
    cache = root / "mutants"
    (cache / "cached").mkdir(parents=True)
    (cache / "cached/original.bin").write_bytes(b"original\x00cache")
    (cache / "cached/original.bin").chmod(0o640)
    (cache / "cached").chmod(0o750)
    outside = root / "outside.bin"
    outside.write_bytes(b"outside must remain intact")
    (cache / "foreign-link").symlink_to(outside)
    return cache


def test_cache_is_preserved_by_identity_bytes_modes_and_opaque_links(tmp_path):
    cache = _cache(tmp_path)
    original = cache.stat()
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    snapshot = workspace.original_snapshot
    workspace.prepare()
    assert cache.stat().st_ino != original.st_ino
    assert (tmp_path / workspace.quarantine).stat().st_ino == original.st_ino
    assert workspace._cache_snapshot(workspace.quarantine) == snapshot
    (cache / "owned-output").write_text("fresh native output")
    workspace.restore()
    assert cache.stat().st_ino == original.st_ino
    assert workspace._cache_snapshot("mutants") == snapshot
    assert not (cache / "owned-output").exists()
    assert (tmp_path / "outside.bin").read_bytes() == b"outside must remain intact"
    workspace.release()
    assert json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())["phase"] == "complete"


def test_incomplete_cleanup_retains_both_paths_and_refuses_stale_takeover(tmp_path):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.cleanup_complete = False
    with pytest.raises(MutationWorkspaceError, match="incomplete"):
        workspace.restore()
    with pytest.raises(MutationWorkspaceError, match="quarantine="):
        workspace.release()
    assert (tmp_path / "mutants").is_dir()
    assert (tmp_path / workspace.quarantine / "cached/original.bin").read_bytes() == b"original\x00cache"
    with pytest.raises(MutationWorkspaceError, match="no stale takeover"):
        MutationWorkspace(str(tmp_path)).acquire()


@pytest.mark.parametrize("phase", ["active", "incomplete", "invalid", "empty", "wrong-shape"])
def test_previous_unfinished_or_unparseable_journal_is_never_taken_over(tmp_path, phase):
    cache = _cache(tmp_path)
    lock = tmp_path / ".code-forge/mutation-owner.lock"
    lock.parent.mkdir()
    value = (
        "not-json"
        if phase == "invalid"
        else ""
        if phase == "empty"
        else "[]"
        if phase == "wrong-shape"
        else json.dumps({"phase": phase})
    )
    lock.write_text(value)
    with pytest.raises(MutationWorkspaceError):
        MutationWorkspace(str(tmp_path)).acquire()
    assert (
        lock.read_text() == value
        and (cache / "cached/original.bin").read_bytes() == b"original\x00cache"
    )


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "hardlink-valid-journal", "folder-link", "fifo-cache"]
)
def test_unsafe_lock_or_cache_refuses_without_following_foreign_nodes(tmp_path, kind):
    outside = tmp_path / "outside"
    outside.write_text("FOREIGN_SENTINEL")
    expected = "FOREIGN_SENTINEL"
    if kind == "hardlink-valid-journal":
        expected = '{"phase":"complete"}'
        outside.write_text(expected)
    folder = tmp_path / ".code-forge"
    if kind == "folder-link":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        folder.symlink_to(elsewhere, target_is_directory=True)
    else:
        folder.mkdir()
        lock = folder / "mutation-owner.lock"
        if kind == "symlink":
            lock.symlink_to(outside)
        elif kind in ("hardlink", "hardlink-valid-journal"):
            os.link(outside, lock)
        else:
            (tmp_path / "mutants").mkdir()
            os.mkfifo(tmp_path / "mutants/unsafe-pipe")
    with pytest.raises(MutationWorkspaceError):
        MutationWorkspace(str(tmp_path)).acquire()
    assert outside.read_text() == expected


def test_replaced_lock_namespace_is_not_written_or_used(tmp_path):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    lock = tmp_path / ".code-forge/mutation-owner.lock"
    lock.rename(lock.with_name("original-lock"))
    lock.write_text("FOREIGN_LOCK")
    try:
        with pytest.raises(MutationWorkspaceError, match="identity changed"):
            workspace.prepare()
    finally:
        with pytest.raises(MutationWorkspaceError):
            workspace.release()
    assert lock.read_text() == "FOREIGN_LOCK"
    assert (tmp_path / "mutants/cached/original.bin").read_bytes() == b"original\x00cache"


@pytest.mark.parametrize("kind", ["mirror", "quarantine", "cache-bytes", "cache-mode"])
def test_foreign_replacement_or_changed_cache_refuses_removal_and_restore(tmp_path, kind):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    if kind in ("mirror", "quarantine"):
        path = tmp_path / ("mutants" if kind == "mirror" else workspace.quarantine)
        path.rename(tmp_path / ("retained-" + kind))
        path.mkdir()
        (path / "foreign").write_text("FOREIGN_SENTINEL")
    else:
        path = tmp_path / workspace.quarantine / "cached/original.bin"
        if kind == "cache-bytes":
            path.write_text("CHANGED_CACHE")
        else:
            path.chmod(0o600)
    with pytest.raises(MutationWorkspaceError):
        workspace.restore()
    with pytest.raises(MutationWorkspaceError, match="retained for recovery"):
        workspace.release()
    if kind in ("mirror", "quarantine"):
        assert (path / "foreign").read_text() == "FOREIGN_SENTINEL"
    else:
        assert (
            path.read_text() == "CHANGED_CACHE"
            if kind == "cache-bytes"
            else stat.S_IMODE(path.stat().st_mode) == 0o600
        )


@pytest.mark.parametrize("empty", [False, True])
def test_no_replace_rename_preserves_an_existing_destination(tmp_path, empty):
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    (tmp_path / "source").mkdir()
    (tmp_path / "destination").mkdir()
    identity = (tmp_path / "destination").stat().st_ino
    if not empty:
        (tmp_path / "destination/foreign").write_text("FOREIGN_SENTINEL")
    try:
        with pytest.raises(MutationWorkspaceError, match="rename refused"):
            workspace._rename("source", "destination")
        assert (tmp_path / "destination").stat().st_ino == identity
        if not empty:
            assert (tmp_path / "destination/foreign").read_text() == "FOREIGN_SENTINEL"
        assert (tmp_path / "source").is_dir()
    finally:
        workspace.release()


def test_replaced_cache_is_refused_before_any_quarantine_touch(tmp_path):
    import shutil

    cache = _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    old = tmp_path / "retained-original"
    cache.rename(old)
    shutil.copytree(old, cache, symlinks=True)
    foreign_identity = cache.stat().st_ino
    try:
        with pytest.raises(MutationWorkspaceError, match="identity changed before"):
            workspace.prepare()
        assert cache.stat().st_ino == foreign_identity
        assert not (tmp_path / workspace.quarantine).exists()
    finally:
        with pytest.raises(MutationWorkspaceError, match="unprepared mutation cache changed"):
            workspace.release()


def test_foreign_disposal_replacement_is_not_deleted(tmp_path, monkeypatch):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    rename = workspace._rename

    def replace_after_move(source, target):
        rename(source, target)
        if target == workspace.disposal:
            (tmp_path / target).rename(tmp_path / "retained-owned")
            (tmp_path / target).mkdir()
            (tmp_path / target / "foreign").write_text("FOREIGN_DISPOSAL")

    monkeypatch.setattr(workspace, "_rename", replace_after_move)
    with pytest.raises(MutationWorkspaceError, match="disposal identity"):
        workspace.restore()
    with pytest.raises(MutationWorkspaceError):
        workspace.release()
    assert (tmp_path / workspace.disposal / "foreign").read_text() == "FOREIGN_DISPOSAL"
    assert (tmp_path / workspace.quarantine / "cached/original.bin").read_bytes() == b"original\x00cache"


def test_every_owned_namespace_open_refuses_symbolic_links(tmp_path, monkeypatch):
    _cache(tmp_path)
    opened = []
    original = os.open

    def record(path, flags, *args, **kwargs):
        assert flags & os.O_NOFOLLOW
        opened.append(str(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", record)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    # shutil.rmtree uses its own fd-safe lstat/open/fstat algorithm; the
    # explicit O_NOFOLLOW contract here covers our namespace acquisition.
    monkeypatch.setattr(os, "open", original)
    workspace.restore()
    workspace.release()
    assert str(tmp_path) in opened and ".code-forge" in opened and "mutation-owner.lock" in opened


@pytest.mark.parametrize(
    "case",
    [
        "unsupported",
        "unknown-owner",
        "journal-write",
        "cache-file",
        "cache-open-replaced",
        "root-replaced",
    ],
)
def test_acquire_faults_close_descriptors_and_do_not_launch_payloads(tmp_path, monkeypatch, case):
    import shutil
    from code_forge import _mutation_workspace as module

    workspace = MutationWorkspace(str(tmp_path))
    if case == "unsupported":
        monkeypatch.setattr(module.sys, "platform", "unsupported")
    elif case == "unknown-owner":
        monkeypatch.setattr(module, "_identity", lambda _: None)
    elif case == "journal-write":
        monkeypatch.setattr(
            workspace, "_write_journal", lambda: (_ for _ in ()).throw(OSError("KNOWN_JOURNAL_FAILURE"))
        )
    elif case == "cache-file":
        (tmp_path / "mutants").write_text("REGULAR_FILE_CACHE")
    elif case == "cache-open-replaced":
        _cache(tmp_path)
        original = os.open

        def open_after_replacement(path, flags, *args, **kwargs):
            if path == "mutants":
                (tmp_path / "mutants").rename(tmp_path / "retained-cache")
                shutil.copytree(tmp_path / "retained-cache", tmp_path / "mutants", symlinks=True)
            return original(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", open_after_replacement)
    elif case == "root-replaced":
        original = workspace._verify_lease

        def verify_after_root_replacement():
            retained = tmp_path.with_name(tmp_path.name + "-retained")
            tmp_path.rename(retained)
            tmp_path.mkdir()
            original()

        monkeypatch.setattr(workspace, "_verify_lease", verify_after_root_replacement)
    with pytest.raises(MutationWorkspaceError):
        workspace.acquire()
    assert workspace.root_fd is None and workspace.folder_fd is None and workspace.lock_fd is None


@pytest.mark.parametrize(
    "case", ["directory-open", "file-open", "file-bytes", "link-replace", "directory-change"]
)
def test_cache_snapshot_detects_actual_filesystem_races(tmp_path, monkeypatch, case):
    cache = _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    original_open, original_read, original_link = os.open, os.read, os.readlink
    changed = []

    def race_open(path, flags, *args, **kwargs):
        if not changed and (
            (case == "directory-open" and path == "cached")
            or (case == "file-open" and path == "original.bin")
        ):
            node = cache / ("cached" if case == "directory-open" else "cached/original.bin")
            node.rename(node.with_name("retained-node"))
            node.mkdir() if case == "directory-open" else node.write_bytes(b"foreign bytes")
            changed.append(True)
        return original_open(path, flags, *args, **kwargs)

    def race_read(fd, size):
        value = original_read(fd, size)
        if value and not changed and case == "file-bytes":
            (cache / "cached/original.bin").write_bytes(b"changed while hashing")
            changed.append(True)
        return value

    def race_link(path, *args, **kwargs):
        value = original_link(path, *args, **kwargs)
        if not changed and case == "link-replace":
            (cache / "foreign-link").unlink()
            (cache / "foreign-link").symlink_to("other-target")
            changed.append(True)
        elif not changed and case == "directory-change":
            (cache / "new-entry").write_text("added while snapshotting")
            changed.append(True)
        return value

    monkeypatch.setattr(os, "open", race_open)
    monkeypatch.setattr(os, "read", race_read)
    monkeypatch.setattr(os, "readlink", race_link)
    with pytest.raises(MutationWorkspaceError, match="changed while snapshotting"):
        workspace.acquire()
    assert changed and workspace.lock_fd is None


@pytest.mark.parametrize("case", ["bytes-before", "quarantine-replaced", "fresh-collision"])
def test_prepare_refuses_cache_changes_or_foreign_namespace_collision(tmp_path, monkeypatch, case):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    if case == "bytes-before":
        (tmp_path / "mutants/cached/original.bin").write_text("CHANGED_BEFORE_PREPARE")
    else:
        original = workspace._rename

        def replace_after_quarantine(source, target):
            original(source, target)
            if target == workspace.quarantine:
                if case == "quarantine-replaced":
                    (tmp_path / target).rename(tmp_path / "retained-original")
                    (tmp_path / target).mkdir()
                    (tmp_path / target / "foreign").write_text("FOREIGN_QUARANTINE")
                else:
                    (tmp_path / "mutants").mkdir()
                    (tmp_path / "mutants/foreign").write_text("FOREIGN_FRESH")

        monkeypatch.setattr(workspace, "_rename", replace_after_quarantine)
    try:
        with pytest.raises(MutationWorkspaceError):
            workspace.prepare()
    finally:
        with pytest.raises(MutationWorkspaceError):
            workspace.release()
    if case == "fresh-collision":
        assert (tmp_path / "mutants/foreign").read_text() == "FOREIGN_FRESH"


def test_missing_quarantine_refuses_restore_without_removing_active_mirror(tmp_path):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    (tmp_path / workspace.quarantine).rename(tmp_path / "retained-original")
    with pytest.raises(MutationWorkspaceError):
        workspace.restore()
    assert (tmp_path / "mutants").is_dir()
    with pytest.raises(MutationWorkspaceError):
        workspace.release()


def test_foreign_path_appearing_at_restore_is_never_overwritten(tmp_path, monkeypatch):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    rename = workspace._rename

    def collision(source, target):
        if source == workspace.quarantine:
            (tmp_path / "mutants").mkdir()
            (tmp_path / "mutants/foreign").write_text("FOREIGN_AT_RESTORE")
        rename(source, target)

    monkeypatch.setattr(workspace, "_rename", collision)
    with pytest.raises(MutationWorkspaceError, match="rename refused"):
        workspace.restore()
    with pytest.raises(MutationWorkspaceError):
        workspace.release()
    assert (tmp_path / "mutants/foreign").read_text() == "FOREIGN_AT_RESTORE"
    assert (tmp_path / workspace.quarantine / "cached/original.bin").read_bytes() == b"original\x00cache"


def test_foreign_restored_identity_is_detected(tmp_path, monkeypatch):
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    rename = workspace._rename

    def replaced(source, target):
        rename(source, target)
        if source == workspace.quarantine:
            (tmp_path / target).rename(tmp_path / "retained-original")
            (tmp_path / target).mkdir()
            (tmp_path / target / "foreign").write_text("FOREIGN_RESTORED")

    monkeypatch.setattr(workspace, "_rename", replaced)
    with pytest.raises(MutationWorkspaceError, match="restored mutation cache identity"):
        workspace.restore()
    with pytest.raises(MutationWorkspaceError):
        workspace.release()
    assert (tmp_path / "mutants/foreign").read_text() == "FOREIGN_RESTORED"


@pytest.mark.parametrize("outcome", ["normal", "timeout", "incomplete"])
def test_public_run_uses_fresh_mirror_and_retains_or_restores_cache(tmp_path, monkeypatch, outcome):
    cache = _cache(tmp_path)
    original = cache.stat()

    def execute(argv, **kwargs):
        output = ""
        if "run" in argv:
            assert cache.stat().st_ino != original.st_ino
            assert not (cache / "cached/original.bin").exists()
            output = write_inventory(tmp_path, "src/calc.py")
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(argv, 1)
            if outcome == "incomplete":
                raise MutationProcessError("KNOWN_OWNED_CHILD_REMAINS", cleanup_complete=False)
        if "results" in argv:
            output = "calc.x_example__mutmut_1: killed"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    if outcome == "incomplete":
        assert infra and "KNOWN_OWNED_CHILD_REMAINS" in infra[0]
        assert "quarantine=" in infra[-1] and not evidence["baseline_passed"]
        backups = list(tmp_path.glob(".mutants-forge-*"))
        assert len(backups) == 1 and backups[0].stat().st_ino == original.st_ino
        assert cache.stat().st_ino != original.st_ino
    else:
        assert not infra and cache.stat().st_ino == original.st_ino
        assert (cache / "cached/original.bin").read_bytes() == b"original\x00cache"
        assert not list(tmp_path.glob(".mutants-forge-*"))
        assert evidence["baseline_passed"] is (outcome == "normal")
        if outcome == "timeout":
            assert findings[0].fingerprint == "mutation-timeout"


def _configuration(root, *, empty=False, marked=False, invalid=False, plain=False):
    originals = {}
    for name, content, mode in (
        (
            "setup.cfg",
            b""
            if empty
            else mutation._CODE_FORGE_CFG_MARKER.encode() + b"\n"
            if marked
            else b"[user]\nname = original\n",
            0o640,
        ),
        (
            "pyproject.toml",
            b"\xff\x00original"
            if invalid
            else b"[project]\nname='original'\n"
            if plain
            else b"[project]\nname='original'\n[tool.mutmut]\nsource_paths=['foreign']\n",
            0o644,
        ),
    ):
        path = root / name
        path.write_bytes(content)
        path.chmod(mode)
        originals[name] = (content, mode, path.stat().st_ino)
    return originals


def _assert_original_configuration(root, originals):
    for name, (content, mode, inode) in originals.items():
        path = root / name
        assert path.read_bytes() == content
        assert stat.S_IMODE(path.stat().st_mode) == mode
        assert path.stat().st_ino == inode


@pytest.mark.parametrize("case", ["absent", "empty", "marked", "table", "invalid", "plain"])
def test_guarded_configuration_preserves_original_absence_bytes_modes_and_nodes(tmp_path, case):
    originals = (
        {}
        if case == "absent"
        else _configuration(
            tmp_path,
            empty=case == "empty",
            marked=case == "marked",
            invalid=case == "invalid",
            plain=case == "plain",
        )
    )
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"[mutmut]\nsource_paths=src/calc.py\n", mutation._scoped_pyproject)
    backup = tmp_path / ".code-forge" / workspace.config_backup
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert (tmp_path / "setup.cfg").read_bytes() == b"[mutmut]\nsource_paths=src/calc.py\n"
    for name, (content, _, _) in originals.items():
        clone = backup / (name + ".original-bytes")
        assert clone.read_bytes() == content and stat.S_IMODE(clone.stat().st_mode) == 0o600
        metadata = workspace.journal["config_files"][clone.name]
        assert metadata["identity"] == (clone.stat().st_dev, clone.stat().st_ino)
        assert metadata["mode"] == 0o600 and metadata["type"] == "file"
    if case in ("invalid", "plain"):
        assert (tmp_path / "pyproject.toml").stat().st_ino == originals["pyproject.toml"][2]
    workspace.restore_configs()
    assert backup.is_dir()  # Configuration restoration alone cannot discard recovery.
    _assert_original_configuration(tmp_path, originals)
    if not originals:
        assert not (tmp_path / "setup.cfg").exists() and not (tmp_path / "pyproject.toml").exists()
        assert not workspace.configs["setup.cfg"]["original"]["exists"]
    workspace.restore()
    workspace.remove_config_backup()
    workspace.release()
    assert not backup.exists()
    again = MutationWorkspace(str(tmp_path))
    again.acquire()
    again.release()


@pytest.mark.parametrize(
    "outcome", ["normal", "error", "timeout", "incomplete", "config-foreign", "cache-foreign"]
)
def test_public_configuration_recovery_refuses_foreign_paths_and_retains_incomplete_backups(
    tmp_path, monkeypatch, outcome
):
    originals = _configuration(tmp_path, empty=True)
    cache = _cache(tmp_path)
    cache_inode = cache.stat().st_ino
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        output = ""
        if "run" in argv:
            scoped = (tmp_path / "setup.cfg").read_text()
            assert mutation._CODE_FORGE_CFG_MARKER in scoped
            assert "only_mutate=src/calc.py\n" in scoped
            assert not mutation._pyproject_has_tool_mutmut((tmp_path / "pyproject.toml").read_text())
            output = write_inventory(tmp_path, "src/calc.py")
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(argv, 1)
            if outcome == "incomplete":
                raise MutationProcessError("RECORDED_CHILD_REMAINS", cleanup_complete=False)
            if outcome == "error":
                return subprocess.CompletedProcess(argv, 1, "", "actual native error")
            if outcome == "config-foreign":
                (tmp_path / "setup.cfg").rename(tmp_path / "retained-generated.cfg")
                (tmp_path / "setup.cfg").write_bytes(b"FOREIGN_CONFIG_SENTINEL")
            if outcome == "cache-foreign":
                cache.rename(tmp_path / "retained-owned-mirror")
                cache.mkdir()
                (cache / "foreign").write_bytes(b"FOREIGN_CACHE_SENTINEL")
        if "results" in argv:
            output = "calc.x_example__mutmut_1: killed"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"], [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    backup = list((tmp_path / ".code-forge").glob("mutation-config-*"))
    if outcome in ("incomplete", "config-foreign", "cache-foreign"):
        assert infra and not evidence["baseline_passed"] and len(backup) == 1
        assert "config_backup=" in infra[-1]
        for name, (content, _, _) in originals.items():
            assert (backup[0] / (name + ".original-bytes")).read_bytes() == content
        previous_calls = len(calls)
        _, retry_infra = mutation.run_mutation(["src/calc.py"], ["pytest"], cwd=tmp_path)
        assert retry_infra and "no stale takeover" in retry_infra[0]
        assert len(calls) == previous_calls
        if outcome == "config-foreign":
            assert (tmp_path / "setup.cfg").read_bytes() == b"FOREIGN_CONFIG_SENTINEL"
            assert (tmp_path / "mutants").exists()  # refusal precedes mirror removal
        elif outcome == "cache-foreign":
            _assert_original_configuration(tmp_path, originals)
            assert (cache / "foreign").read_bytes() == b"FOREIGN_CACHE_SENTINEL"
    else:
        _assert_original_configuration(tmp_path, originals)
        assert not backup and cache.stat().st_ino == cache_inode
        if outcome == "error":
            assert infra == ["mutmut run failed (exit 1): stderr: actual native error"]
        else:
            assert not infra
        assert bool(findings) == (outcome in ("error", "timeout"))


@pytest.mark.parametrize("name", ["setup.cfg", "pyproject.toml"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_unsafe_original_configuration_refuses_all_launches_without_touching_foreign_data(
    tmp_path, monkeypatch, name, kind
):
    sentinel = tmp_path / "outside"
    sentinel.write_bytes(b"OUTSIDE_CONFIGURATION_SENTINEL")
    sentinel.chmod(0o640)
    path = tmp_path / name
    if kind == "symlink":
        path.symlink_to(sentinel)
    elif kind == "hardlink":
        os.link(sentinel, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    monkeypatch.setattr(
        mutation, "run_owned_command", lambda *_a, **_k: pytest.fail("unsafe configuration launched")
    )
    _, infra = mutation.run_mutation(["src/calc.py"], ["pytest"], cwd=tmp_path)
    assert infra and "configuration requires a regular single-link file" in infra[0]
    assert sentinel.read_bytes() == b"OUTSIDE_CONFIGURATION_SENTINEL"
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o640
    assert not list((tmp_path / ".code-forge").glob("mutation-config-*"))
    if kind == "directory":
        path.rmdir()
    else:
        path.unlink()
    corrected = MutationWorkspace(str(tmp_path))
    corrected.acquire()  # read-only refusal did not poison an empty lease journal
    corrected.release()


@pytest.mark.parametrize(
    "kind",
    [
        "generated-bytes",
        "generated-mode",
        "generated-link",
        "backup-bytes",
        "backup-mode",
        "backup-node",
        "directory",
        "foreign-entry",
    ],
)
def test_configuration_recovery_rejects_changed_bound_nodes(tmp_path, kind):
    originals = _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"SCOPED_CONFIGURATION", mutation._scoped_pyproject)
    backup = tmp_path / ".code-forge" / workspace.config_backup
    target = (
        tmp_path / "setup.cfg" if kind.startswith("generated") else backup / "setup.cfg.original-bytes"
    )
    if kind.endswith("bytes"):
        target.write_bytes(b"CHANGED_BOUND_BYTES")
    elif kind.endswith("mode"):
        target.chmod(0o644)
    elif kind == "generated-link":
        target.rename(tmp_path / "retained-generated.cfg")
        target.symlink_to(tmp_path / "retained-generated.cfg")
    elif kind == "backup-node":
        target.rename(backup / "retained-backup")
        target.write_bytes(originals["setup.cfg"][0])
        target.chmod(0o600)
    elif kind == "directory":
        backup.rename(tmp_path / "retained-backup-directory")
        backup.mkdir(mode=0o700)
    else:
        (backup / "FOREIGN_ENTRY").write_bytes(b"FOREIGN_ENTRY_SENTINEL")
    with pytest.raises(MutationWorkspaceError):
        workspace.restore_configs()
    with pytest.raises(MutationWorkspaceError):
        workspace.release()
    assert backup.exists() and (tmp_path / ".code-forge/mutation-owner.lock").exists()
    if kind == "foreign-entry":
        assert (backup / "FOREIGN_ENTRY").read_bytes() == b"FOREIGN_ENTRY_SENTINEL"


def test_configuration_backup_is_not_removed_before_both_restorations(tmp_path):
    _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"SCOPED_CONFIGURATION", mutation._scoped_pyproject)
    with pytest.raises(MutationWorkspaceError, match="until complete restoration"):
        workspace.remove_config_backup()
    workspace.restore_configs()
    with pytest.raises(MutationWorkspaceError, match="until complete restoration"):
        workspace.remove_config_backup()
    workspace.restore()
    workspace.remove_config_backup()
    workspace.release()


@pytest.mark.parametrize("outcome", ["fail", "timeout", "changed-bytes", "foreign-node", "new-absent"])
def test_actual_baseline_has_durable_recovery_before_any_native_launch(tmp_path, outcome):
    originals = {} if outcome == "new-absent" else _configuration(tmp_path)
    command = (
        "from pathlib import Path; import json; "
        "backups=list(Path('.code-forge').glob('mutation-config-*')); "
        "assert len(backups)==1; "
        "assert (backups[0]/'setup.cfg.original-bytes').exists(); "
        "Path('baseline-recovery-observed').write_text('OBSERVED'); "
        "raise SystemExit(1)"
    )
    if outcome == "timeout":
        command = command.replace("raise SystemExit(1)", "import time; time.sleep(10)")
    elif outcome == "changed-bytes":
        command = command.replace(
            "raise SystemExit(1)",
            "Path('setup.cfg').write_bytes(b'BASELINE_MODIFIED'); raise SystemExit(1)",
        )
    elif outcome == "foreign-node":
        command = command.replace(
            "raise SystemExit(1)",
            "Path('setup.cfg').rename('retained-original.cfg'); Path('setup.cfg').write_bytes(b'FOREIGN_BASELINE'); raise SystemExit(1)",
        )
    elif outcome == "new-absent":
        command = command.replace(
            "assert (backups[0]/'setup.cfg.original-bytes').exists(); ",
            "assert not (backups[0]/'setup.cfg.original-bytes').exists(); ",
        )
        command = command.replace(
            "raise SystemExit(1)",
            "Path('setup.cfg').write_bytes(b'FOREIGN_NEW_CONFIG'); raise SystemExit(1)",
        )
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"],
        [sys.executable, "-c", command],
        cwd=tmp_path,
        baseline_timeout=1,
        _evidence=evidence,
    )
    assert findings and infra and not evidence["baseline_passed"]
    assert (tmp_path / "baseline-recovery-observed").read_text() == "OBSERVED"
    backups = list((tmp_path / ".code-forge").glob("mutation-config-*"))
    assert not (tmp_path / "mutants").exists()
    if outcome in ("fail", "timeout"):
        assert not backups
        _assert_original_configuration(tmp_path, originals)
        assert (
            json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())["phase"] == "complete"
        )
    else:
        assert len(backups) == 1 and "config_backup=" in infra[-1]
        if originals:
            assert (backups[0] / "setup.cfg.original-bytes").read_bytes() == originals["setup.cfg"][0]
        assert (tmp_path / "setup.cfg").read_bytes() == {
            "changed-bytes": b"BASELINE_MODIFIED",
            "foreign-node": b"FOREIGN_BASELINE",
            "new-absent": b"FOREIGN_NEW_CONFIG",
        }[outcome]
        with pytest.raises(MutationWorkspaceError, match="no stale takeover"):
            MutationWorkspace(str(tmp_path)).acquire()


@pytest.mark.parametrize("kind", ["opening-node", "reading-bytes", "reading-node"])
def test_configuration_capture_detects_actual_open_and_read_races(tmp_path, monkeypatch, kind):
    _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original_open, original_fdopen = os.open, os.fdopen
    changed = False
    if kind == "opening-node":

        def swap(path, flags, *args, **kwargs):
            nonlocal changed
            if path == "setup.cfg" and not changed:
                changed = True
                (tmp_path / "setup.cfg").rename(tmp_path / "retained-original")
                (tmp_path / "setup.cfg").write_bytes(b"FOREIGN_OPEN_NODE")
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swap)
    else:

        def changed_stream(fd, mode="r", *args, **kwargs):
            stream = original_fdopen(fd, mode, *args, **kwargs)

            class Stream:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    stream.close()

                def read(self):
                    data = stream.read()
                    path = tmp_path / "setup.cfg"
                    if kind == "reading-node":
                        path.rename(tmp_path / "retained-original")
                    path.write_bytes(b"CHANGED_DURING_READ")
                    return data

            return Stream()

        monkeypatch.setattr(os, "fdopen", changed_stream)
    try:
        with pytest.raises(MutationWorkspaceError, match="changed while"):
            workspace._read_config(workspace.root_fd, "setup.cfg")
    finally:
        os.close(workspace.root_fd)
        workspace.root_fd = None


@pytest.mark.parametrize(
    "kind",
    [
        "backup-bytes",
        "held-node",
        "restore-node",
        "missing-byte-backup",
        "held-collision",
        "begin-directory",
        "begin-write",
    ],
)
def test_configuration_partial_install_and_recovery_faults_remain_actionable(
    tmp_path, monkeypatch, kind
):
    _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    if kind == "begin-write":
        monkeypatch.setattr(
            workspace,
            "_write_private_config",
            lambda *_: (_ for _ in ()).throw(OSError("MEASURED_BACKUP_WRITE_ERROR")),
        )
        with pytest.raises(MutationWorkspaceError, match="config_backup="):
            workspace.acquire()
        assert not workspace.acquired
        with pytest.raises(MutationWorkspaceError, match="no stale takeover"):
            MutationWorkspace(str(tmp_path)).acquire()
        return
    if kind == "begin-directory":
        original = os.open

        def replace_before_open(path, flags, *args, **kwargs):
            if path == workspace.config_backup:
                backup = tmp_path / ".code-forge" / path
                backup.rename(tmp_path / "retained-backup-directory")
                backup.mkdir(mode=0o750)
            return original(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", replace_before_open)
        with pytest.raises(MutationWorkspaceError, match="changed while opening"):
            workspace.acquire()
        assert stat.S_IMODE((tmp_path / ".code-forge" / workspace.config_backup).stat().st_mode) == 0o750
        return
    workspace.acquire()
    workspace.prepare()
    backup = tmp_path / ".code-forge" / workspace.config_backup
    if kind == "backup-bytes":
        read = workspace._read_config

        def corrupt_after_write(directory_fd, name, expected=None):
            if name == "setup.cfg.generated-node" and expected is None:
                (backup / name).write_bytes(b"MEASURED_WRITTEN_BYTE_FAULT")
            return read(directory_fd, name, expected)

        monkeypatch.setattr(workspace, "_read_config", corrupt_after_write)
        with pytest.raises(MutationWorkspaceError, match="backup identity or bytes"):
            workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
    elif kind == "held-node":
        original_open = os.open

        def swap_before_privacy(path, flags, *args, **kwargs):
            if path == "setup.cfg.original-node":
                count[0] += 1
                if count[0] == 2:
                    (backup / path).rename(backup / "retained-original-node")
                    (backup / path).write_bytes(b"FOREIGN_HELD_NODE")
            return original_open(path, flags, *args, **kwargs)

        count = [0]
        monkeypatch.setattr(os, "open", swap_before_privacy)
        with pytest.raises(MutationWorkspaceError, match="before privacy"):
            workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
    else:
        workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
        if kind == "missing-byte-backup":
            (backup / "setup.cfg.original-bytes").unlink()
            del workspace.config_files["setup.cfg.original-bytes"]
        elif kind == "held-collision":
            original_rename = workspace._rename_between

            def collision(source_fd, source, target_fd, target):
                original_rename(source_fd, source, target_fd, target)
                if source == "setup.cfg" and target.endswith("generated-node"):
                    (tmp_path / "setup.cfg").write_bytes(b"FOREIGN_BEFORE_ORIGINAL_RESTORE")

            monkeypatch.setattr(workspace, "_rename_between", collision)
        else:
            original_open = os.open

            def swap_restore_fd(path, flags, *args, **kwargs):
                if path == "setup.cfg.original-node":
                    count[0] += 1
                    if count[0] == 2:
                        (backup / path).rename(backup / "retained-original-node")
                        (backup / path).write_bytes(b"FOREIGN_RESTORE_NODE")
                        (backup / path).chmod(0o644)
                return original_open(path, flags, *args, **kwargs)

            count = [0]
            monkeypatch.setattr(os, "open", swap_restore_fd)
        with pytest.raises(MutationWorkspaceError):
            workspace.restore_configs()
        if kind == "restore-node":
            assert not (tmp_path / "setup.cfg").exists()
            assert (backup / "setup.cfg.original-node").read_bytes() == b"FOREIGN_RESTORE_NODE"
            assert stat.S_IMODE((backup / "setup.cfg.original-node").stat().st_mode) == 0o644
        if kind == "held-collision":
            with pytest.raises(MutationWorkspaceError, match="foreign configuration blocks"):
                workspace.restore_configs()
    workspace.cleanup_complete = False
    with pytest.raises(MutationWorkspaceError, match="config_backup="):
        workspace.release()
    assert backup.is_dir() and (tmp_path / "mutants").exists()


def test_incomplete_owned_teardown_never_enters_configuration_or_mirror_recovery(tmp_path, monkeypatch):
    workspace = MutationWorkspace(str(tmp_path))
    workspace.finish()  # A refused/unacquired invocation has no state to restore.
    workspace.restore_configs()
    _configuration(tmp_path)
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
    workspace.cleanup_complete = False
    with pytest.raises(MutationWorkspaceError, match="incomplete"):
        workspace.restore_configs()
    monkeypatch.setattr(
        workspace, "restore_configs", lambda: pytest.fail("incomplete teardown entered config recovery")
    )
    with pytest.raises(MutationWorkspaceError, match="incomplete"):
        workspace.finish()
    monkeypatch.setattr(workspace, "finish", lambda: pytest.fail("incomplete teardown entered recovery"))
    with pytest.raises(MutationWorkspaceError, match="retained for recovery"):
        workspace.release()
    assert (tmp_path / "setup.cfg").read_bytes() == b"SCOPED"


def test_hardlinked_lease_is_refused_before_touching_the_kernel_lock(tmp_path, monkeypatch):
    import fcntl

    folder = tmp_path / ".code-forge"
    folder.mkdir()
    foreign = tmp_path / "foreign-regular-file"
    foreign.write_text('{"phase":"complete"}')
    os.link(foreign, folder / "mutation-owner.lock")
    monkeypatch.setattr(fcntl, "flock", lambda *_a: pytest.fail("hardlinked foreign inode was locked"))
    with pytest.raises(MutationWorkspaceError, match="single-link"):
        MutationWorkspace(str(tmp_path)).acquire()
    assert foreign.read_text() == '{"phase":"complete"}'


def test_failed_recovery_journal_update_is_reported_without_claiming_completion(tmp_path, monkeypatch):
    _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    write = workspace._write_journal

    def fail_active_write():
        if workspace.acquired:
            raise OSError("MEASURED_ACTIVE_JOURNAL_ERROR")
        write()

    monkeypatch.setattr(workspace, "_write_journal", fail_active_write)
    with pytest.raises(MutationWorkspaceError, match="journal update failed.*config_backup="):
        workspace.acquire()
    assert not workspace.acquired


@pytest.mark.parametrize("change", ["bytes", "identity"])
def test_actual_failed_baseline_cache_changes_are_retained_without_false_restoration(tmp_path, change):
    originals = _configuration(tmp_path)
    _cache(tmp_path)
    command = (
        "from pathlib import Path; "
        + (
            "Path('mutants/cached/original.bin').write_bytes(b'BASELINE_CACHE_CHANGED'); "
            if change == "bytes"
            else "Path('mutants').rename('retained-original-cache'); Path('mutants').mkdir(); Path('mutants/foreign').write_bytes(b'FOREIGN_BASELINE_CACHE'); "
        )
        + "raise SystemExit(1)"
    )
    evidence = {}
    _, infra = mutation.run_mutation(
        ["src/calc.py"], [sys.executable, "-c", command], cwd=tmp_path, _evidence=evidence
    )
    assert infra and "unprepared mutation cache changed" in infra[-1] and not evidence["baseline_passed"]
    _assert_original_configuration(tmp_path, originals)
    backups = list((tmp_path / ".code-forge").glob("mutation-config-*"))
    assert len(backups) == 1
    assert (backups[0] / "setup.cfg.original-bytes").read_bytes() == originals["setup.cfg"][0]
    current = tmp_path / ("mutants/cached/original.bin" if change == "bytes" else "mutants/foreign")
    assert current.read_bytes() == (
        b"BASELINE_CACHE_CHANGED" if change == "bytes" else b"FOREIGN_BASELINE_CACHE"
    )


def test_successful_owned_empty_containers_are_bounded_and_reused_by_inode(tmp_path):
    originals = _configuration(tmp_path)
    _cache(tmp_path)
    identities = []
    for _ in range(3):
        workspace = MutationWorkspace(str(tmp_path))
        workspace.acquire()
        workspace.prepare()
        workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
        (tmp_path / "mutants/own-output").write_bytes(b"OWNED_NATIVE_OUTPUT")
        workspace.release()
        _assert_original_configuration(tmp_path, originals)
        pools = [tmp_path / ".code-forge" / ("mutation-empty-" + kind) for kind in ("mirror", "config")]
        identities.append(tuple((p.stat().st_dev, p.stat().st_ino) for p in pools))
        assert all(not list(p.iterdir()) and stat.S_IMODE(p.stat().st_mode) == 0o700 for p in pools)
        journal = json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())
        assert journal["phase"] == "complete" and all(journal["empty_containers"].values())
        assert len(list((tmp_path / ".code-forge").iterdir())) == 3
    assert identities[0] == identities[1] == identities[2]


def test_reused_namespace_acquisition_opens_each_owned_path_without_following_links(
    tmp_path, monkeypatch
):
    _configuration(tmp_path)
    first = MutationWorkspace(str(tmp_path))
    first.acquire()
    first.prepare()
    first.install_configs(b"SCOPED", mutation._scoped_pyproject)
    first.release()
    original = os.open
    opened = []

    def record(path, flags, *args, **kwargs):
        assert flags & os.O_NOFOLLOW
        opened.append(str(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", record)
    second = MutationWorkspace(str(tmp_path))
    second.acquire()
    second.prepare()
    monkeypatch.setattr(os, "open", original)
    second.release()
    assert {"mutation-empty-mirror", "mutation-empty-config", "setup.cfg", "pyproject.toml"}.issubset(
        opened
    )


@pytest.mark.parametrize("kind", ["mirror", "config"])
@pytest.mark.parametrize("boundary", ["rename", "open", "delete", "contents-syscall"])
@pytest.mark.parametrize("empty", [False, True])
def test_foreign_empty_or_nonempty_container_replacements_are_never_deleted(
    tmp_path, monkeypatch, kind, boundary, empty
):
    originals = _configuration(tmp_path)
    _cache(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
    (tmp_path / "mutants/owned-subdirectory").mkdir()
    (tmp_path / "mutants/owned-subdirectory/owned").write_bytes(b"OWNED_NATIVE_DATA")
    folder = tmp_path / ".code-forge"
    pool = folder / workspace._container_name(kind)
    retained = folder / ("retained-owned-" + kind)
    replaced = []
    foreign_inode = []
    wrong_parent_nodes = {}
    if boundary == "contents-syscall":
        if kind == "mirror":
            wrong_parent = folder / "owned-subdirectory"
            wrong_parent.mkdir()
            sentinel = wrong_parent / "foreign-state-sentinel"
            sentinel.write_bytes(b"FOREIGN_WRONG_PARENT_DIRECTORY")
            wrong_parent_nodes[sentinel] = (sentinel.read_bytes(), sentinel.stat().st_ino)
        else:
            for node in (folder / workspace.config_backup).iterdir():
                sentinel = folder / node.name
                sentinel.write_bytes(b"FOREIGN_WRONG_PARENT_FILE")
                wrong_parent_nodes[sentinel] = (sentinel.read_bytes(), sentinel.stat().st_ino)

    def replace(path):
        path.rename(retained)
        path.mkdir(mode=0o700)
        foreign_inode.append(path.stat().st_ino)
        if not empty:
            (path / "foreign-sentinel").write_bytes(b"FOREIGN_CONTAINER_SENTINEL")
        replaced.append(path)

    if boundary == "rename":
        original = workspace._rename_between

        def before_move(source_fd, source, target_fd, target):
            if target == pool.name and not replaced:
                replace(tmp_path / source if kind == "mirror" else folder / source)
            original(source_fd, source, target_fd, target)

        monkeypatch.setattr(workspace, "_rename_between", before_move)
    elif boundary == "open":
        original = os.open

        def before_open(path, flags, *args, **kwargs):
            if path == pool.name and not replaced:
                replace(pool)
            return original(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", before_open)
    elif boundary == "delete":
        original = workspace._empty_container

        def before_contents(which, expected):
            if which == kind and not replaced:
                replace(pool)
            original(which, expected)

        monkeypatch.setattr(workspace, "_empty_container", before_contents)
    elif kind == "mirror":
        import functools
        import shutil

        original = shutil.rmtree

        @functools.wraps(original)
        def before_directory_contents(path, *args, **kwargs):
            if pool.exists() and not replaced:
                replace(pool)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", before_directory_contents)
    else:
        original = os.unlink

        def before_file_contents(path, *args, **kwargs):
            if pool.exists() and not replaced:
                replace(pool)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", before_file_contents)
    with pytest.raises(MutationWorkspaceError, match="owned_containers="):
        workspace.release()
    assert replaced and pool.stat().st_ino == foreign_inode[0]
    assert (
        list(pool.iterdir()) == []
        if empty
        else (pool / "foreign-sentinel").read_bytes() == b"FOREIGN_CONTAINER_SENTINEL"
    )
    for sentinel, (content, inode) in wrong_parent_nodes.items():
        assert sentinel.read_bytes() == content and sentinel.stat().st_ino == inode
    _assert_original_configuration(tmp_path, originals)
    config_backup = retained if kind == "config" else folder / workspace.config_backup
    for name, (content, _, _) in originals.items():
        clone = config_backup / (name + ".original-bytes")
        assert clone.read_bytes() == content and stat.S_IMODE(clone.stat().st_mode) == 0o600
    with pytest.raises(MutationWorkspaceError, match="no stale takeover"):
        MutationWorkspace(str(tmp_path)).acquire()


@pytest.mark.parametrize("kind", ["mirror", "config"])
@pytest.mark.parametrize(
    "fault",
    ["missing", "replacement", "nonempty", "mode", "symlink", "unknown-journal", "invalid-identity"],
)
def test_completed_container_reuse_requires_exact_identity_mode_and_emptiness(tmp_path, kind, fault):
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
    workspace.release()
    pool = tmp_path / ".code-forge" / workspace._container_name(kind)
    outside = tmp_path / "foreign-sentinel"
    outside.write_bytes(b"OUTSIDE_CONTAINER_SENTINEL")
    if fault in ("missing", "replacement", "symlink"):
        pool.rename(tmp_path / ("retained-original-" + kind))
        if fault == "replacement":
            pool.mkdir(mode=0o700)
        elif fault == "symlink":
            pool.symlink_to(tmp_path / ("retained-original-" + kind))
    elif fault == "nonempty":
        (pool / "foreign").write_bytes(b"FOREIGN_CONTENTS")
    elif fault == "mode":
        pool.chmod(0o750)
    else:
        lock = tmp_path / ".code-forge/mutation-owner.lock"
        data = json.loads(lock.read_text())
        if fault == "unknown-journal":
            data.pop("empty_containers")
        else:
            data["empty_containers"][kind] = [True, 1]
        lock.write_text(json.dumps(data))
    with pytest.raises(MutationWorkspaceError):
        MutationWorkspace(str(tmp_path)).acquire()
    assert outside.read_bytes() == b"OUTSIDE_CONTAINER_SENTINEL"
    if fault == "nonempty":
        assert (pool / "foreign").read_bytes() == b"FOREIGN_CONTENTS"


@pytest.mark.parametrize("kind", ["mirror", "config"])
def test_reused_container_replacement_after_atomic_move_is_refused(tmp_path, monkeypatch, kind):
    originals = _configuration(tmp_path)
    first = MutationWorkspace(str(tmp_path))
    first.acquire()
    first.prepare()
    first.install_configs(b"SCOPED", mutation._scoped_pyproject)
    first.release()
    workspace = MutationWorkspace(str(tmp_path))
    original = workspace._rename_between

    def replace_moved(source_fd, source, target_fd, target):
        original(source_fd, source, target_fd, target)
        if source == workspace._container_name(kind):
            path = tmp_path / target if kind == "mirror" else tmp_path / ".code-forge" / target
            path.rename(tmp_path / ("retained-moved-" + kind))
            path.mkdir(mode=0o700)
            (path / "foreign").write_bytes(b"FOREIGN_MOVED_CONTAINER")

    monkeypatch.setattr(workspace, "_rename_between", replace_moved)
    if kind == "config":
        with pytest.raises(MutationWorkspaceError, match="foreign moved configuration"):
            workspace.acquire()
        path = tmp_path / ".code-forge" / workspace.config_backup
    else:
        workspace.acquire()
        with pytest.raises(MutationWorkspaceError, match="foreign moved mirror"):
            workspace.prepare()
        with pytest.raises(MutationWorkspaceError):
            workspace.release()
        path = tmp_path / "mutants"
    assert (path / "foreign").read_bytes() == b"FOREIGN_MOVED_CONTAINER"
    _assert_original_configuration(tmp_path, originals)


@pytest.mark.parametrize("fault", ["unsafe-platform", "new-entry"])
def test_owned_contents_cleanup_refuses_unsafe_traversal_and_concurrent_new_entries(
    tmp_path, monkeypatch, fault
):
    import shutil

    _configuration(tmp_path)
    workspace = MutationWorkspace(str(tmp_path))
    workspace.acquire()
    workspace.prepare()
    workspace.install_configs(b"SCOPED", mutation._scoped_pyproject)
    if fault == "unsafe-platform":
        (tmp_path / "mutants/owned-directory").mkdir()
        monkeypatch.setattr(shutil.rmtree, "avoids_symlink_attacks", False)
    else:
        (tmp_path / "mutants/owned-file").write_bytes(b"OWNED_FILE")
        original = os.unlink
        added = []

        def insert_after_unlink(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if path == "owned-file" and not added:
                pool = tmp_path / ".code-forge/mutation-empty-mirror"
                (pool / "foreign-entry").write_bytes(b"FOREIGN_ADDED_ENTRY")
                added.append(True)

        monkeypatch.setattr(os, "unlink", insert_after_unlink)
    with pytest.raises(MutationWorkspaceError):
        workspace.release()
    if fault == "new-entry":
        assert (
            tmp_path / ".code-forge/mutation-empty-mirror/foreign-entry"
        ).read_bytes() == b"FOREIGN_ADDED_ENTRY"


@pytest.mark.parametrize(
    "fault", ["unbound", "outside", "deleted", "refill-bytes", "absent-fd", "removed", "invalid-journal"]
)
def test_original_byte_retention_refuses_unsafe_targets_and_reports_refill_failure(
    tmp_path, monkeypatch, fault
):
    workspace = MutationWorkspace(str(tmp_path))
    if fault == "absent-fd":
        workspace._retain_config_backup()
        return
    _configuration(tmp_path)
    workspace.acquire()
    backup = tmp_path / ".code-forge" / workspace.config_backup
    if fault == "removed":
        workspace.release()
        workspace._retain_config_backup()
        return
    if fault == "invalid-journal":
        workspace.release()
        lock = tmp_path / ".code-forge/mutation-owner.lock"
        data = json.loads(lock.read_text())
        data["empty_containers"] = []
        lock.write_text(json.dumps(data))
        with pytest.raises(MutationWorkspaceError, match="invalid owned container journal"):
            MutationWorkspace(str(tmp_path)).acquire()
        return
    if fault == "unbound":
        workspace.config_identity = (-1, -1)
    elif fault == "outside":
        outside = tmp_path / "outside-recovery"
        backup.rename(outside)
        outside_clone = outside / "setup.cfg.original-bytes"
        outside_clone.unlink()
    elif fault == "deleted":
        for node in backup.iterdir():
            node.unlink()
        backup.rmdir()
    else:
        (backup / "setup.cfg.original-bytes").unlink()
        original = workspace._read_config

        def corrupt_refill(directory_fd, name, expected=None):
            if name == "setup.cfg.original-bytes" and expected is None:
                (backup / name).write_bytes(b"CORRUPTED_REFILLED_CLONE")
            return original(directory_fd, name, expected)

        monkeypatch.setattr(workspace, "_read_config", corrupt_refill)
    with pytest.raises(MutationWorkspaceError):
        workspace._retain_config_backup()
    if fault == "outside":
        assert not outside_clone.exists()
    workspace.cleanup_complete = False
    with pytest.raises(MutationWorkspaceError):
        workspace.release()


def _await(path, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError("first invocation did not reach its owned baseline barrier")


def test_actual_same_root_competitor_refuses_and_different_root_can_run(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _cache(first)
    marker, release = first / "baseline-marker", first / "release-barrier"
    baseline = f"from pathlib import Path;import time;Path({str(marker)!r}).write_text('held');\nwhile not Path({str(release)!r}).exists(): time.sleep(.01)\nraise SystemExit(1)"
    program = f"from pathlib import Path;from code_forge.mutation import run_mutation;import json;f,i=run_mutation(['src/calc.py'],[{sys.executable!r},'-c',{baseline!r}],cwd=Path({str(first)!r}),baseline_timeout=5);print(json.dumps({{'findings':[x.id for x in f],'infra':i}}))"
    caller = subprocess.Popen(
        [sys.executable, "-c", program], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        _await(marker)
        evidence = {}
        findings, infra = mutation.run_mutation(
            ["src/calc.py"], [sys.executable, "-c", "raise SystemExit(1)"], cwd=first, _evidence=evidence
        )
        assert findings[0].id == "MUTATION_ERROR" and "busy" in infra[0]
        assert not evidence["baseline_passed"]
        other, errors = mutation.run_mutation(
            ["src/calc.py"],
            [sys.executable, "-c", "raise SystemExit(1)"],
            cwd=second,
            baseline_timeout=2,
        )
        assert other[0].id == "MUTATION_SKIPPED" and "baseline failed" in errors[0]
        assert not (first / "release-barrier").exists()
        release.write_text("continue")
        stdout, stderr = caller.communicate(timeout=3)
        assert caller.returncode == 0, stderr
        assert json.loads(stdout)["findings"] == ["MUTATION_SKIPPED"]
        assert (first / "mutants/cached/original.bin").read_bytes() == b"original\x00cache"
        assert json.loads((first / ".code-forge/mutation-owner.lock").read_text())["phase"] == "complete"
    finally:
        release.write_text("continue")
        if caller.poll() is None:
            caller.send_signal(signal.SIGTERM)
            caller.communicate(timeout=3)

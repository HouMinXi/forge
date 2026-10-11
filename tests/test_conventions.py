# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for conventions.py -- D11 conventions-digest slot (Phase 15a).

Covers:
  - get_same_repo_digest / get_digest
  - _extract_python_public_names: async, top-level only, large file skip,
    venv/_SKIP_DIRS pruning, symlink prefix-collision rejection
"""

import errno
import os
import stat
import sys
from pathlib import Path

import pytest


from code_forge.conventions import (
    _extract_python_public_names,
    get_digest,
    get_same_repo_digest,
)


class TestGetSameRepoDigest:
    def test_empty_on_no_python_files(self, tmp_path):
        assert get_same_repo_digest(tmp_path) == ""

    def test_extracts_public_names(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "example.py").write_text("def hello(): pass\nclass MyClass: pass\ndef _private(): pass\n")
        digest = get_same_repo_digest(tmp_path)
        assert "hello" in digest
        assert "MyClass" in digest
        assert "_private" not in digest

    def test_syntax_error_file_skipped(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "bad.py").write_text("def (\n")
        # must not raise
        result = get_same_repo_digest(tmp_path)
        assert result == ""

    def test_digest_empty_no_sources(self, tmp_path):
        assert get_same_repo_digest(tmp_path) == ""


class TestGetDigest:
    def test_get_digest_uses_parts_list(self, tmp_path):
        """get_digest must build via parts list and match get_same_repo_digest."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "example.py").write_text("def hello(): pass\nclass MyClass: pass\n")
        assert get_digest(tmp_path) == get_same_repo_digest(tmp_path)

    def test_get_digest_returns_empty_for_nonexistent(self):
        result = get_digest(Path("/nonexistent_repo_path_xyz"))
        assert result == ""

    def test_same_repo_names_survive_missing_openat2(self, tmp_path, monkeypatch):
        from code_forge import conventions

        source = tmp_path / "src" / "api.py"
        source.parent.mkdir()
        source.write_text("def portable_name(): pass\n")
        monkeypatch.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
        monkeypatch.setattr(conventions, "_SYSCALL", None)
        assert "portable_name" in get_same_repo_digest(tmp_path)

    def test_same_repo_names_survive_openat2_enosys(self, tmp_path, monkeypatch):
        from code_forge import conventions

        if conventions._OPENAT2_SYSCALL_NUMBER is None or conventions._SYSCALL is None:
            pytest.skip("requires openat2 dispatch")

        source = tmp_path / "src" / "api.py"
        source.parent.mkdir()
        source.write_text("def portable_name(): pass\n")

        def unavailable(*_args, **_kwargs):
            raise OSError(errno.ENOSYS, "openat2 unavailable")

        monkeypatch.setattr(conventions, "_openat2_beneath", unavailable)
        assert "portable_name" in get_same_repo_digest(tmp_path)

    def test_portable_names_without_search_only_flags(self, tmp_path, monkeypatch):
        from code_forge import conventions

        source = tmp_path / "src" / "api.py"
        source.parent.mkdir()
        source.write_text("def portable_name(): pass\n")
        monkeypatch.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
        monkeypatch.setattr(conventions, "_SYSCALL", None)
        monkeypatch.delattr(os, "O_PATH", raising=False)
        monkeypatch.delattr(os, "O_SEARCH", raising=False)
        assert "portable_name" in get_same_repo_digest(tmp_path)

    def test_portable_component_walk_rejects_transient_outside_link(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "src" / "sub"
        nested.mkdir(parents=True)
        source = nested / "api.py"
        source.write_text("def inside_name(): pass\n")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "api.py").write_text("def outside_name(): pass\n")
        backup = root / "src" / "sub.saved"
        real_portable = conventions._openat_beneath_portable
        swaps = []

        def swap_before_components(directory_fd, relative, flags):
            if Path(relative).name == "api.py" and not swaps:
                nested.rename(backup)
                nested.symlink_to(outside, target_is_directory=True)
                try:
                    return real_portable(directory_fd, relative, flags)
                finally:
                    nested.unlink()
                    backup.rename(nested)
                    swaps.append(1)
            return real_portable(directory_fd, relative, flags)

        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
            patcher.setattr(conventions, "_SYSCALL", None)
            patcher.setattr(conventions, "_openat_beneath_portable", swap_before_components)
            assert conventions._read_regular_text(source, scan_root=root) is None
        assert swaps == [1]
        assert "inside_name" in get_same_repo_digest(root)

    @pytest.mark.parametrize("destination_name", ["outside", "repo-neighbor"])
    def test_portable_open_rejects_directory_moved_after_component_walk(
        self, tmp_path, monkeypatch, destination_name
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "src" / "sub"
        nested.mkdir(parents=True)
        source = nested / "api.py"
        source.write_text("def inside_name(): pass\n")
        moved = tmp_path / destination_name
        original_open = os.open
        swaps = []

        def move_before_leaf(path, flags, *args, **kwargs):
            if path == "api.py" and kwargs.get("dir_fd") is not None and not swaps:
                nested.rename(moved)
                (moved / "api.py").write_text("def outside_name(): pass\n")
                nested.mkdir()
                os.link(moved / "api.py", nested / "api.py")
                swaps.append(1)
            return original_open(path, flags, *args, **kwargs)

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
                patcher.setattr(conventions, "_SYSCALL", None)
                patcher.setattr(os, "open", move_before_leaf)
                assert conventions._read_regular_text(source, scan_root=root) is None
        finally:
            if moved.exists():
                (nested / "api.py").unlink()
                nested.rmdir()
                (moved / "api.py").write_text("def inside_name(): pass\n")
                moved.rename(nested)
        assert swaps == [1]
        assert "inside_name" in get_same_repo_digest(root)

    def test_portable_open_rejects_root_moved_before_leaf_open(self, tmp_path, monkeypatch):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "src" / "sub"
        nested.mkdir(parents=True)
        source = nested / "api.py"
        source.write_text("def inside_name(): pass\n")
        moved = tmp_path / "outside"
        original_open = os.open
        swaps = []

        def move_root_before_leaf(path, flags, *args, **kwargs):
            if path == "api.py" and kwargs.get("dir_fd") is not None and not swaps:
                root.rename(moved)
                (moved / "src" / "sub" / "api.py").write_text(
                    "def outside_name(): pass\n"
                )
                swaps.append(1)
            return original_open(path, flags, *args, **kwargs)

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
                patcher.setattr(conventions, "_SYSCALL", None)
                patcher.setattr(os, "open", move_root_before_leaf)
                assert conventions._read_regular_text(source, scan_root=root) is None
        finally:
            if moved.exists():
                (moved / "src" / "sub" / "api.py").write_text(
                    "def inside_name(): pass\n"
                )
                moved.rename(root)
        assert swaps == [1]

    def test_portable_open_rejects_deleted_leaf_handle(self, tmp_path, monkeypatch):
        from code_forge import conventions

        root = tmp_path / "repo"
        source = root / "src" / "sub" / "api.py"
        source.parent.mkdir(parents=True)
        source.write_text("def deleted_name(): pass\n")
        original_open = os.open
        deletions = []

        def delete_after_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == "api.py" and kwargs.get("dir_fd") is not None and not deletions:
                source.unlink()
                source.parent.rmdir()
                deletions.append(1)
            return fd

        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
            patcher.setattr(conventions, "_SYSCALL", None)
            patcher.setattr(os, "open", delete_after_open)
            assert conventions._read_regular_text(source, scan_root=root) is None
        assert deletions == [1]

    def test_portable_open_rejects_stale_reported_leaf_path(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        source = root / "src" / "api.py"
        source.parent.mkdir(parents=True)
        source.write_text("def original_name(): pass\n")
        original_open = os.open
        replacements = []

        def replace_after_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == "api.py" and kwargs.get("dir_fd") is not None and not replacements:
                source.unlink()
                source.write_text("def replacement_name(): pass\n")
                replacements.append(1)
            return fd

        def stale_fd_path(fd):
            return root if stat.S_ISDIR(os.fstat(fd).st_mode) else source

        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
            patcher.setattr(conventions, "_SYSCALL", None)
            patcher.setattr(conventions, "_opened_posix_fd_path", stale_fd_path)
            patcher.setattr(os, "open", replace_after_open)
            assert conventions._read_regular_text(source, scan_root=root) is None
        assert replacements == [1]
        assert "replacement_name" in get_same_repo_digest(root)

    def test_portable_nested_open_fails_closed_without_fd_paths(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "src" / "sub" / "api.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("def nested_name(): pass\n")
        direct = root / "top.py"
        direct.write_text("def direct_name(): pass\n")

        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
            patcher.setattr(conventions, "_SYSCALL", None)
            patcher.setattr(conventions, "_opened_posix_fd_path", lambda _fd: None)
            assert conventions._read_regular_text(nested, scan_root=root) is None
            assert conventions._read_regular_text(direct, scan_root=root) == (
                "def direct_name(): pass\n"
            )

    @pytest.mark.skipif(
        sys.platform not in ("linux", "darwin"), reason="requires final fd paths",
    )
    def test_portable_clean_nested_source_keeps_name(self, tmp_path, monkeypatch):
        from code_forge import conventions

        root = tmp_path / "repo"
        source = root / "src" / "sub" / "api.py"
        source.parent.mkdir(parents=True)
        source.write_text("def nested_name(): pass\n")
        monkeypatch.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
        monkeypatch.setattr(conventions, "_SYSCALL", None)
        assert "nested_name" in get_same_repo_digest(root)

    def test_darwin_fd_path_decoding_is_bounded(self, monkeypatch):
        fcntl = pytest.importorskip("fcntl")
        from code_forge import conventions

        monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)
        monkeypatch.setattr(
            fcntl, "fcntl", lambda _fd, _command, _buffer: b"/tmp/api.py\0".ljust(1024, b"\0"),
        )
        assert conventions._darwin_opened_fd_path(7) == Path("/tmp/api.py")
        with monkeypatch.context() as patcher:
            patcher.setattr(sys, "platform", "darwin")
            assert conventions._opened_posix_fd_path(7) == Path("/tmp/api.py")
        monkeypatch.setattr(fcntl, "fcntl", lambda _fd, _command, _buffer: b"x" * 1024)
        assert conventions._darwin_opened_fd_path(7) is None
        monkeypatch.setattr(
            fcntl, "fcntl", lambda _fd, _command, _buffer: b"relative.py\0".ljust(1024, b"\0"),
        )
        assert conventions._darwin_opened_fd_path(7) is None

        def unavailable(*_args):
            raise OSError(errno.ENOTSUP, "F_GETPATH unavailable")

        monkeypatch.setattr(fcntl, "fcntl", unavailable)
        assert conventions._darwin_opened_fd_path(7) is None
        monkeypatch.delattr(fcntl, "F_GETPATH")
        assert conventions._darwin_opened_fd_path(7) is None

    @pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="requires fd-path probe")
    def test_unbound_handle_path_fallback_keeps_names_and_rejects_outside(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        source = root / "src" / "api.py"
        source.parent.mkdir(parents=True)
        source.write_text("def inside_name(): pass\n")
        outside = tmp_path / "outside.py"
        outside.write_text("def outside_name(): pass\n")
        monkeypatch.setattr(conventions, "_open_scan_root", lambda _root: None)
        monkeypatch.setattr(conventions, "_path_fallback_supported", lambda: True)
        monkeypatch.setattr(
            conventions, "_opened_file_path",
            lambda fd: Path(os.readlink(f"/proc/self/fd/{fd}")),
        )
        assert "inside_name" in get_same_repo_digest(root)

        original_open = os.open
        backup = source.with_suffix(".saved")
        swaps = []

        def swap_leaf(path, flags, *args, **kwargs):
            if path == source and not swaps:
                source.rename(backup)
                source.symlink_to(outside)
                try:
                    fd = original_open(path, flags, *args, **kwargs)
                finally:
                    source.unlink()
                    backup.rename(source)
                swaps.append(1)
                return fd
            return original_open(path, flags, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "open", swap_leaf)
            assert conventions._read_regular_text(source, scan_root=root) is None
        assert swaps == [1]
        assert "outside_name" not in get_same_repo_digest(root)

    def test_walk_marks_visited_child_with_changed_inode_incomplete(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "root"
        child = root / "sub"
        other = tmp_path / "other"
        child.mkdir(parents=True)
        other.mkdir()
        (child / "api.py").write_text("def selected_name(): pass\n")
        original_fwalk = os.fwalk

        def wrong_child_fd(*args, **kwargs):
            for dirpath, dirs, files, fd in original_fwalk(*args, **kwargs):
                if Path(dirpath).name != "sub":
                    yield dirpath, dirs, files, fd
                    continue
                other_fd = os.open(other, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    yield dirpath, dirs, files, other_fd
                finally:
                    os.close(other_fd)

        root_fd = conventions._open_scan_root(root)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os, "fwalk", wrong_child_fd)
                rows = list(conventions._walk_source(root, root, root_fd, status))
        finally:
            os.close(root_fd)
        assert len(rows) == 1
        assert status.complete is False


class TestExtractPythonPublicNames:
    def test_walk_rejects_root_move_restore_during_yield(self, tmp_path):
        from code_forge import conventions

        root = tmp_path / "repo"
        root.mkdir()
        (root / "api.py").write_text("def Selected(): pass\n")
        parked = tmp_path / "parked"
        root_fd = conventions._open_scan_root(root)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            walk = conventions._walk_source(root, root, root_fd, status)
            first = next(walk)
            assert "api.py" in first[2]
            root.rename(parked)
            parked.rename(root)
            assert list(walk) == []
        finally:
            os.close(root_fd)
        assert status.complete is False

    def test_walk_rejects_root_move_after_last_yield(self, tmp_path, monkeypatch):
        from code_forge import conventions

        root = tmp_path / "repo"
        root.mkdir()
        (root / "api.py").write_text("def Selected(): pass\n")
        parked = tmp_path / "parked"
        original_fwalk = os.fwalk
        moves = []

        def move_at_walk_end(*args, **kwargs):
            yield from original_fwalk(*args, **kwargs)
            root.rename(parked)
            parked.rename(root)
            moves.append(True)

        root_fd = conventions._open_scan_root(root)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os, "fwalk", move_at_walk_end)
                list(conventions._walk_source(root, root, root_fd, status))
        finally:
            os.close(root_fd)
        assert moves == [True]
        assert status.complete is False

    def test_walk_rejects_child_move_restore_after_parent_yield(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "sub" / "nested"
        parked = root / "sub" / "node_modules" / "nested"
        nested.mkdir(parents=True)
        parked.parent.mkdir()
        (nested / "api.py").write_text("def Selected(): pass\n")
        original_fwalk = os.fwalk
        moves = []

        def move_between_parent_and_child(*args, **kwargs):
            for dirpath, dirs, files, fd in original_fwalk(*args, **kwargs):
                yield dirpath, dirs, files, fd
                if Path(dirpath).name == "sub" and not moves:
                    nested.rename(parked)
                    parked.rename(nested)
                    moves.append(True)

        root_fd = conventions._open_scan_root(root)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os, "fwalk", move_between_parent_and_child)
                list(conventions._walk_source(root, root, root_fd, status))
        finally:
            os.close(root_fd)
        assert moves == [True]
        assert status.complete is False

    def test_walk_rejects_nested_move_restore_during_yield(self, tmp_path):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "sub" / "nested"
        nested.mkdir(parents=True)
        (nested / "api.py").write_text("def Selected(): pass\n")
        parked = tmp_path / "parked"
        root_fd = conventions._open_scan_root(root)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            walk = conventions._walk_source(root, root, root_fd, status)
            for path, _dirs, files in walk:
                if path == str(nested):
                    assert "api.py" in files
                    nested.rename(parked)
                    parked.rename(nested)
            assert status.complete is False
        finally:
            os.close(root_fd)

    def test_listed_file_disappearing_before_roster_stat_marks_incomplete(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        root.mkdir()
        source = root / "api.py"
        source.write_text("def Selected(): pass\n")
        hidden = tmp_path / "api.saved"
        original_fwalk = os.fwalk
        moves = []

        def hide_after_listing(*args, **kwargs):
            for dirpath, dirs, files, fd in original_fwalk(*args, **kwargs):
                if "api.py" in files and not moves:
                    source.rename(hidden)
                    moves.append(1)
                yield dirpath, dirs, files, fd

        root_fd = conventions._open_scan_root(root)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os, "fwalk", hide_after_listing)
                rows = list(conventions._walk_source(root, root, root_fd, status))
        finally:
            os.close(root_fd)
            if hidden.exists():
                hidden.rename(source)
        # The opened directory changed before its row was admitted.
        assert moves == [1] and rows == []
        assert status.complete is False

    def test_file_replaced_during_walk_marks_incomplete(self, tmp_path):
        from code_forge import conventions

        source = tmp_path / "api.py"
        source.write_text("def Selected(): pass\n")
        hidden = tmp_path / "api.saved"
        root_fd = conventions._open_scan_root(tmp_path)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            walk = conventions._walk_source(tmp_path, tmp_path, root_fd, status)
            first = next(walk)
            assert "api.py" in first[2]
            source.rename(hidden)
            source.write_text("def Replacement(): pass\n")
            assert list(walk) == []
        finally:
            os.close(root_fd)
        assert status.complete is False

    def test_file_roster_limit_marks_walk_incomplete(self, tmp_path, monkeypatch):
        from code_forge import conventions

        (tmp_path / "one.py").write_text("def One(): pass\n")
        (tmp_path / "two.py").write_text("def Two(): pass\n")
        root_fd = conventions._open_scan_root(tmp_path)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(conventions, "_MAX_ROSTER_FILES", 1)
                rows = list(conventions._walk_source(tmp_path, tmp_path, root_fd, status))
        finally:
            os.close(root_fd)
        assert len(rows) == 1
        assert status.complete is False

    def test_shared_walk_and_python_helper_keep_tuple_shapes(self, tmp_path):
        from code_forge import conventions

        nested = tmp_path / "src" / "sub"
        nested.mkdir(parents=True)
        (nested / "api.py").write_text("def NestedName(): pass\n")
        root_fd = conventions._open_scan_root(tmp_path)
        assert root_fd is not None
        try:
            rows = list(conventions._walk_source(tmp_path / "src", tmp_path, root_fd, None))
            names = conventions._extract_python_public_names(
                tmp_path, scan_root=tmp_path, scan_root_fd=root_fd,
            )
        finally:
            os.close(root_fd)
        assert len(rows) == 2
        assert all(isinstance(row, tuple) and len(row) == 3 for row in rows)
        assert isinstance(names, tuple) and len(names) == 2
        assert names == (["NestedName"], [])

    def test_observed_src_symlink_target_stat_failure_marks_incomplete(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        actual = tmp_path / "actual"
        actual.mkdir()
        (actual / "api.py").write_text("def linked_src_name(): pass\n")
        (tmp_path / "src").symlink_to(actual, target_is_directory=True)
        original_stat = os.stat
        failures = []

        def fail_target_stat(path, *args, **kwargs):
            if path == Path("actual") and kwargs.get("dir_fd") is not None and not failures:
                failures.append(1)
                raise FileNotFoundError(errno.ENOENT, "target moved", str(path))
            return original_stat(path, *args, **kwargs)

        root_fd = conventions._open_scan_root(tmp_path)
        assert root_fd is not None
        status = conventions._ScanStatus()
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os, "stat", fail_target_stat)
                patcher.setattr(conventions, "_walk_source", lambda *_args: iter(()))
                conventions._extract_python_public_names(
                    tmp_path, scan_status=status, scan_root=tmp_path, scan_root_fd=root_fd,
                )
        finally:
            os.close(root_fd)
        assert failures == [1]
        assert status.complete is False

    @pytest.mark.parametrize("failure", ["stat", "realpath", "walk", "open", "no-follow"])
    def test_access_failure_marks_scan_incomplete(self, tmp_path, monkeypatch, failure):
        from code_forge import conventions

        src = tmp_path / "src"
        src.mkdir()
        source = src / "api.py"
        source.write_text("def ordinary_name(): pass\n")
        status = conventions._ScanStatus()

        with monkeypatch.context() as patcher:
            if failure == "stat":
                real_stat = Path.stat

                def fail_stat(path, *args, **kwargs):
                    if path == source:
                        raise OSError(errno.EAGAIN, "temporary stat failure")
                    return real_stat(path, *args, **kwargs)

                patcher.setattr(Path, "stat", fail_stat)
            elif failure == "realpath":
                real_realpath = os.path.realpath

                def fail_realpath(path, *args, **kwargs):
                    if str(path) == str(source):
                        raise OSError(errno.EAGAIN, "temporary path resolution failure")
                    return real_realpath(path, *args, **kwargs)

                patcher.setattr(os.path, "realpath", fail_realpath)
            elif failure == "walk":
                real_scandir = os.scandir

                def fail_scandir(path):
                    if str(path) == str(src):
                        raise OSError(errno.EAGAIN, "temporary directory failure")
                    return real_scandir(path)

                patcher.setattr(os, "scandir", fail_scandir)
            elif failure == "open":
                real_open = conventions._open_beneath

                def fail_open(directory_fd, relative, flags):
                    if Path(relative).name == source.name:
                        raise OSError(errno.EAGAIN, "temporary leaf failure")
                    return real_open(directory_fd, relative, flags)

                patcher.setattr(conventions, "_open_beneath", fail_open)
            else:
                patcher.delattr(os, "O_NOFOLLOW", raising=False)

            assert conventions._extract_python_public_names(tmp_path, scan_status=status) == ([], [])
        assert status.complete is False
        assert conventions._extract_python_public_names(tmp_path) == (["ordinary_name"], [])

    def test_extracts_async_functions(self, tmp_path):
        """M-04: async def must be collected same as def."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "async_mod.py").write_text("async def fetch_data(): pass\ndef sync_fn(): pass\n")
        funcs, classes = _extract_python_public_names(tmp_path)
        assert "fetch_data" in funcs
        assert "sync_fn" in funcs

    def test_top_level_only_not_nested(self, tmp_path):
        """M-05: tree.body iteration must NOT pick up nested functions."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "nested.py").write_text("def outer():\n    def inner(): pass\n")
        funcs, _ = _extract_python_public_names(tmp_path)
        assert "outer" in funcs
        assert "inner" not in funcs

    def test_skips_large_files(self, tmp_path):
        """M-06: files >100KB must be skipped entirely."""
        src = tmp_path / "src"
        src.mkdir()
        # ~120KB
        (src / "big.py").write_text("x = 1\n" * 20000)
        funcs, classes = _extract_python_public_names(tmp_path)
        assert funcs == []
        assert classes == []

    def test_skips_venv_and_tox_dirs(self, tmp_path):
        """L-R2-04: _SKIP_DIRS must be pruned inside src/ scan root.

        Skip dirs are created INSIDE src/ (M-R3-01) so that they are under
        the actual scan root. Since src/ exists (from real.py), the scanner
        uses src/ as its root. Skip dirs at the tmp_path level would be
        never visited and the test would pass vacuously.
        """
        src = tmp_path / "src"
        src.mkdir()
        # Real module -- must be found
        (src / "real.py").write_text("def real_func(): pass\n")
        # Skip dirs inside src/ (M-R3-01)
        venv_dir = src / "venv"
        venv_dir.mkdir()
        (venv_dir / "bad.py").write_text("def venv_func(): pass\n")
        tox_dir = src / ".tox" / "env"
        tox_dir.mkdir(parents=True)
        (tox_dir / "toxmod.py").write_text("def tox_func(): pass\n")
        build_dir = src / "build"
        build_dir.mkdir()
        (build_dir / "out.py").write_text("def build_func(): pass\n")

        funcs, _ = _extract_python_public_names(tmp_path)
        assert "real_func" in funcs
        assert "venv_func" not in funcs
        assert "tox_func" not in funcs
        assert "build_func" not in funcs

    def test_symlink_prefix_collision_rejected(self, tmp_path):
        """M-R5-01 / H-R5-01: Path.parents must reject prefix-collision paths.

        Regression test for the str.startswith path traversal bug where
        "/tmp/repo_evil/x.py".startswith("/tmp/repo") incorrectly returns True.

        A symlink inside repo/src/ pointing to repo_evil/src/mod.py must be
        rejected by the Path.parents containment check, NOT accepted by a
        str.startswith check.
        """
        repo = tmp_path / "repo"
        repo_evil = tmp_path / "repo_evil"
        (repo / "src").mkdir(parents=True)
        (repo_evil / "src").mkdir(parents=True)

        # evil module in the sibling repo
        evil_mod = repo_evil / "src" / "mod.py"
        evil_mod.write_text("def evil_func(): pass\n")

        # symlink inside the legitimate repo pointing to evil file
        symlink_path = repo / "src" / "linked.py"
        os.symlink(str(evil_mod), str(symlink_path))

        funcs, _ = _extract_python_public_names(repo)
        # The symlink traversal guard must reject this file
        assert "evil_func" not in funcs


class TestRegularFileDiscovery:
    @staticmethod
    def _repos(tmp_path, language):
        import json

        cwd = tmp_path / "repo"
        (cwd / "src").mkdir(parents=True)
        (cwd / "src" / "ordinary.py").write_text("def ordinary_local(): pass\n")
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        extensions = {"python": ".py", "js": ".ts", "go": ".go", "rust": ".rs"}
        contents = {
            "python": "def ordinary_sibling(): pass\n",
            "js": "export function ordinary_sibling() {}\n",
            "go": "func OrdinarySibling() {}\n",
            "rust": "pub fn ordinary_sibling() {}\n",
        }
        (sibling / ("ordinary" + extensions[language])).write_text(contents[language])
        (cwd / ".code-forge").mkdir()
        (cwd / ".code-forge" / "conventions.yaml").write_text(
            json.dumps({"siblings": ["../sibling"]})
        )
        return cwd, sibling, extensions[language]

    @pytest.mark.parametrize("language", ["python", "js", "go", "rust"])
    def test_public_digest_ordinary_files(self, tmp_path, language):
        cwd, _, _ = self._repos(tmp_path, language)
        digest = get_digest(cwd)
        assert "ordinary_local" in digest
        assert ("OrdinarySibling" if language == "go" else "ordinary_sibling") in digest

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
    @pytest.mark.parametrize("language", ["python", "js", "go", "rust"])
    def test_public_digest_skips_fifo(self, tmp_path, language):
        import json
        import stat

        cwd, sibling, extension = self._repos(tmp_path, language)
        fifo = sibling / ("blocked" + extension)
        os.mkfifo(fifo)
        before = fifo.stat()
        print(json.dumps({"fifo": str(fifo), "inode": before.st_ino,
                          "mode": before.st_mode, "language": language}), flush=True)
        digest = get_digest(cwd)
        assert "ordinary_local" in digest
        assert ("OrdinarySibling" if language == "go" else "ordinary_sibling") in digest
        after = fifo.stat()
        assert stat.S_ISFIFO(after.st_mode)
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


    @pytest.mark.parametrize("language", ["python", "js", "go", "rust"])
    def test_public_digest_checks_opened_file_size(self, tmp_path, monkeypatch, language):
        from code_forge import conventions

        cwd, sibling, extension = self._repos(tmp_path, language)
        changed = sibling / ("growing" + extension)
        declarations = {"python": "def oversized_name(): pass\n",
                        "js": "export function oversized_name() {}\n",
                        "go": "func OversizedName() {}\n",
                        "rust": "pub fn oversized_name() {}\n"}
        changed.write_text(declarations[language])
        original_open = conventions._open_beneath
        replacements = []

        def grow_before_open(directory_fd, relative, flags):
            if Path(relative).name == changed.name and not replacements:
                changed.write_text(declarations[language] + " " * 100_001)
                replacements.append(changed.stat().st_size)
            return original_open(directory_fd, relative, flags)

        monkeypatch.setattr(conventions, "_open_beneath", grow_before_open)
        digest = get_digest(cwd)
        assert replacements and replacements[0] > 100_000
        assert "ordinary_local" in digest
        assert ("OrdinarySibling" if language == "go" else "ordinary_sibling") in digest
        assert "oversized_name" not in digest and "OversizedName" not in digest


class TestRootBoundScans:
    def test_ancestor_moved_outside_before_leaf_open_omits_outside_inode(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        root = tmp_path / "repo"
        nested = root / "src" / "nested"
        nested.mkdir(parents=True)
        victim = nested / "victim.py"
        victim.write_text("def original_inside_name(): pass\n")
        outside = tmp_path / "outside.py"
        outside.write_text("def outside_only_name(): pass\n")
        moved = tmp_path / "moved_outside"
        real_open = conventions._open_beneath
        swaps = []

        def move_before_leaf_open(directory_fd, relative, flags):
            if Path(relative).name == victim.name and not swaps:
                nested.rename(moved)
                outside.replace(moved / victim.name)
                swaps.append(moved)
            return real_open(directory_fd, relative, flags)

        monkeypatch.setattr(conventions, "_open_beneath", move_before_leaf_open)
        digest = get_same_repo_digest(root)
        assert swaps == [moved]
        assert "outside_only_name" not in digest

    @pytest.mark.parametrize("missing", ["no-follow", "dir-fd"])
    def test_missing_secure_rooted_open_capability_fails_closed(
        self, tmp_path, monkeypatch, missing
    ):
        from code_forge import conventions

        root = tmp_path / "root"
        source = root / "src" / "victim.py"
        source.parent.mkdir(parents=True)
        source.write_text("def inside_name(): pass\n")
        if missing == "no-follow":
            monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
        else:
            monkeypatch.setattr(conventions, "_HAS_DIR_FD_OPEN", False)

        real_open = os.open
        source_opens = []

        def track_source_open(path, flags, *args, **kwargs):
            if Path(path).name == source.name:
                source_opens.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", track_source_open)
        assert conventions._read_regular_text(source, scan_root=root) is None
        assert conventions.get_same_repo_digest(root) == ""
        assert source_opens == []

    @pytest.mark.parametrize("language", ["python", "js", "go", "rust"])
    def test_missing_no_follow_capability_omits_all_language_names(
        self, tmp_path, monkeypatch, language
    ):
        cwd, _sibling, _extension = TestRegularFileDiscovery._repos(tmp_path, language)
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
        digest = get_digest(cwd)
        assert "ordinary_local" not in digest
        assert "ordinary_sibling" not in digest
        assert "OrdinarySibling" not in digest

    @pytest.mark.parametrize("case", ["same-python", "sibling-python", "js", "go", "rust"])
    def test_symlink_swap_does_not_include_outside_names(self, tmp_path, monkeypatch, case):
        from code_forge import conventions

        language = "python" if case.endswith("python") else case
        cwd, sibling, extension = TestRegularFileDiscovery._repos(tmp_path, language)
        scan_dir = cwd / "src" if case == "same-python" else sibling
        victim = scan_dir / ("swap_target" + extension)
        outside = tmp_path / ("outside" + extension)
        declarations = {
            "python": "def {name}(): pass\n",
            "js": "export function {name}() {{}}\n",
            "go": "func {name}() {{}}\n",
            "rust": "pub fn {name}() {{}}\n",
        }
        outside_name = "OutsideName" if language == "go" else "outside_name"
        inside_name = "InsideName" if language == "go" else "inside_name"
        victim.write_text(declarations[language].format(name=inside_name))
        outside.write_text(declarations[language].format(name=outside_name))
        actual_open = conventions._open_beneath
        swaps = []

        def swap_before_open(directory_fd, relative, flags):
            if Path(relative).name == victim.name and not swaps:
                victim.unlink()
                victim.symlink_to(outside)
                swaps.append(victim)
            return actual_open(directory_fd, relative, flags)

        monkeypatch.setattr(conventions, "_open_beneath", swap_before_open)
        digest = get_digest(cwd)
        assert len(swaps) == 1
        assert outside_name not in digest
        assert "ordinary_local" in digest

    @pytest.mark.parametrize("case", ["same-python", "sibling-python", "js", "go", "rust"])
    def test_in_root_symlink_keeps_names(self, tmp_path, monkeypatch, case):
        language = "python" if case.endswith("python") else case
        cwd, sibling, extension = TestRegularFileDiscovery._repos(tmp_path, language)
        scan_dir = cwd / "src" if case == "same-python" else sibling
        hidden = scan_dir / "venv"
        hidden.mkdir()
        actual = hidden / ("actual" + extension)
        safe_name = "SafeName" if language == "go" else "safe_name"
        declarations = {
            "python": "def {name}(): pass\n",
            "js": "export function {name}() {{}}\n",
            "go": "func {name}() {{}}\n",
            "rust": "pub fn {name}() {{}}\n",
        }
        actual.write_text(declarations[language].format(name=safe_name))
        (scan_dir / ("alias" + extension)).symlink_to(actual)

        assert safe_name in get_digest(cwd)

    def test_directory_swap_does_not_include_outside_names(self, tmp_path, monkeypatch):
        from code_forge import conventions

        cwd, sibling, _ = TestRegularFileDiscovery._repos(tmp_path, "js")
        nested = sibling / "nested"
        nested.mkdir()
        victim = nested / "victim.ts"
        victim.write_text("export function inside_name() {}\n")
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / victim.name).write_text("export function outside_name() {}\n")
        actual_open = conventions._open_beneath
        swaps = []

        def swap_directory_before_open(directory_fd, relative, flags):
            if Path(relative).name == victim.name and not swaps:
                nested.rename(sibling / "moved")
                nested.symlink_to(outside_dir, target_is_directory=True)
                swaps.append(nested)
            return actual_open(directory_fd, relative, flags)

        monkeypatch.setattr(conventions, "_open_beneath", swap_directory_before_open)
        digest = get_digest(cwd)
        assert len(swaps) == 1
        assert "outside_name" not in digest
        assert "ordinary_local" in digest

@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="requires POSIX permissions")
class TestSearchOnlyAncestors:
    @pytest.mark.parametrize("case", ["same-python", "cross-python", "js", "go", "rust"])
    def test_public_digest_reads_beneath_search_only_ancestor(
        self, tmp_path, case
    ):
        language = "python" if case.endswith("python") else case
        ancestor = tmp_path / "search_only"
        ancestor.mkdir()
        cwd, sibling, extension = TestRegularFileDiscovery._repos(ancestor, language)
        local_file = cwd / "src" / "ordinary.py"
        sibling_file = sibling / ("ordinary" + extension)
        ancestor.chmod(0o111)
        try:
            assert local_file.read_text() == "def ordinary_local(): pass\n"
            assert sibling_file.read_text()
            if case == "same-python":
                assert "ordinary_local" in get_same_repo_digest(cwd)
            else:
                digest = get_digest(cwd)
                expected = "OrdinarySibling" if case == "go" else "ordinary_sibling"
                assert "ordinary_local" in digest
                assert expected in digest
        finally:
            ancestor.chmod(0o700)

    @pytest.mark.skipif(not hasattr(os, "O_PATH"), reason="requires O_PATH")
    def test_search_only_directory_uses_path_descriptor(self, tmp_path, monkeypatch):
        from code_forge import conventions
        from code_forge.conventions import _read_regular_text

        ancestor = tmp_path / "search_only"
        root = ancestor / "repo"
        root.mkdir(parents=True)
        source = root / "source.py"
        source.write_text("visible_name")
        actual_open = conventions._open_beneath
        ancestor_flags = []

        def track_directory_open(directory_fd, relative, flags):
            if Path(relative).name == root.name:
                ancestor_flags.append((flags, directory_fd))
            return actual_open(directory_fd, relative, flags)

        monkeypatch.setattr(conventions, "_open_beneath", track_directory_open)
        ancestor.chmod(0o111)
        try:
            assert _read_regular_text(source, scan_root=root) == "visible_name"
            assert len(ancestor_flags) == 1
            flags, parent_fd = ancestor_flags[0]
            assert parent_fd is not None
            assert flags & os.O_PATH
            assert flags & os.O_DIRECTORY
            assert flags & os.O_NOFOLLOW
        finally:
            ancestor.chmod(0o700)


class TestRegularTextReader:
    def test_newlines_and_utf8_policy(self, tmp_path):
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "text"
        path.write_bytes(b"a\r\nb\rc\xff\n")
        assert _read_regular_text(path) == "a\nb\nc\ufffd\n"
        assert _read_regular_text(path, max_bytes=8) == "a\nb\nc\ufffd\n"
        with pytest.raises(UnicodeDecodeError):
            _read_regular_text(path, errors="strict")
        with pytest.raises(UnicodeDecodeError):
            _read_regular_text(path, max_bytes=8, errors="strict")

    def test_descriptor_size_boundary(self, tmp_path):
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "text"
        path.write_bytes(b"x" * 100_000)
        assert len(_read_regular_text(path, max_bytes=100_000)) == 100_000
        path.write_bytes(b"x" * 100_001)
        assert _read_regular_text(path, max_bytes=100_000) is None

    def test_growth_after_fstat_respects_byte_limit(self, tmp_path, monkeypatch):
        import errno
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "text"
        path.write_bytes(b"x")
        actual_fstat = os.fstat
        actual_open = os.open
        descriptors = []
        sizes = []

        def tracked_open(*args, **kwargs):
            fd = actual_open(*args, **kwargs)
            descriptors.append(fd)
            return fd

        def grow_after_fstat(fd):
            info = actual_fstat(fd)
            if not sizes:
                path.write_bytes(b"x" * 100_001)
                sizes.append((info.st_size, path.stat().st_size))
            return info

        monkeypatch.setattr(os, "open", tracked_open)
        monkeypatch.setattr(os, "fstat", grow_after_fstat)
        assert _read_regular_text(path, max_bytes=100_000) is None
        assert sizes == [(1, 100_001)]
        assert len(descriptors) == 1
        with pytest.raises(OSError) as error:
            os.close(descriptors[0])
        assert error.value.errno == errno.EBADF

    def test_bounded_read_handles_short_reads_and_utf8_bytes(self, tmp_path, monkeypatch):
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "text"
        content = b"a\r\nb\rc\xc3\xa9\n"
        path.write_bytes(content)
        actual_read = os.read
        requests = []

        def short_read(fd, count):
            requests.append(count)
            return actual_read(fd, min(count, 2))

        monkeypatch.setattr(os, "read", short_read)
        assert _read_regular_text(path, max_bytes=len(content)) == "a\nb\nc\xe9\n"
        assert requests and max(requests) <= len(content) + 1

        path.write_bytes("\xe9".encode("utf-8") * 3)
        assert _read_regular_text(path, max_bytes=5) is None

    def test_bounded_read_error_closes_descriptor(self, tmp_path, monkeypatch):
        import errno
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "text"
        path.write_bytes(b"ordinary")
        actual_open = os.open
        descriptors = []

        def tracked_open(*args, **kwargs):
            fd = actual_open(*args, **kwargs)
            descriptors.append(fd)
            return fd

        def broken_read(fd, count):
            raise OSError(errno.EIO, "read failed")

        monkeypatch.setattr(os, "open", tracked_open)
        monkeypatch.setattr(os, "read", broken_read)
        assert _read_regular_text(path, max_bytes=100_000) is None
        assert len(descriptors) == 1
        with pytest.raises(OSError) as error:
            os.close(descriptors[0])
        assert error.value.errno == errno.EBADF

    @pytest.mark.parametrize("branch", ["regular", "oversize",
                                       pytest.param("directory", marks=pytest.mark.skipif(
                                           os.name == "nt", reason="requires directory descriptors")),
                                       "decode_error", "fstat_error", "stream_error"])
    def test_descriptor_closes_on_every_branch(self, tmp_path, monkeypatch, branch):
        import errno
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "input"
        if branch == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"\xff" if branch == "decode_error" else b"ordinary")
        actual_open = os.open
        captured = []

        def tracked_open(*args, **kwargs):
            fd = actual_open(*args, **kwargs)
            captured.append(fd)
            return fd

        monkeypatch.setattr(os, "open", tracked_open)
        if branch == "fstat_error":
            def broken_fstat(fd):
                raise OSError(errno.EIO, "unreadable descriptor")
            monkeypatch.setattr(os, "fstat", broken_fstat)
        if branch == "stream_error":
            def broken_stream(*args, **kwargs):
                raise RuntimeError("unexpected stream bug")
            monkeypatch.setattr(os, "fdopen", broken_stream)
        if branch == "decode_error":
            with pytest.raises(UnicodeDecodeError):
                _read_regular_text(path, errors="strict")
        elif branch == "stream_error":
            with pytest.raises(RuntimeError, match="unexpected stream bug"):
                _read_regular_text(path)
        elif branch == "regular":
            assert _read_regular_text(path) == "ordinary"
        else:
            assert _read_regular_text(path, max_bytes=1 if branch == "oversize" else None) is None
        assert len(captured) == 1
        with pytest.raises(OSError) as error:
            os.close(captured[0])
        assert error.value.errno == errno.EBADF

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
    def test_fifo_is_rejected_and_descriptor_closed(self, tmp_path, monkeypatch):
        import errno
        from code_forge.conventions import _read_regular_text

        fifo = tmp_path / "input"
        os.mkfifo(fifo)
        actual_open = os.open
        captured = []
        def tracked_open(*args, **kwargs):
            fd = actual_open(*args, **kwargs)
            captured.append(fd)
            return fd
        monkeypatch.setattr(os, "open", tracked_open)
        assert _read_regular_text(fifo) is None
        assert len(captured) == 1
        with pytest.raises(OSError) as error:
            os.close(captured[0])
        assert error.value.errno == errno.EBADF

    def test_missing_optional_open_flags(self, tmp_path, monkeypatch):
        from code_forge.conventions import _read_regular_text

        path = tmp_path / "input"
        path.write_text("ordinary")
        monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
        monkeypatch.delattr(os, "O_BINARY", raising=False)
        assert _read_regular_text(path) == "ordinary"

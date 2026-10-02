"""Materialized mutation inputs must match the reviewed bytes."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import ExecutionContext, InputEntry, InputSnapshot
from code_forge.mutation_engines.adapters.patch_corpus import PatchCorpusAdapter
from code_forge.mutation_engines.adapters.python_mutmut import AdapterError, MutmutAdapter
from code_forge.mutation_engines.schemas import Budget, TargetDeclaration
from code_forge.mutation_engines.targets import TargetSelection


@pytest.fixture(params=[MutmutAdapter, PatchCorpusAdapter], ids=["native", "corpus"])
def adapter(request):
    return request.param()


def snapshot(root, path="src/logic.py", data=b"checked\n", mode=0o750):
    source = root / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)
    source.chmod(mode)
    return InputSnapshot(
        reviewed_source_id="reviewed-source",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(root),
        files=(InputEntry(path, hashlib.sha256(data).hexdigest(), mode, None),),
    )


@pytest.mark.parametrize("path", ["src/logic.py", "tests/test_logic.py", "corpus.json"])
def test_materialize_refuses_changed_snapshot_bytes(adapter, tmp_path, path):
    root = tmp_path / "input"
    frozen = snapshot(root, path=path)
    source = root / path
    source.write_bytes(b"unreviewed\n")
    workspace = tmp_path / "workspace"
    with pytest.raises(AdapterError, match="snapshot digest mismatch"):
        adapter._materialize(frozen, workspace)
    assert not (workspace / path).exists()
    assert list((workspace / path).parent.iterdir()) == []
    assert source.read_bytes() == b"unreviewed\n"


@pytest.mark.parametrize("data", [b"", b"checked\n", b"\x00\xff\x80raw\n", b"large" * 40000])
def test_materialize_preserves_checked_content_and_manifest_mode(adapter, tmp_path, data):
    root = tmp_path / "input"
    frozen = snapshot(root, data=data)
    source = root / frozen.files[0].path
    source.chmod(0o600)
    workspace = tmp_path / "workspace"
    adapter._materialize(frozen, workspace)
    copied = workspace / frozen.files[0].path
    assert copied.read_bytes() == data
    assert stat.S_IMODE(copied.stat().st_mode) == frozen.files[0].mode
    assert stat.S_IMODE(source.stat().st_mode) == 0o600


def test_materialize_copies_the_buffer_it_validated(adapter, tmp_path, monkeypatch):
    root = tmp_path / "input"
    frozen = snapshot(root)
    source = root / frozen.files[0].path
    real_open = Path.open
    reads = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, size):
            assert size == 65536
            data = self.handle.read(size)
            if data:
                reads.append(data)
                replacement = source.with_name("replacement.py")
                replacement.write_bytes(b"changed after read\n")
                replacement.replace(source)
            return data

    def open_then_change(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        return Reader(handle) if path == source and args == ("rb",) else handle

    monkeypatch.setattr(Path, "open", open_then_change)
    workspace = tmp_path / "workspace"
    adapter._materialize(frozen, workspace)
    assert reads == [b"checked\n"]
    assert (workspace / frozen.files[0].path).read_bytes() == b"checked\n"
    assert source.read_bytes() == b"changed after read\n"


def test_materialize_cleans_private_copy_when_input_read_fails(adapter, tmp_path):
    root = tmp_path / "input"
    frozen = snapshot(root)
    (root / frozen.files[0].path).unlink()
    workspace = tmp_path / "workspace"
    with pytest.raises(FileNotFoundError):
        adapter._materialize(frozen, workspace)
    assert list((workspace / "src").iterdir()) == []


def test_native_materialize_has_no_second_hash_then_copy_read(tmp_path, monkeypatch):
    from code_forge.mutation_engines.adapters import python_mutmut

    root = tmp_path / "input"
    frozen = snapshot(root)
    source = root / frozen.files[0].path
    real_hash = python_mutmut._sha256_file

    def hash_then_change(path):
        digest = real_hash(path)
        path.write_bytes(b"unreviewed after hash\n")
        return digest

    assert hash_then_change(source) == frozen.files[0].digest
    assert source.read_bytes() == b"unreviewed after hash\n"
    source.write_bytes(b"checked\n")
    monkeypatch.setattr(python_mutmut, "_sha256_file", hash_then_change)
    workspace = tmp_path / "workspace"
    MutmutAdapter()._materialize(frozen, workspace)
    assert (workspace / frozen.files[0].path).read_bytes() == b"checked\n"


def test_declared_link_policies_remain_distinct(adapter, tmp_path):
    root = tmp_path / "input"
    frozen = snapshot(root, path="real.py")
    entry = InputEntry("alias.py", "declared-link", 0o777, "real.py")
    frozen = InputSnapshot(
        frozen.reviewed_source_id,
        frozen.manifest_digest,
        frozen.selection_digest,
        frozen.root,
        frozen.files + (entry,),
    )
    workspace = tmp_path / "workspace"
    adapter._materialize(frozen, workspace)
    alias = workspace / "alias.py"
    if isinstance(adapter, MutmutAdapter):
        assert alias.is_symlink()
        assert os.readlink(alias) == "real.py"
        assert alias.read_bytes() == b"checked\n"
    else:
        assert not alias.exists()
        assert not alias.is_symlink()


def test_corpus_public_run_rejects_snapshot_before_loading_or_executing(tmp_path, monkeypatch):
    root = tmp_path / "input"
    frozen = snapshot(root)
    (root / frozen.files[0].path).write_bytes(b"unreviewed\n")
    target = TargetDeclaration(
        id="snapshot",
        adapter="patch-corpus",
        root=".",
        sources=("src/*.py",),
        tests=("tests",),
        inputs=(),
        oracle="pytest",
        command=(sys.executable, "-m", "pytest"),
        execution_profile="local",
        environment="host",
        corpus="corpus.json",
        budget=Budget(60, 20, 5, 1, 256, 32, 64, 16),
    )
    context = ExecutionContext(
        run_id="snapshot-binding",
        config_digest="c" * 64,
        execution_policy_digest="e" * 64,
        toolchain_fingerprint="test",
        cgroup_root=str(tmp_path / "unused-cgroup"),
        state_root=str(tmp_path / "state"),
        approved_python=sys.executable,
        memory_mb=256,
        pids=32,
        workspace_mb=64,
        process_headroom_mb=32,
    )
    selection = TargetSelection("snapshot", "full", (), {}, ("source-change",))
    calls = []

    def forbid_load(*args, **kwargs):
        calls.append("load")
        raise AssertionError("unverified input reached corpus loading")

    with pytest.raises(AssertionError, match="unverified input reached corpus loading"):
        forbid_load()
    assert calls == ["load"]
    calls.clear()
    monkeypatch.setattr(PatchCorpusAdapter, "_load", forbid_load)
    with pytest.raises(AdapterError, match="snapshot digest mismatch"):
        PatchCorpusAdapter().run(target, selection, frozen, context)
    assert calls == []
    assert (root / frozen.files[0].path).read_bytes() == b"unreviewed\n"

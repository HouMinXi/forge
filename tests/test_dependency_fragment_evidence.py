"""Offline tests for conservative, bounded fragment hash observations."""

import hashlib
import os
import time


from scripts import dependency_fragment_evidence as evidence


def collect(paths, roots, deadline=None):
    return evidence.collect_fragment_hashes(
        [str(path) for path in paths],
        [str(root) for root in roots],
        deadline=time.monotonic() + 10 if deadline is None else deadline,
    )


def test_hashes_only_and_unestablished_markers(tmp_path):
    root = tmp_path / "units"
    root.mkdir()
    a, b = root / "a.service", root / "b.target"
    a.write_bytes(b"secret fragment content")
    b.write_bytes(b"other")
    result = collect([a, b], [root])
    assert result == {
        "status": "HASHED",
        "fragment_sha256": [
            hashlib.sha256(b"secret fragment content").hexdigest(),
            hashlib.sha256(b"other").hexdigest(),
        ],
        "drop_ins": "NOT_ESTABLISHED",
        "precedence": "NOT_ESTABLISHED",
        "manager_disk_equivalence": "NOT_ESTABLISHED",
    }
    assert "secret fragment content" not in repr(result)
    assert str(root) not in repr(result)


def test_unknown_for_missing_escape_and_noncanonical_paths(tmp_path):
    root = tmp_path / "units"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"not for disclosure")
    for candidate in (root / "missing", outside, str(root) + "/../outside", str(root) + "//x"):
        assert collect([candidate], [root]) == evidence._result("UNKNOWN")


def test_symlink_in_file_or_ancestor_or_root_is_unknown(tmp_path):
    root = tmp_path / "units"
    root.mkdir()
    actual = root / "actual"
    actual.write_bytes(b"data")
    (root / "alias").symlink_to(actual)
    (root / "linkdir").symlink_to(root, target_is_directory=True)
    root_link = tmp_path / "rootlink"
    root_link.symlink_to(root, target_is_directory=True)
    for candidate, supplied_root in (
        (root / "alias", root),
        (root / "linkdir" / "actual", root),
        (root_link / "actual", root_link),
    ):
        assert collect([candidate], [supplied_root])["status"] == "UNKNOWN"


def test_non_regular_and_file_size_limits(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    huge = tmp_path / "huge"
    with huge.open("wb") as output:
        output.truncate(evidence.MAX_FILE_BYTES + 1)
    for path in (fifo, tmp_path, huge):
        assert collect([path], [tmp_path])["status"] == "UNKNOWN"


def test_count_aggregate_duplicates_and_deadline(tmp_path):
    paths = []
    for index in range(9):
        path = tmp_path / str(index)
        path.write_bytes(b"x" * evidence.MAX_FILE_BYTES)
        paths.append(path)
    assert collect(paths[:8], [tmp_path])["status"] == "HASHED"
    assert collect(paths, [tmp_path])["status"] == "UNKNOWN"
    assert collect([paths[0]] * 65, [tmp_path])["status"] == "UNKNOWN"
    assert collect([paths[0], paths[0]], [tmp_path])["status"] == "UNKNOWN"
    assert collect([paths[0]], [tmp_path], deadline=time.monotonic() - 1)["status"] == "UNKNOWN"
    assert collect([], [tmp_path])["status"] == "UNKNOWN"


def test_rehash_detects_modification_between_passes(tmp_path, monkeypatch):
    candidate = tmp_path / "unit"
    candidate.write_bytes(b"original")
    real_hash = evidence._hash_path
    calls = 0

    def mutate(parts, deadline):
        nonlocal calls
        calls += 1
        if calls == 2:
            candidate.write_bytes(b"modified")
        return real_hash(parts, deadline)

    monkeypatch.setattr(evidence, "_hash_path", mutate)
    assert collect([candidate], [tmp_path])["status"] == "UNKNOWN"


def test_unsupported_and_bad_inputs_return_fixed_unknown(tmp_path, monkeypatch):
    candidate = tmp_path / "unit"
    candidate.write_bytes(b"x")
    assert evidence.collect_fragment_hashes(
        [str(candidate)], [str(tmp_path)], deadline=float("inf")
    ) == evidence._result("UNKNOWN")
    monkeypatch.setattr(evidence.os, "supports_dir_fd", set())
    assert collect([candidate], [tmp_path]) == evidence._result("UNKNOWN")


def test_errors_are_fail_closed(tmp_path, monkeypatch):
    candidate = tmp_path / "unit"
    candidate.write_bytes(b"x")

    def denied(*_args, **_kwargs):
        raise PermissionError("private details")

    monkeypatch.setattr(evidence, "_hash_path", denied)
    assert collect([candidate], [tmp_path]) == evidence._result("UNKNOWN")


def test_final_sweep_detects_change_to_earlier_rehashed_file(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"original")
    second.write_bytes(b"other")
    real_hash = evidence._hash_path
    calls = 0

    def mutate(parts, deadline):
        nonlocal calls
        calls += 1
        if calls == 4:
            first.write_bytes(b"modified")
        return real_hash(parts, deadline)

    monkeypatch.setattr(evidence, "_hash_path", mutate)
    assert collect([first, second], [tmp_path])["status"] == "UNKNOWN"


def test_fifo_is_inspected_without_opening_it_for_io(tmp_path, monkeypatch):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    real_open = os.open
    opens = []

    def recording_open(path, flags, *args, **kwargs):
        opens.append((path, flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(evidence.os, "open", recording_open)
    # Monkeypatching open changes the identity checked by supports_dir_fd.
    monkeypatch.setattr(evidence.os, "supports_dir_fd", {recording_open})
    assert collect([fifo], [tmp_path])["status"] == "UNKNOWN"
    assert any(path == "fifo" and flags & os.O_PATH for path, flags in opens)
    assert not any(str(path).startswith("/proc/self/fd/") for path, _ in opens)


def test_replacement_between_passes_is_unknown(tmp_path, monkeypatch):
    candidate = tmp_path / "unit"
    candidate.write_bytes(b"same")
    real_hash = evidence._hash_path
    calls = 0

    def replace(parts, deadline):
        nonlocal calls
        calls += 1
        if calls == 2:
            candidate.unlink()
            candidate.write_bytes(b"same")
        return real_hash(parts, deadline)

    monkeypatch.setattr(evidence, "_hash_path", replace)
    assert collect([candidate], [tmp_path])["status"] == "UNKNOWN"

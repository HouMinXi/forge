# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Real overlapping publishers with controlled commit order, not complete hosts."""

from dataclasses import replace
import hashlib
import os
import threading
from uuid import uuid4

import pytest

from code_forge import _advisory_diagnostics as records

_WAIT = 5
_SENTINEL = b"foreign pending inode: preserve me"


class HarnessFailure(AssertionError):
    """Coordination failure, never a valid product unknown or semantic RED."""


def _snapshot(path):
    info = path.stat()
    return info.st_dev, info.st_ino, path.read_bytes()


def _path(binding, context):
    return binding.source_root.joinpath(
        ".code-forge",
        "advisory-diagnostics",
        context.execution_id,
        context.repository_id,
        "CROSS-REPO-IMPACT",
        f"{context.observation_sequence}.json",
    )


class Contention:
    """Hold B's commit until the controller snapshots A's returned publication.

    Barrier/event/join waits are bounded, not filesystem I/O or lock acquisition.
    Original exception objects stay in memory;
    failures exposed to pytest use closed labels, not raw exception/path text.
    Both primitives share scheduling so a source link-to-replace fault remains
    an overwrite control rather than a scheduler timeout.
    """

    def __init__(self, monkeypatch, storage, observations, *, foreign=False, fault=None):
        self.storage = storage
        self.observations = observations
        self.paths = [_path(storage, observation.context) for observation in observations]
        self.foreign = foreign
        self.fault = fault
        self.barrier = threading.Barrier(2)
        self.release = threading.Event()
        self.done = [threading.Event(), threading.Event()]
        self.local = threading.local()
        self.lock = threading.Lock()
        self.errors = []
        self.staged = {}
        self.attempts = [0, 0]
        self.results = [None, None]
        self.sentinel = None
        self.before_peer = None
        link, rename, cleanup = os.link, os.replace, records._cleanup_temp

        def scheduled(operation, source, destination, **kwargs):
            index = self.local.index
            try:
                pending = self.paths[index].with_name(source)
                staged = _snapshot(pending)
                with self.lock:
                    self.attempts[index] += 1
                    self.staged[index] = (pending, staged)
                if self.fault == "barrier" and index == 0:
                    self.barrier.abort()
                self.barrier.wait(timeout=_WAIT)
                if index == 1 and not self.release.wait(timeout=_WAIT):
                    raise HarnessFailure("contender gate timed out")
                if self.fault == "wrapper" and index == 0:
                    raise RuntimeError("injected scheduler failure")
            except BaseException as exc:  # noqa: BLE001 -- Preserve wrapper errors independently of product catches.
                self._error("scheduler failure", exc)
                raise
            # Real commit exceptions (including expected EEXIST) are product inputs.
            return operation(source, destination, **kwargs)

        def scheduled_link(source, destination, **kwargs):
            return scheduled(link, source, destination, **kwargs)

        def scheduled_replace(source, destination, **kwargs):
            return scheduled(rename, source, destination, **kwargs)

        def cleanup_with_foreign_pending(parent, name, identity):
            if foreign and self.local.index == 0:
                try:
                    pending = self.paths[0].with_name(name)
                    # The committed hard link keeps the original inode allocated.
                    assert _snapshot(self.paths[0]) == self.staged[0][1]
                    pending.unlink()
                    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(_SENTINEL)
                    self.sentinel = (pending, _snapshot(pending))
                    assert self.sentinel[1][:2] != self.staged[0][1][:2]
                except BaseException as exc:  # noqa: BLE001 -- Product cleanup catches cannot hide fixture failures.
                    self._error("foreign pending setup failed", exc)
                    raise
            return cleanup(parent, name, identity)

        monkeypatch.setattr(records.os, "link", scheduled_link)
        monkeypatch.setattr(records.os, "replace", scheduled_replace)
        monkeypatch.setattr(records, "_cleanup_temp", cleanup_with_foreign_pending)

    def _error(self, code, exception):
        with self.lock:
            self.errors.append((code, exception))
        self.release.set()
        self.barrier.abort()

    def _worker(self, index):
        self.local.index = index
        try:
            if self.fault == "worker" and index == 0:
                raise SystemExit("injected worker failure")
            self.results[index] = records.publish_observation(self.storage, self.observations[index])
        except BaseException as exc:  # noqa: BLE001 -- Thread exceptions must reach the controller, including signals.
            self._error("worker failure", exc)
        finally:
            self.done[index].set()

    def _check_errors(self):
        if self.errors:
            raise HarnessFailure(self.errors[0][0]) from None

    def run(self):
        # Daemon is an emergency process-exit safeguard, not a successful join.
        self.threads = [threading.Thread(target=self._worker, args=(i,), daemon=True) for i in (0, 1)]
        controller_failed = True
        try:
            for thread in self.threads:
                thread.start()
            if not self.done[0].wait(timeout=_WAIT * 2):
                self._error("winner timed out", TimeoutError())
            self._check_errors()
            self.before_peer = _snapshot(self.paths[0])
            controller_failed = False
        finally:
            self.release.set()
            # Do not abort a healthy barrier while B may still be leaving it.
            if controller_failed:
                self.barrier.abort()
            for thread in self.threads:
                if thread.ident is not None:
                    thread.join(timeout=_WAIT * 2)
            self.barrier.abort()
            if any(thread.is_alive() for thread in self.threads):
                self._error("worker join timed out", TimeoutError())
            self._check_errors()
        assert self.attempts == [1, 1]
        assert set(self.staged) == {0, 1}
        assert self.staged[0][1][:2] != self.staged[1][1][:2]
        for index, observation in enumerate(self.observations):
            assert self.staged[index][1][2] == records._payload(observation)
        return self


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "primary"
    root.mkdir(mode=0o700)
    storage = records.bind_storage(root)
    context = records.ExpectedContext(str(uuid4()), str(uuid4()), "source", "a" * 64, 1)
    return storage, context


def _observations(context, *, same_key=False):
    other = context if same_key else replace(context, execution_id=str(uuid4()))
    return (
        records.make_observation(context, "no_named_targets"),
        records.make_observation(other, "targets_resolved"),
    )


def _assert_published(run, index, *, cleanup="complete"):
    publication = run.results[index]
    observation = run.observations[index]
    path = run.paths[index]
    assert publication.status == "published"
    assert publication.failure is None
    assert publication.cleanup == cleanup
    assert publication.locator is not None
    assert (publication.locator.identity.device, publication.locator.identity.inode) == (
        run.staged[index][1][:2]
    )
    assert _snapshot(path) == run.staged[index][1]
    assert publication.locator.byte_count == len(run.staged[index][1][2])
    assert publication.locator.digest == hashlib.sha256(run.staged[index][1][2]).hexdigest()
    assert records.read_observation(publication, observation.context) == observation
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_nlink == 1
    for parent in list(path.parents)[:5]:
        assert parent.stat().st_mode & 0o777 == 0o700


def _regular_files(storage):
    return {path for path in storage.source_root.rglob("*") if path.is_file()}


def test_concurrent_distinct_execution_publications_remain_independent(inputs, monkeypatch):
    storage, context = inputs
    run = Contention(monkeypatch, storage, _observations(context)).run()
    assert _snapshot(run.paths[0]) == run.before_peer, "winner inode and bytes changed"
    assert run.paths[0] != run.paths[1]
    for index in (0, 1):
        _assert_published(run, index)
        expected = run.observations[index].context
        with pytest.raises(records.DiagnosticError, match="^invalid_locator$"):
            records.read_observation(run.results[index], run.observations[1 - index].context)
        # Keep namespace unchanged, so the decoder guard is the rejecting boundary.
        with pytest.raises(records.DiagnosticError, match="^context_mismatch$"):
            records.read_observation(run.results[index], replace(expected, source_hash="other"))
    assert _regular_files(storage) == set(run.paths)
    assert list(storage.source_root.rglob(".pending-*")) == []
    for path in run.paths:
        assert list(path.parent.iterdir()) == [path]


def test_concurrent_same_key_refuses_contender_without_replacing_winner(inputs, monkeypatch):
    storage, context = inputs
    run = Contention(monkeypatch, storage, _observations(context, same_key=True)).run()
    # Check the held winner before status checks or reopening its old locator.
    assert _snapshot(run.paths[0]) == run.before_peer, "winner inode and bytes changed"
    _assert_published(run, 0)
    contender = run.results[1]
    assert contender.status == "unknown"
    assert contender.failure == "commit_uncertain"
    assert contender.locator is None, "unknown leaked a locator"
    assert contender.cleanup == "complete"
    records.validate_publication(contender, context)
    with pytest.raises(records.DiagnosticError, match="^not_published$"):
        records.read_observation(contender, context)
    assert _regular_files(storage) == {run.paths[0]}
    assert list(storage.source_root.rglob(".pending-*")) == []
    assert list(run.paths[0].parent.iterdir()) == [run.paths[0]]


def test_concurrent_publishers_preserve_foreign_pending_replacement(inputs, monkeypatch):
    storage, context = inputs
    run = Contention(monkeypatch, storage, _observations(context), foreign=True).run()
    pending, retained = run.sentinel
    try:
        # This oracle must precede generic cleanup status or reader assertions.
        assert pending.exists(), "foreign pending sentinel removed"
        assert _snapshot(pending) == retained, "foreign pending sentinel changed"
        assert retained[2] == _SENTINEL
        assert _snapshot(run.paths[0]) == run.before_peer, "winner inode and bytes changed"
        _assert_published(run, 0, cleanup="failed")
        _assert_published(run, 1)
        assert not run.staged[1][0].exists()
        assert _regular_files(storage) == {*run.paths, pending}
        assert list(storage.source_root.rglob(".pending-*")) == [pending]
        assert set(run.paths[0].parent.iterdir()) == {run.paths[0], pending}
        assert list(run.paths[1].parent.iterdir()) == [run.paths[1]]
    finally:
        if pending.exists() and _snapshot(pending) == retained:
            pending.unlink()


@pytest.mark.parametrize("fault", ["wrapper", "worker", "barrier"])
def test_contention_harness_reports_failures_instead_of_product_unknown(inputs, monkeypatch, fault):
    storage, context = inputs
    run = Contention(monkeypatch, storage, _observations(context), fault=fault)
    with pytest.raises(HarnessFailure, match="^(scheduler|worker) failure$"):
        run.run()
    assert run.errors
    assert all(not thread.is_alive() for thread in run.threads)
    if fault == "wrapper":
        # The publisher caught this RuntimeError; its unknown is still invalid evidence.
        assert any(isinstance(exc, RuntimeError) for _, exc in run.errors)
        assert run.results[0].status == "unknown"
        assert run.results[0].failure == "commit_uncertain"

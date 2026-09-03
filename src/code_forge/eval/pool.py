"""Bounded worker pool for eval corpus replay.

Runs corpus entries in parallel with strict scratch-tree isolation:
every entry gets its own temporary directory, and a structural guard
prevents two entries from ever sharing one.  A per-entry timeout
records hung entries without stalling the pool.

The pool is intentionally simple: concurrent.futures.ProcessPoolExecutor
with one future per entry.  The isolation guarantee lives in the worker
function, not in the pool topology, so it survives a refactor of the
executor.
"""
from __future__ import annotations

import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed, Future
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.runner import replay_entry
from code_forge.eval.scorer import EvalResult


# -- Scratch-tree registry (per-process isolation guard) -------------------

_tree_lock = threading.Lock()
_active_trees: dict[str, str] = {}
"""Maps temp_dir -> entry name.  Used to detect collisions."""


def _check_no_shared_tree(temp_dir: str, entry_name: str) -> None:
    """Raise if temp_dir is already in use by another entry.

    Called inside the worker BEFORE any review runs.  The check is
    structural: it fails deterministically when two entries point at the
    same directory, regardless of timing.  A guard that only fails under
    contention is not a guard.
    """
    with _tree_lock:
        existing = _active_trees.get(temp_dir)
        if existing is not None and existing != entry_name:
            raise RuntimeError(
                "scratch tree collision: %s is already in use by %r, "
                "cannot assign it to %r" % (temp_dir, existing, entry_name)
            )
        _active_trees[temp_dir] = entry_name


def _release_tree(temp_dir: str) -> None:
    """Remove a scratch tree from the registry after the entry finishes."""
    with _tree_lock:
        _active_trees.pop(temp_dir, None)


# -- Per-entry result container -------------------------------------------

@dataclass
class PoolEntry:
    """One entry's outcome from the pool."""
    entry: CorpusEntry
    result: Optional[EvalResult] = None
    error: Optional[str] = None
    wall_s: float = 0.0
    hung: bool = False


# -- Worker function (runs in subprocess) ----------------------------------


def _worker(
    entry: CorpusEntry,
    corpus_dir: str,
    backend_name: str,
    runs: Optional[int],
    backend_config: Optional[dict],
) -> tuple[EvalResult, float]:
    """Run one entry in its own process with its own scratch tree.

    Returns the result along with the wall time measured inside the
    worker.  The parent cannot time this: futures complete in whatever
    order they finish, so a clock started in the parent measures queue
    latency plus execution, not execution.

    Scratch-tree isolation is enforced here rather than trusted.
    replay_entry creates its own tempdir per run (runner.py:716), and
    this function registers that directory against the entry name so a
    collision raises instead of silently interleaving two entries
    through one .code-forge/state.json.
    """
    t0 = time.monotonic()
    tracked: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def _tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        _check_no_shared_tree(path, entry.name)
        tracked.append(path)
        return path

    tempfile.mkdtemp = _tracking_mkdtemp
    try:
        result = replay_entry(
            entry,
            corpus_dir=Path(corpus_dir),
            backend_name=backend_name,
            runs=runs,
            backend_config=backend_config,
        )
    finally:
        tempfile.mkdtemp = real_mkdtemp
        for path in tracked:
            _release_tree(path)
    return result, time.monotonic() - t0


# -- Pool orchestrator -----------------------------------------------------


def run_pool(
    entries: list[CorpusEntry],
    corpus_dir: Path,
    backend_name: str,
    runs: Optional[int],
    backend_config: Optional[dict],
    jobs: int = 4,
    entry_timeout_s: int = 3600,
    progress_cb=None,
) -> list[PoolEntry]:
    """Run entries through the eval pipeline with bounded concurrency.

    Args:
        entries: corpus entries to evaluate.
        corpus_dir: directory containing the corpus manifest and diffs.
        backend_name: backend to use for review.
        runs: override run count per entry (None = axis-dependent).
        backend_config: optional backend config dict.
        jobs: max concurrent workers (default 4).
        entry_timeout_s: per-entry wall-clock timeout in seconds.
        progress_cb: optional callback(done, total, entry_name, wall_s)
            called after each entry completes.

    Returns:
        List of PoolEntry in the same order as the input entries.

    Structural isolation guarantee:
        Each entry runs in a separate process via ProcessPoolExecutor.
        replay_entry creates a fresh tempdir per run (runner.py:716), and
        _worker registers each one against the entry name, so two entries
        landing on one tree raises instead of silently interleaving
        through one .code-forge/state.json.  The per-process boundary also
        isolates module-level state (_AXIS_HOOKS, env mutations) that
        threading would share.

    Caller requirement (jobs > 1):
        Python 3.14 starts pool workers through forkserver, which
        re-imports the calling module in the child.  A caller that
        invokes run_pool at module scope will have the child re-run it
        and the executor refuses to start ("attempt to start a new
        process before the current process has finished its
        bootstrapping phase").  Call it from inside a function guarded by
        ``if __name__ == "__main__"``.  The CLI already satisfies this;
        ad-hoc scripts often do not.
    """
    if jobs < 1:
        raise ValueError("--jobs must be >= 1, got %d" % jobs)

    total = len(entries)
    results: list[PoolEntry] = [PoolEntry(entry=e) for e in entries]

    if jobs == 1:
        # Serial path: no process pool overhead, same interface.
        for i, entry in enumerate(entries):
            pe = results[i]
            t0 = time.monotonic()
            try:
                pe.result = replay_entry(
                    entry,
                    corpus_dir=corpus_dir,
                    backend_name=backend_name,
                    runs=runs,
                    backend_config=backend_config,
                )
                pe.wall_s = time.monotonic() - t0
            except Exception as exc:
                pe.wall_s = time.monotonic() - t0
                pe.error = str(exc)
            if progress_cb:
                progress_cb(i + 1, total, entry.name, pe.wall_s)
        return results

    # Parallel path: one future per entry.
    #
    # ProcessPoolExecutor rather than ThreadPoolExecutor, because
    # replay_entry mutates os.environ (XDG_CONFIG_HOME, FORGE_SKIP_
    # WORKTREE_CHECK) and holds module-level state (_AXIS_HOOKS).
    # Threads would share both, and env mutations from one entry
    # would be visible to another running concurrently.  Processes
    # get their own copies.
    corpus_dir_str = str(corpus_dir)
    future_to_idx: dict[Future, int] = {}

    # A hung entry must not hold the run open forever, and a pool-wide
    # deadline is the only thing that survives a worker that ignores
    # signals.  as_completed's own timeout counts from the moment it is
    # called, so it is a deadline for ALL remaining futures, not one
    # per entry; the per-entry budget is enforced by giving each entry
    # its share and letting the runner's own review timeout
    # (FORGE_REVIEW_TIMEOUT_S) do the fine-grained killing.
    deadline = time.monotonic() + entry_timeout_s * max(
        1, (len(entries) + jobs - 1) // jobs
    )

    executor = ProcessPoolExecutor(max_workers=jobs)
    try:
        for i, entry in enumerate(entries):
            future = executor.submit(
                _worker,
                entry,
                corpus_dir_str,
                backend_name,
                runs,
                backend_config,
            )
            future_to_idx[future] = i

        done_count = 0
        pending = set(future_to_idx)
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for future in pending:
                    pe = results[future_to_idx[future]]
                    pe.hung = True
                    pe.error = (
                        "pool deadline exceeded before this entry finished"
                    )
                    future.cancel()
                break

            try:
                completed = next(as_completed(pending, timeout=remaining))
            except TimeoutError:
                continue

            pending.discard(completed)
            idx = future_to_idx[completed]
            pe = results[idx]
            try:
                pe.result, pe.wall_s = completed.result()
            except Exception as exc:
                pe.error = "%s: %s" % (type(exc).__name__, exc)

            done_count += 1
            if progress_cb:
                progress_cb(done_count, total, pe.entry.name, pe.wall_s)
    finally:
        # A cancelled-but-running worker keeps the interpreter alive on
        # __exit__, which is exactly the stall the timeout exists to
        # prevent.  Do not wait for it.
        executor.shutdown(wait=False, cancel_futures=True)

    return results

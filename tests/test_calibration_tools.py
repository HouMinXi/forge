# SPDX-License-Identifier: Apache-2.0
"""Tools for the Phase 59-A2 falsify calibration set.

The eval runner deletes each entry's temp dir after scoring, so the
falsifier's real inputs (the L1 findings with their file/line/description)
never survive a run. keep_state_dir asks replay_entry to copy state.json
out before the rmtree; the calibration scripts build on that.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.runner import replay_entry


def _entry(name: str = "calib-1") -> CorpusEntry:
    return CorpusEntry(
        name=name, diff_file="diffs/test.diff",
        expected_verdict="HOLD", axis_tags=["TRUST"],
    )


def _corpus(tmp_path: Path) -> Path:
    diff_dir = tmp_path / "corpus"
    (diff_dir / "diffs").mkdir(parents=True)
    (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")
    return diff_dir


STATE = {"findings": [{"id": "x", "fingerprint": "abc", "source": "L1",
                       "disposition": "CONFIRMED", "file": "f.py",
                       "line_range": [3, 3], "description": "d"}]}


@patch("code_forge.eval.runner._run_review")
@patch("code_forge.eval.runner.subprocess.run")
@patch("code_forge.eval.runner.record_trust")
def test_replay_entry_keeps_state_when_asked(
    mock_trust: MagicMock, mock_run: MagicMock, mock_review: MagicMock,
    tmp_path: Path,
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")

    def _review(cmd, temp_dir, env, timeout_s):
        d = Path(temp_dir) / ".code-forge"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
        return (1, "")
    mock_review.side_effect = _review

    keep = tmp_path / "keep"
    replay_entry(_entry(), _corpus(tmp_path), "test-backend",
                 keep_state_dir=str(keep))
    kept = keep / "calib-1" / "state.json"
    assert kept.exists()
    assert json.loads(kept.read_text())["findings"][0]["fingerprint"] == "abc"


@patch("code_forge.eval.runner._run_review")
@patch("code_forge.eval.runner.subprocess.run")
@patch("code_forge.eval.runner.record_trust")
def test_replay_entry_without_keep_leaves_nothing(
    mock_trust: MagicMock, mock_run: MagicMock, mock_review: MagicMock,
    tmp_path: Path,
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")

    def _review(cmd, temp_dir, env, timeout_s):
        d = Path(temp_dir) / ".code-forge"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
        return (1, "")
    mock_review.side_effect = _review
    replay_entry(_entry(), _corpus(tmp_path), "test-backend")
    assert not (tmp_path / "keep").exists()


@patch("code_forge.eval.runner._run_review")
@patch("code_forge.eval.runner.subprocess.run")
@patch("code_forge.eval.runner.record_trust")
def test_replay_entry_keep_tolerates_missing_state(
    mock_trust: MagicMock, mock_run: MagicMock, mock_review: MagicMock,
    tmp_path: Path,
) -> None:
    """A run that died before writing state.json is a SKIPPED result,
    not an exception from the keep path."""
    mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
    mock_review.side_effect = lambda cmd, temp_dir, env, timeout_s: (1, "")
    keep = tmp_path / "keep"
    r = replay_entry(_entry(), _corpus(tmp_path), "test-backend",
                     keep_state_dir=str(keep))
    assert r.actual_verdict == "SKIPPED"
    assert not (keep / "calib-1" / "state.json").exists()


# ---- run_falsify_calibration.py -------------------------------------------

import subprocess
import sys

_RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_falsify_calibration.py"


def _calib_dir(tmp_path: Path, items: list[dict]) -> Path:
    d = tmp_path / "calib"
    d.mkdir()
    (d / "expected.json").write_text(json.dumps({
        "version": 1, "frozen_at": "deadbeef", "items": items}))
    return d


def _item(i: int, expected: str) -> dict:
    return {"id": "c%02d" % i, "entry": "e-%d" % i,
            "finding": {"file": "f.py", "line_range": [i, i], "description": "d%d" % i},
            "expected": expected, "why": "w", "evidence": "ev"}


def _run(d: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(_RUNNER.parent.parent / "src"))
    return subprocess.run([sys.executable, str(_RUNNER), str(d), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_runner_stub_all_dismissed_agrees(tmp_path):
    d = _calib_dir(tmp_path, [_item(1, "DISMISSED"), _item(2, "DISMISSED")])
    r = _run(d, "--engine", "stub", "--stub-default", "DISMISSED")
    assert r.returncode == 0, r.stderr
    assert "agree 2/2" in r.stdout


def test_runner_stub_reports_miss_and_exits_1(tmp_path):
    d = _calib_dir(tmp_path, [_item(1, "CONFIRMED"), _item(2, "DISMISSED")])
    r = _run(d, "--engine", "stub", "--stub-default", "DISMISSED")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "MISS c01 expected=CONFIRMED got=DISMISSED" in r.stdout
    assert "agree 1/2" in r.stdout


def test_runner_threshold_boundary_is_inclusive(tmp_path):
    """19/20 with --min-agree 0.95 is exit 0; the A3 gate is 18/20."""
    items = [_item(i, "DISMISSED") for i in range(1, 20)] + [_item(20, "CONFIRMED")]
    d = _calib_dir(tmp_path, items)
    r = _run(d, "--engine", "stub", "--stub-default", "DISMISSED", "--min-agree", "0.95")
    assert r.returncode == 0, r.stdout
    r = _run(d, "--engine", "stub", "--stub-default", "DISMISSED", "--min-agree", "0.96")
    assert r.returncode == 1


def test_runner_bad_expected_json_exits_2(tmp_path):
    d = tmp_path / "calib"
    d.mkdir()
    (d / "expected.json").write_text('{"version": 1}')
    r = _run(d, "--engine", "stub")
    assert r.returncode == 2


@patch("code_forge.eval.runner._run_review")
@patch("code_forge.eval.runner.subprocess.run")
@patch("code_forge.eval.runner.record_trust")
def test_keep_failure_does_not_mask_or_skip_cleanup(
    mock_trust: MagicMock, mock_run: MagicMock, mock_review: MagicMock,
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Review round 0 on 10bb893: _keep_state was bare inside finally, so
    a copy failure would replace the real exception and skip rmtree."""
    import code_forge.eval.runner as runner_mod
    mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")

    def _review(cmd, temp_dir, env, timeout_s):
        d = Path(temp_dir) / ".code-forge"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
        return (1, "")
    mock_review.side_effect = _review

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(runner_mod.shutil, "copy2", boom)
    removed = []
    real_rmtree = runner_mod.shutil.rmtree
    monkeypatch.setattr(runner_mod.shutil, "rmtree",
                        lambda p, **k: (removed.append(p), real_rmtree(p, **k)))
    r = replay_entry(_entry(), _corpus(tmp_path), "test-backend",
                     keep_state_dir=str(tmp_path / "keep"))
    assert r.actual_verdict == "HOLD"
    assert removed, "rmtree skipped"
    assert "could not keep" in capsys.readouterr().err


def test_runner_with_corpus_hands_each_item_its_entry_diff(tmp_path, monkeypatch):
    """A4-0 arm: --corpus makes the real falsifier see the entry's diff.
    Drive the script in-process with llm_invoke mocked and assert the
    prompt for item c01 carries e-1's hunk and not e-2's."""
    import importlib.util
    from types import SimpleNamespace
    corpus = tmp_path / "corpus"
    (corpus / "diffs").mkdir(parents=True)
    (corpus / "diffs" / "e1.diff").write_text(
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-ONE_OLD\n+ONE_NEW\n")
    (corpus / "diffs" / "e2.diff").write_text(
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-TWO_OLD\n+TWO_NEW\n")
    (corpus / "corpus.yaml").write_text(
        "entries:\n- name: e-1\n  diff_file: diffs/e1.diff\n- name: e-2\n  diff_file: diffs/e2.diff\n")
    d = _calib_dir(tmp_path, [_item(1, "DISMISSED"), _item(2, "DISMISSED")])
    seen = []

    def fake(prompt, **kw):
        seen.append(prompt)
        return SimpleNamespace(content={"verdict": "DISMISSED", "reasoning": "r"})
    monkeypatch.setattr("code_forge.falsify_real.llm_invoke", fake)
    # Keep the real factory: the script must reach the per-item diff
    # through build_falsifier, not by rebuilding RealFalsifier from the
    # judge's private fields (review finding on the first cut).
    spec = importlib.util.spec_from_file_location("rfc", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_backend", lambda name: None)
    monkeypatch.setattr(sys, "argv", ["x", str(d), "--engine", "real",
                                      "--corpus", str(corpus / "corpus.yaml")])
    rc = mod.main()
    assert rc == 0
    assert "ONE_NEW" in seen[0] and "TWO_NEW" not in seen[0]
    assert "TWO_NEW" in seen[1] and "ONE_NEW" not in seen[1]


def test_runner_never_reaches_into_the_judges_private_fields():
    """Review finding on the first --corpus cut: the script rebuilt
    RealFalsifier from `fals._backend`. Behaviourally identical, so no
    fixture catches it; pin the shape."""
    src = _RUNNER.read_text()
    assert "._backend" not in src.replace("def _backend", "")
    assert "RealFalsifier(" not in src


def test_keep_state_refuses_names_that_escape_the_dir(tmp_path):
    from code_forge.eval.runner import _keep_state
    tmp = tmp_path / "t"; (tmp / ".code-forge").mkdir(parents=True)
    (tmp / ".code-forge" / "state.json").write_text("{}")
    keep = tmp_path / "keep"; keep.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    import pytest
    with pytest.raises(ValueError):
        _keep_state(str(tmp), str(keep), "../outside/x")
    assert not (outside / "x" / "state.json").exists()
    _keep_state(str(tmp), str(keep), "fine-name")
    assert (keep / "fine-name" / "state.json").exists()


def test_runner_rejects_malformed_items_up_front(tmp_path, monkeypatch, capsys):
    """A missing key used to surface as a KeyError mid-loop, after the
    earlier items had already been billed to the backend."""
    import importlib.util, sys, json
    d = tmp_path / "calib"; d.mkdir()
    (d / "expected.json").write_text(json.dumps({"items": [
        {"id": "c01", "expected": "DISMISSED", "entry": "e", "why": "w",
         "finding": {"file": "f.py", "description": "d"}}]}))  # no line_range
    spec = importlib.util.spec_from_file_location("rfc", _RUNNER)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "argv", ["x", str(d), "--engine", "stub"])
    rc = mod.main()  # main() turns the SystemExit into rc=2 + stderr line
    assert rc == 2
    assert "line_range" in capsys.readouterr().err


def test_entry_escaping_raw_is_refused(tmp_path):
    item = _item(1, "CONFIRMED"); item["entry"] = "../outside"
    d = _calib_dir(tmp_path, [item])
    raw = tmp_path / "raw"; raw.mkdir()
    r = _run(d, "--engine", "stub", "--raw", str(raw))
    assert r.returncode == 2, r.stderr
    assert "escapes --raw" in r.stderr


def test_untrusted_gate_backends_are_ignored_by_the_runner(tmp_path, monkeypatch):
    """The CLI refuses backends from a gate.yaml whose credential fields
    changed since trust; the calibration runner reads the same file and
    must apply the same guard."""
    import importlib.util, yaml
    (tmp_path / ".code-forge").mkdir()
    (tmp_path / ".code-forge" / "gate.yaml").write_text(yaml.safe_dump({
        "backends": {"evil": {"type": "api", "base_url": "https://attacker.example",
                              "api_key_env": "X", "model": "m"}}}))
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    monkeypatch.setattr("code_forge.user_config.load_user_backends", lambda: {})
    import pytest
    with pytest.raises(SystemExit, match="not in"):
        m._backend("evil")


def test_dirty_tree_is_refused_on_the_next_item(tmp_path, monkeypatch, capsys):
    """_reader_rows restores the tree in a finally. If that restore
    fails while another exception is propagating, the exception must
    win -- but the tree is dirty, and the next item that would use it
    must stop rather than grep a half-applied checkout.
    Contract change: disk check raises RuntimeError, not SystemExit."""
    import importlib.util
    import subprocess
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    root = tmp_path / "trees"
    e1 = root / "e1"
    e1.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e1, check=True)
    (e1 / "x.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=e1, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e1, check=True)

    real_run = subprocess.run

    def fake_run(argv, **kw):
        if argv[:2] == ["git", "checkout"]:
            return subprocess.CompletedProcess(argv, 1, "", "checkout refused")
        return real_run(argv, **kw)
    monkeypatch.setattr(subprocess, "run", fake_run)

    class _Boom:
        def __init__(self, *a): pass
        def facts(self, *a): raise RuntimeError("facts blew up")
    monkeypatch.setattr("code_forge.context_sources.RemovedSymbolReaders", _Boom)
    import pytest
    _D = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
          "@@ -1 +1 @@\n-a = 1\n+a = 2\n")
    with pytest.raises(RuntimeError, match="facts blew up"):
        m._reader_rows(root, "e1", _D)
    assert "git checkout failed to restore e1" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="dirty"):
        m._reader_rows(root, "e1", _D)


def test_dirty_tree_seen_by_new_process(tmp_path):
    """A new process running the calibration runner must detect a dirty tree
    on disk via git status without relying on an in-memory set, record INFRA,
    and exit with return code 2."""
    root = tmp_path / "trees"
    e1 = root / "e1"
    e1.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e1, check=True)
    (e1 / "x.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=e1, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e1, check=True)
    # Leave tree dirty on disk
    (e1 / "x.py").write_text("a = 999\n")

    corpus = tmp_path / "corpus"
    (corpus / "diffs").mkdir(parents=True)
    (corpus / "diffs" / "e1.diff").write_text(
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n")
    (corpus / "corpus.yaml").write_text(
        "entries:\n- name: e1\n  diff_file: diffs/e1.diff\n")
    d = _calib_dir(tmp_path, [_item(1, "DISMISSED")])
    d_items = json.loads((d / "expected.json").read_text())
    d_items["items"][0]["entry"] = "e1"
    (d / "expected.json").write_text(json.dumps(d_items))

    r = _run(d, "--engine", "stub", "--stub-default", "DISMISSED",
             "--corpus", str(corpus / "corpus.yaml"),
             "--tree-root", str(root))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "INFRA c01" in r.stdout
    assert "infra=1" in r.stdout
    assert "agree 0/1" in r.stdout


def test_checkout_failure_or_timeout_leaves_dirty_tree_and_raises(tmp_path, monkeypatch):
    """When git checkout fails or times out, RuntimeError is raised, the tree
    is left dirty on disk, and git clean is not run."""
    import importlib.util
    import subprocess
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    root = tmp_path / "trees"
    e1 = root / "e1"
    e1.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e1, check=True)
    (e1 / "x.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=e1, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e1, check=True)

    real_run = subprocess.run

    def fake_run(argv, **kw):
        if argv[:2] == ["git", "checkout"]:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=60)
        return real_run(argv, **kw)
    monkeypatch.setattr(subprocess, "run", fake_run)

    class _Boom:
        def __init__(self, *a): pass
        def facts(self, *a): raise RuntimeError("facts blew up")
    monkeypatch.setattr("code_forge.context_sources.RemovedSymbolReaders", _Boom)

    import pytest
    _D = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
          "@@ -1 +1 @@\n-a = 1\n+a = 2\n")
    with pytest.raises(RuntimeError, match="facts blew up"):
        m._reader_rows(root, "e1", _D)
    # Tree must remain dirty on disk from git apply (not cleaned up by checkout)
    res = real_run(["git", "status", "--porcelain"], cwd=e1, capture_output=True, text=True)
    assert res.stdout.strip() != ""


def test_main_failed_item_skips_judge_and_continues_independent_item(tmp_path, monkeypatch):
    """If item 1 fails with dirty tree / infra error, its judge is not called,
    while independent item 2 with clean tree continues and calls its judge."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    root = tmp_path / "trees"
    e1 = root / "e1"
    e1.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e1, check=True)
    (e1 / "x.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=e1, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e1, check=True)
    # e1 dirty
    (e1 / "x.py").write_text("a = 999\n")

    e2 = root / "e2"
    e2.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e2, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e2, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e2, check=True)
    (e2 / "x.py").write_text("b = 1\n")
    subprocess.run(["git", "add", "."], cwd=e2, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e2, check=True)

    corpus = tmp_path / "corpus"
    (corpus / "diffs").mkdir(parents=True)
    (corpus / "diffs" / "e1.diff").write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n")
    (corpus / "diffs" / "e2.diff").write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-b = 1\n+b = 2\n")
    (corpus / "corpus.yaml").write_text("entries:\n- name: e1\n  diff_file: diffs/e1.diff\n- name: e2\n  diff_file: diffs/e2.diff\n")

    it1 = _item(1, "DISMISSED")
    it1["entry"] = "e1"
    it2 = _item(2, "DISMISSED")
    it2["entry"] = "e2"
    d = _calib_dir(tmp_path, [it1, it2])

    judged_items = []
    class FakeJudge:
        def __init__(self, entry): self.entry = entry
        def falsify(self, sf):
            judged_items.append(sf.id)
            from types import SimpleNamespace
            return SimpleNamespace(value="DISMISSED")

    monkeypatch.setattr(m, "_make_falsifier", lambda *a, **k: FakeJudge("judge"))
    monkeypatch.setattr(m, "_backend", lambda name: None)
    monkeypatch.setattr(sys, "argv", [
        "rfc", str(d), "--engine", "real",
        "--corpus", str(corpus / "corpus.yaml"),
        "--tree-root", str(root),
    ])
    rc = m.main()
    assert rc == 2
    # Item 1 was skipped (INFRA); only Item 2 was judged
    assert judged_items == ["c02"]


def test_main_cwd_restored_on_failure(tmp_path, monkeypatch):
    """When os.chdir changes to entry_dir and an operational failure happens,
    the original cwd is always restored."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    raw = tmp_path / "raw"
    (raw / "e1").mkdir(parents=True)
    it1 = _item(1, "DISMISSED")
    it1["entry"] = "e1"
    d = _calib_dir(tmp_path, [it1])

    class FakeJudge:
        def falsify(self, sf):
            raise RuntimeError("falsify crashed")

    monkeypatch.setattr(m, "_make_falsifier", lambda *a, **k: FakeJudge())
    cwd_start = os.getcwd()
    monkeypatch.setattr(sys, "argv", [
        "rfc", str(d), "--engine", "stub", "--raw", str(raw),
    ])
    rc = m.main()
    assert rc == 2
    assert os.getcwd() == cwd_start


def test_restore_detects_untracked_file_without_destructive_clean(tmp_path):
    """If git apply creates a new untracked file, checkout -- . does not remove it.
    The restore check must detect the dirty state, raise RuntimeError,
    refuse to run destructive git clean, and leave the tree dirty so the next
    item or process refuses it."""
    import importlib.util
    import subprocess
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    root = tmp_path / "trees"
    e1 = root / "e1"
    e1.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e1, check=True)
    (e1 / "x.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "."], cwd=e1, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e1, check=True)

    diff_new = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1 @@\n"
        "+new_var = 1\n"
    )
    import pytest
    with pytest.raises(RuntimeError, match="remains dirty after restore"):
        m._reader_rows(root, "e1", diff_new)

    assert (e1 / "new.py").is_file()
    with pytest.raises(RuntimeError, match="is dirty"):
        m._reader_rows(root, "e1", diff_new)


def test_apply_failure_raises_and_records_infra(tmp_path, monkeypatch, capsys):
    """When git apply fails, RuntimeError is raised with apply output,
    _reader_rows does not silently return empty list, and main records
    INFRA with agree not incremented."""
    import importlib.util
    import subprocess
    import sys
    spec = importlib.util.spec_from_file_location("rfc", str(_RUNNER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    root = tmp_path / "trees"
    e1 = root / "e1"
    e1.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=e1, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=e1, check=True)
    (e1 / "x.py").write_text("a = 999\n")
    subprocess.run(["git", "add", "."], cwd=e1, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=e1, check=True)

    diff_conflict = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n")

    import pytest
    with pytest.raises(RuntimeError, match="git apply failed"):
        m._reader_rows(root, "e1", diff_conflict)

    corpus = tmp_path / "corpus"
    (corpus / "diffs").mkdir(parents=True)
    (corpus / "diffs" / "e1.diff").write_text(diff_conflict)
    (corpus / "corpus.yaml").write_text(
        "entries:\n- name: e1\n  diff_file: diffs/e1.diff\n")
    d = _calib_dir(tmp_path, [_item(1, "DISMISSED")])
    d_items = json.loads((d / "expected.json").read_text())
    d_items["items"][0]["entry"] = "e1"
    (d / "expected.json").write_text(json.dumps(d_items))

    monkeypatch.setattr(m, "_backend", lambda name: None)
    monkeypatch.setattr(m, "_make_falsifier", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "rfc", str(d), "--engine", "real",
        "--corpus", str(corpus / "corpus.yaml"),
        "--tree-root", str(root),
    ])
    rc = m.main()
    assert rc == 2
    out = capsys.readouterr().out
    assert "INFRA c01 RuntimeError: git apply failed" in out
    assert "agree 0/1" in out
    assert "infra=1" in out



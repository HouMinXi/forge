# SPDX-License-Identifier: Apache-2.0
"""Physical source and context denials cannot acquire mutation authority."""

from copy import deepcopy
from dataclasses import replace
from functools import partial
import os
from pathlib import Path
import subprocess

import pytest

from code_forge import mutation, mutation_findings
from code_forge.cli import _build_parser, _run_mutation_check
from code_forge.disposition import Disposition
from code_forge.ledger import append_row
from tests.test_mutation_ledger_ci_controls import (
    ci_project as ci_project,
    _machine,
    _measure,
    _payload,
    _review,
    _row,
    _rule,
    SOURCE_A,
    SOURCE_B,
)


def _capture(project, **changes):
    review = _review(project)
    values = dict(
        source_root=project.root,
        target_kind="WORKING",
        base_sha=review.base_sha,
        head_sha=review.head_sha,
        git_diff=review.git_diff,
        paths=("calc.py",),
        diff_paths=("calc.py",),
        selection="WORKING",
    )
    values.update(changes)
    return mutation.capture_mutation_source_guard(**values)


def _guard(project):
    guard = _capture(project)
    assert guard is not None
    assert mutation.mutation_source_guard_matches(guard, source_root=project.root)
    return guard


def test_guard_head_requires_an_actual_commit(ci_project, monkeypatch):
    root = ci_project.root.parent / "not-a-repository"
    root.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(root.parent))
    with pytest.raises(ValueError, match="cannot resolve HEAD"):
        mutation._guard_head(root)


@pytest.mark.parametrize(
    "selection,paths",
    [
        ("UNSUPPORTED", ()),
        ("TRACKED_WORKING", ("calc.py",)),
        ("invalid-base", ()),
    ],
    ids=["unsupported", "tracked-with-paths", "unresolvable-base"],
)
def test_guard_diff_refuses_unsupported_or_unresolvable_selection(ci_project, selection, paths):
    review = _review(ci_project)
    base = review.base_sha
    message = "unsupported mutation review diff selection"
    if selection == "invalid-base":
        selection, base = "TRACKED_WORKING", "f" * 40
        message = "cannot repeat reviewed diff"
    with pytest.raises(ValueError, match=message):
        mutation._guard_diff(ci_project.root, base, paths, selection)
    assert (ci_project.root / "calc.py").read_bytes() == SOURCE_A


@pytest.mark.parametrize("kind", ["parent-link", "leaf-link", "hardlink", "directory", "aliases"])
def test_guard_entries_refuses_physical_aliases(ci_project, kind):
    root = ci_project.root
    outside = root.parent / "outside-bytes"
    outside.mkdir()
    witness = outside / "record.py"
    witness.write_bytes(SOURCE_A)
    if kind == "parent-link":
        (root / "linked-parent").symlink_to(outside, target_is_directory=True)
        paths, message = ("linked-parent/record.py",), "physical directories"
    elif kind == "leaf-link":
        (root / "leaf.py").symlink_to(witness)
        paths, message = ("leaf.py",), "unaliased regular files"
    elif kind == "hardlink":
        os.link(witness, root / "hard.py")
        paths, message = ("hard.py",), "unaliased regular files"
    elif kind == "directory":
        (root / "directory.py").mkdir()
        paths, message = ("directory.py",), "unaliased regular files"
    else:
        paths, message = ("calc.py", "./calc.py"), "scope contains aliases"
    with pytest.raises(ValueError, match=message):
        mutation._guard_entries(root, paths)
    assert witness.read_bytes() == SOURCE_A
    assert ci_project.measurements == [] and ci_project.launches == []


@pytest.mark.parametrize("kind", ["missing-parent-change", "endpoint-change", "present-parent-change"])
def test_guard_entries_refuses_observed_endpoint_races(ci_project, monkeypatch, kind):
    root = ci_project.root
    parent, other = root / "owned", root / "different-parent"
    parent.mkdir()
    other.mkdir()
    path = parent / "record.py"
    if kind != "missing-parent-change":
        path.write_bytes(SOURCE_A)
    original_lstat = Path.lstat
    parent_observations = []
    if kind.endswith("parent-change"):

        def changed_parent(self, *args, **kwargs):
            if self == parent:
                parent_observations.append(original_lstat(self, *args, **kwargs).st_ino)
                if len(parent_observations) >= 2:
                    return original_lstat(other, *args, **kwargs)
            return original_lstat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", changed_parent)
        message = "parents changed during " + ("absence" if kind.startswith("missing") else "endpoint")
    else:
        original_read = mutation._read_inventory_file

        def changed_endpoint(asked):
            raw = original_read(asked)
            if asked == path:
                asked.write_bytes(SOURCE_B)
            return raw

        monkeypatch.setattr(mutation, "_read_inventory_file", changed_endpoint)
        message = "source changed during endpoint capture"
    with pytest.raises(ValueError, match=message):
        mutation._guard_entries(root, ("owned/record.py",))
    if kind.startswith("missing"):
        assert not path.exists()
    else:
        assert path.read_bytes() == (SOURCE_B if kind == "endpoint-change" else SOURCE_A)
    if kind.endswith("parent-change"):
        assert len(parent_observations) >= 2
        assert original_lstat(parent).st_ino != original_lstat(other).st_ino
    assert ci_project.measurements == []


@pytest.mark.parametrize(
    "kind", ["linked-root", "rename", "copy", "wrong-head", "endpoint-race", "missing-scope"]
)
def test_capture_refuses_unbound_source_authority(ci_project, monkeypatch, kind):
    p = ci_project
    changes = {}
    if kind == "linked-root":
        alias = p.root.parent / "linked-root"
        alias.symlink_to(p.root, target_is_directory=True)
        changes["source_root"] = alias
    elif kind in ("rename", "copy"):
        changes["git_diff"] = _review(p).git_diff + "\n" + kind + " from calc.py\n"
    elif kind == "wrong-head":
        changes["head_sha"] = "0" * 40
    elif kind == "missing-scope":
        changes["paths"] = ("missing.py",)
    else:
        original = mutation._guard_entries
        observations = []

        def changed_entries(root, paths):
            value = original(root, paths)
            observations.append(value)
            if len(observations) == 1:
                (p.root / "calc.py").write_bytes(SOURCE_B)
            return value

        monkeypatch.setattr(mutation, "_guard_entries", changed_entries)
    assert _capture(p, **changes) is None
    assert p.measurements == [] and p.launches == []
    assert (p.root / "calc.py").read_bytes() == (SOURCE_B if kind == "endpoint-race" else SOURCE_A)
    assert not (p.root / "missing.py").exists()


@pytest.mark.parametrize("kind", ["wrong-root", "linked-root", "metadata-error"])
def test_guard_match_refuses_root_or_metadata_denial(ci_project, monkeypatch, kind):
    p = ci_project
    guard = _guard(p)
    root = p.root
    if kind == "wrong-root":
        root = p.state
    elif kind == "linked-root":
        root = p.root.parent / "linked-root"
        root.symlink_to(p.root, target_is_directory=True)
        guard = replace(guard, source_root=str(root))
    else:
        original = Path.lstat

        def denied(self, *args, **kwargs):
            if self == root:
                raise OSError("RECORDED_ROOT_METADATA_DENIAL")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", denied)
    assert mutation.mutation_source_guard_matches(guard, source_root=root) is False
    assert (p.root / "calc.py").read_bytes() == SOURCE_A
    assert p.measurements == []


def test_context_constructor_refuses_malformed_source_root(ci_project):
    review = _review(ci_project)
    assert (
        mutation_findings.mutation_review_context(
            source_root=object(),
            base_sha=review.base_sha,
            head_sha=review.head_sha,
            git_diff=review.git_diff,
        )
        is None
    )


@pytest.mark.parametrize(
    "kind", ["invalid-sha", "invalid-digest", "resolve-oserror", "resolve-valueerror"]
)
def test_bound_decoder_refuses_context_identity_denial(ci_project, monkeypatch, kind):
    p = ci_project
    payload, findings = _payload(p)
    context = deepcopy(payload["review_context"])
    if kind == "invalid-sha":
        context["head_sha"] = "not-a-commit"
    elif kind == "invalid-digest":
        context["diff_sha256"] = "not-a-digest"
    else:
        original = Path.resolve
        error_type = OSError if kind == "resolve-oserror" else ValueError

        def denied(self, *args, **kwargs):
            if self == p.root:
                raise error_type("RECORDED_CONTEXT_PATH_DENIAL")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", denied)
        assert p.state.resolve() == p.state, "unrelated physical path must retain ordinary resolution"
    payload["review_context"] = context
    assert (
        mutation_findings.decode_mutation_survivor_details(
            payload, review_context=context, include_timeout_diagnostics=True
        )
        is None
    )
    assert findings[0].disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("error_type", [OSError, ValueError], ids=["oserror", "valueerror"])
def test_ledger_path_resolution_denial_cannot_dismiss_survivor(ci_project, monkeypatch, error_type):
    p = ci_project
    findings, _, _ = _measure(p)
    review = _review(p)
    denied_path = p.root / "denied-ledger-root"
    append_row(p.root, replace(_row(p, findings[0]), repo_root=str(denied_path)))
    original = Path.resolve
    calls = []

    def denied(self, *args, **kwargs):
        if self == denied_path:
            calls.append(str(self))
            raise error_type("RECORDED_LEDGER_PATH_DENIAL")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", denied)
    assert (
        mutation_findings.apply_mutation_ledger_dispositions(
            findings,
            source_root=p.root,
            base_sha=review.base_sha,
            head_sha=review.head_sha,
            git_diff=review.git_diff,
            eligible_measurement=True,
        )
        == []
    )
    assert calls == [str(denied_path)]
    assert findings[0].disposition is Disposition.CONFIRMED
    assert not denied_path.exists()


def test_malformed_partial_cwd_gains_no_native_measurement_authority(ci_project):
    p = ci_project
    runner = partial(mutation.run_mutation, cwd=object())
    machine = _machine(p, runner=runner)
    assert machine._run_l2_phase() == []
    assert machine._mutation_measurement_eligible is False
    assert any("L2 runner failed" in error for error in machine._state.infra_errors)
    assert p.measurements == [] and p.launches == []


def test_standalone_head_read_error_preserves_current_survivor(ci_project, monkeypatch, capsys):
    p = ci_project
    findings, _, _ = _measure(p)
    _rule(p, findings)
    original = subprocess.run
    calls = []

    def denied(argv, *args, **kwargs):
        if list(argv) == ["git", "rev-parse", "HEAD"] and Path(kwargs.get("cwd", ".")) == p.root:
            calls.append(list(argv))
            raise OSError("RECORDED_HEAD_READ_DENIAL")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", denied)
    args = _build_parser().parse_args(["mutation-check"])
    assert _run_mutation_check(args, p.root) == 1
    output = capsys.readouterr()
    assert calls == [["git", "rev-parse", "HEAD"]]
    assert "review capture unavailable; native ruling refused" in output.err
    assert "1 survivor(s) found" in output.err
    assert "mutation-check: PASS" not in output.err
    assert p.launches == [] and len(p.measurements) == 2


def test_guard_entries_refuses_same_tick_same_size_rewrite(ci_project, monkeypatch):
    """A same-size rewrite that no stat field shows must still be refused.

    On a coarse-timestamp kernel the rewrite can land in the same tick, so
    every stat field matches. Freeze lstat and model the per-read bytes
    directly, so the result does not depend on the box's timestamp
    granularity or on when the rewrite happens.
    """
    root = ci_project.root
    path = root / "record.py"
    path.write_bytes(SOURCE_A)
    frozen = path.lstat()
    original_lstat = Path.lstat
    monkeypatch.setattr(
        Path, "lstat", lambda self, *a, **kw: frozen if self == path else original_lstat(self, *a, **kw)
    )
    original_read = mutation._read_inventory_file
    reads = []

    def rewritten_between_reads(asked):
        if asked != path:
            return original_read(asked)
        reads.append(asked)
        return SOURCE_A if len(reads) == 1 else SOURCE_B

    monkeypatch.setattr(mutation, "_read_inventory_file", rewritten_between_reads)
    with pytest.raises(ValueError, match="source changed during endpoint capture"):
        mutation._guard_entries(root, ("record.py",))

"""Verifier failures retain typed verdicts and accurate check coordinates."""
import hashlib
import json
import logging

import pytest

from code_forge.receipt_scope import repository_scope
from code_forge.verify import parse_diff_files, run_verify


DIFF = "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -0,0 +1,5 @@\n+a\n+b\n+c\n+d\n+e\n"
SHA = hashlib.sha256(DIFF.encode()).hexdigest()
EXCERPT = {"file": "mod.py", "start_line": 1, "end_line": 5, "content": "a\nb\nc\nd\ne"}


def _setup(root, *, floor=1, cycles=(2,), excerpts=None, findings=None, manifest=None):
    rd = root / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (rd.parent / "gate.yaml").write_text(f"verify:\n  required_cycles: {floor}\n")
    for cycle in cycles:
        for perspective in (1, 2, 3):
            receipt = {
                "cycle": cycle, "pass": perspective,
                "diff_sha256": SHA,
                "timestamp": f"2026-09-01T00:{cycle:02d}:{perspective:02d}Z",
                "findings": findings or [], "findings_count": len(findings or []),
                "anchors": [], "code_excerpts": [EXCERPT] if excerpts is None else excerpts,
                "covered_line_ranges": [{"file": "mod.py", "start": 1, "end": 5}],
            }
            if manifest is not None:
                receipt["reviewed_repositories"] = manifest
            (rd / f"receipt-c{cycle}p{perspective}.json").write_text(json.dumps(receipt))
    return rd


def _change(rd, cycle=2, perspective=1, **changes):
    path = rd / f"receipt-c{cycle}p{perspective}.json"
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data))


def _verify(root, **kwargs):
    kwargs.setdefault("diff_text", DIFF)
    return run_verify(root, SHA, parse_diff_files(DIFF), **kwargs)


def _failure(result, reason, check, passed):
    assert result.passed is False
    assert result.reason == reason
    assert type(result.checks_run) is int
    assert type(result.checks_passed) is int
    assert result.checks_run == check
    assert result.checks_passed == passed


@pytest.mark.parametrize("value", [0, -1, True, False, "1", 1.0])
def test_required_cycles_returns_complete_failure(tmp_path, value):
    _failure(_verify(tmp_path, required_cycles=value),
             f"required_cycles must be an integer >= 1, got {value!r}", 1, 0)


@pytest.mark.parametrize("value", [[], (), (1,), "1", [0], [-1], [True], [False], ["1"], [1.0], [1, 1]])
def test_invalid_window_returns_complete_failure(tmp_path, value):
    _failure(_verify(tmp_path, cycles=value),
             f"cycles must be a list of distinct positive ints, got {value!r}", 1, 0)


@pytest.mark.parametrize("repositories,reason", [
    ({}, "INFRA: reviewed repositories must be a nonempty mapping"),
    ([], "INFRA: reviewed repositories must be a nonempty mapping"),
    ({"bad/label": DIFF}, "INFRA: unsupported repository identity"),
])
def test_bad_repository_input_is_not_a_partial_result(tmp_path, repositories, reason):
    _failure(_verify(tmp_path, reviewed_repositories=repositories), reason, 1, 0)


@pytest.mark.parametrize("respect_floor", [False, True])
def test_unreadable_policy_returns_failure_before_loading_receipts(tmp_path, respect_floor):
    rd = _setup(tmp_path)
    gate = rd.parent / "gate.yaml"
    gate.write_text("verify: null\n")
    _failure(_verify(tmp_path, respect_floor=respect_floor),
             f"unreadable gate: {gate} verify section is present but null; a written-down policy must be a mapping or absent",
             1, 0)


def test_missing_receipts_give_exact_recovery_instruction(tmp_path):
    rd = tmp_path / ".code-forge"
    rd.mkdir()
    (rd / "gate.yaml").write_text("verify:\n  required_cycles: 1\n")
    _failure(_verify(tmp_path),
             "missing receipts: 0/3 -- no review receipts found. Run 'code-forge review' on your staged changes first",
             1, 0)


def test_incomplete_receipts_do_not_claim_no_review_occurred(tmp_path):
    rd = _setup(tmp_path)
    (rd / "receipt-c2p2.json").unlink()
    (rd / "receipt-c2p3.json").unlink()
    _failure(_verify(tmp_path), "missing receipts: 1/3", 1, 0)


def test_corrupt_schema_keeps_failure_check_coordinates(tmp_path):
    rd = _setup(tmp_path)
    _change(rd, diff_sha256=3)
    result = _verify(tmp_path)
    assert result.reason.startswith("corrupt receipt:")
    assert "diff_sha256 must be a string" in result.reason
    _failure(result, result.reason, 1, 0)


def test_window_cannot_lower_repository_floor(tmp_path):
    _setup(tmp_path, floor=2, cycles=(1, 2))
    _failure(_verify(tmp_path, cycles=[2]),
             "attested window has 1 cycle(s); repository verifier floor demands 2: [2]", 1, 0)


def test_policy_floor_can_only_be_bypassed_explicitly(tmp_path):
    _setup(tmp_path, floor=3)
    _failure(_verify(tmp_path, required_cycles=1), "missing receipts: 3/9", 1, 0)
    result = _verify(tmp_path, required_cycles=1, respect_floor=False)
    assert result.passed is True
    assert (result.reason, result.checks_run, result.checks_passed) == ("all 8 checks passed", 8, 8)


@pytest.mark.parametrize("cycles", [(2, 4), (2, 3)])
def test_window_validation_precedes_evidence(tmp_path, cycles):
    _setup(tmp_path, floor=2, cycles=cycles)
    if cycles == (2, 4):
        _failure(_verify(tmp_path), "last 2 cycles not consecutive: [2, 4]", 1, 0)
    else:
        result = _verify(tmp_path, cycles=[3, 2])
        assert result.passed is True


def test_pinned_window_does_not_attest_later_receipts(tmp_path):
    rd = _setup(tmp_path, cycles=(2, 3))
    _change(rd, cycle=2, diff_sha256="stale")
    _failure(_verify(tmp_path, cycles=[2]), "diff hash mismatch c2p1", 2, 1)
    assert _verify(tmp_path, cycles=[3]).passed is True


@pytest.mark.parametrize("case,reason", [
    ("duplicate", "duplicate receipt c2p1"),
    ("count", "findings_count mismatch c2p1"),
    ("missing-one", "missing cycle 2/pass 1"),
    ("missing-two", "missing cycle 2/pass 2"),
    ("extra", "cycle 2 has pass 4, outside the three review passes"),
    ("manifest", "INFRA: reviewed repository/source identity mismatch"),
])
def test_matrix_failure_returns_no_completed_checks(tmp_path, case, reason):
    rd = _setup(tmp_path)
    if case == "duplicate":
        (rd / "receipt-c2p4.json").write_bytes((rd / "receipt-c2p1.json").read_bytes())
    elif case == "count":
        _change(rd, findings_count=1)
    elif case.startswith("missing"):
        perspective = 1 if case == "missing-one" else 2
        _change(rd, perspective=perspective, **{"pass": 4})
    elif case == "extra":
        data = json.loads((rd / "receipt-c2p1.json").read_text())
        data["pass"] = 4
        (rd / "receipt-c2p4.json").write_text(json.dumps(data))
    else:
        _change(rd, reviewed_repositories={"unexpected": "source"})
    _failure(_verify(tmp_path), reason, 1, 0)


@pytest.mark.parametrize("case,reason,check,passed", [
    ("hash", "diff hash mismatch c2p1", 2, 1),
    ("anchor", "anchor file elsewhere.py not in diff", 3, 2),
    ("time", "timestamps not monotonic", 4, 3),
    ("content", "excerpt content mismatch at mod.py:1-1 (line 1)", 5, 4),
    ("witness", "unwitnessed hunk mod.py:1-5", 5, 4),
    ("coverage", "coverage 20% < 60% cycle 2; largest uncovered: mod.py (4 lines)", 6, 4),
    ("pass-status", "pass did not complete: c2p1 status=error -- that pass contributed no review, so the cycle cannot attest", 8, 6),
])
def test_hardened_failure_names_the_exact_check(tmp_path, case, reason, check, passed):
    excerpts = [] if case == "witness" else None
    if case == "coverage":
        excerpts = [dict(EXCERPT, end_line=1, content="a")]
    rd = _setup(tmp_path, excerpts=excerpts)
    if case == "hash":
        _change(rd, diff_sha256="stale")
    elif case == "anchor":
        _change(rd, anchors=[{"file": "elsewhere.py"}])
    elif case == "time":
        _change(rd, timestamp="2099")
    elif case == "content":
        _change(rd, code_excerpts=[dict(EXCERPT, end_line=1, content="forged")])
    elif case == "pass-status":
        _change(rd, pass_status="error")
    _failure(_verify(tmp_path), reason, check, passed)


@pytest.mark.parametrize("quoted_count,passed", [(2, False), (3, True)])
def test_coverage_floor_is_inclusive(tmp_path, quoted_count, passed):
    _setup(tmp_path, excerpts=[dict(EXCERPT, end_line=quoted_count,
                                   content="\n".join("abcde"[:quoted_count]))])
    result = _verify(tmp_path)
    assert result.passed is passed
    if passed:
        assert (result.reason, result.checks_run, result.checks_passed) == ("all 8 checks passed", 8, 8)
    else:
        _failure(result, "coverage 40% < 60% cycle 2; largest uncovered: mod.py (3 lines)", 6, 4)


@pytest.mark.parametrize("bad_file", [None, 17, "forged.py"])
def test_joint_receipt_rejects_finding_outside_repository(tmp_path, bad_file):
    repositories = {"project": DIFF}
    qualified, manifest = repository_scope(repositories)
    path = next(iter(parse_diff_files(qualified)))
    _setup(tmp_path, excerpts=[dict(EXCERPT, file=path)],
           findings=[{"file": bad_file, "description": "untrusted"}], manifest=manifest)
    _failure(_verify(tmp_path, reviewed_repositories=repositories),
             "INFRA: finding repository/source identity mismatch", 1, 0)


def test_joint_receipt_accepts_matching_repository_evidence(tmp_path):
    repositories = {"project": DIFF}
    qualified, manifest = repository_scope(repositories)
    path = next(iter(parse_diff_files(qualified)))
    _setup(tmp_path, excerpts=[dict(EXCERPT, file=path)],
           findings=[{"file": path, "description": "audited"}], manifest=manifest)
    result = _verify(tmp_path, reviewed_repositories=repositories)
    assert result.passed is True
    assert (result.reason, result.checks_run, result.checks_passed) == ("all 8 checks passed", 8, 8)


@pytest.mark.parametrize("hardened", [False, True])
def test_jaccard_failure_preserves_counter_fields(tmp_path, hardened):
    _setup(tmp_path, floor=2, cycles=(2, 3), findings=[{"description": "open"}])
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    _failure(_verify(tmp_path, hardened=hardened), "Jaccard overlap 1.00 > 0.8 c2-c3", 7,
             5 if hardened else 6)


def test_receipt_count_cannot_replace_distinct_cycle_count(tmp_path):
    rd = _setup(tmp_path, floor=2)
    for perspective in (1, 2, 3):
        source = rd / f"receipt-c2p{perspective}.json"
        (rd / f"receipt-c9p{perspective}.json").write_bytes(source.read_bytes())
    _failure(_verify(tmp_path), "fewer than 2 cycles: 1", 1, 0)


def test_unparseable_diff_retains_failure_fields(tmp_path):
    _setup(tmp_path)
    result = run_verify(tmp_path, SHA, {}, diff_text="not a unified diff")
    _failure(result, "diff parse failed -- cannot verify excerpts", 5, 4)


def test_legacy_read_failure_retains_failure_fields(tmp_path):
    _setup(tmp_path)
    (tmp_path / "mod.py").mkdir()
    _failure(_verify(tmp_path, hardened=False), "excerpt line range error mod.py:1-5", 5, 4)


@pytest.mark.parametrize("quoted_count,passed", [(2, False), (3, True)])
def test_legacy_coverage_reports_failure_fields(tmp_path, quoted_count, passed):
    rd = _setup(tmp_path)
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    for perspective in (1, 2, 3):
        _change(rd, perspective=perspective,
                covered_line_ranges=[{"file": "mod.py", "start": 1, "end": quoted_count}])
    result = _verify(tmp_path, hardened=False)
    assert result.passed is passed
    if not passed:
        _failure(result, "coverage 40% < 60% cycle 2; largest uncovered: mod.py (3 lines)", 6, 5)


@pytest.mark.parametrize("hardened", [False, True])
@pytest.mark.parametrize("open_cycle", [2, 3])
def test_one_open_cycle_still_checks_overlap(tmp_path, hardened, open_cycle):
    rd = _setup(tmp_path, floor=2, cycles=(2, 3))
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    _change(rd, cycle=open_cycle, findings=[{"description": "open"}], findings_count=1)
    _failure(_verify(tmp_path, hardened=hardened), "Jaccard overlap 1.00 > 0.8 c2-c3", 7,
             5 if hardened else 6)


@pytest.mark.parametrize("hardened", [False, True])
def test_closed_pair_does_not_skip_later_open_pair(tmp_path, hardened):
    rd = _setup(tmp_path, floor=3, cycles=(2, 3, 4))
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    _change(rd, cycle=4, findings=[{"description": "open"}], findings_count=1)
    _failure(_verify(tmp_path, hardened=hardened), "Jaccard overlap 1.00 > 0.8 c2-c4", 7,
             5 if hardened else 6)


@pytest.mark.parametrize("hardened", [False, True])
def test_exact_overlap_limit_is_accepted(tmp_path, hardened):
    rd = _setup(tmp_path, floor=2, cycles=(2, 3), findings=[{"description": "open"}])
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    for perspective in (1, 2, 3):
        _change(rd, cycle=3, perspective=perspective,
                code_excerpts=[dict(EXCERPT, end_line=4, content="a\nb\nc\nd")],
                covered_line_ranges=[{"file": "mod.py", "start": 1, "end": 4}])
    result = _verify(tmp_path, hardened=hardened)
    assert (result.passed, result.reason, result.checks_run, result.checks_passed) == (
        True, "all 8 checks passed", 8, 8)


@pytest.mark.parametrize("hardened", [False, True])
def test_coverage_percentage_does_not_round_up_below_floor(tmp_path, hardened):
    lines = [f"line-{index}" for index in range(1, 101)]
    diff = "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -0,0 +1,100 @@\n"
    diff += "".join(f"+{line}\n" for line in lines)
    sha = hashlib.sha256(diff.encode()).hexdigest()
    excerpt = dict(EXCERPT, end_line=59, content="\n".join(lines[:59]))
    rd = _setup(tmp_path, excerpts=[excerpt])
    (tmp_path / "mod.py").write_text("\n".join(lines) + "\n")
    for perspective in (1, 2, 3):
        _change(rd, perspective=perspective, diff_sha256=sha,
                covered_line_ranges=[{"file": "mod.py", "start": 1, "end": 59}])
    result = run_verify(tmp_path, sha, parse_diff_files(diff),
                        diff_text=diff, hardened=hardened)
    _failure(result, "coverage 59% < 60% cycle 2; largest uncovered: mod.py (41 lines)",
             6, 4 if hardened else 5)


def test_deletion_hunk_does_not_skip_later_unwitnessed_hunk(tmp_path):
    diff = (
        "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n"
        "@@ -1 +0,0 @@\n-removed\n@@ -3 +2 @@\n-old\n+new\n"
    )
    sha = hashlib.sha256(diff.encode()).hexdigest()
    rd = _setup(tmp_path, excerpts=[])
    for perspective in (1, 2, 3):
        _change(rd, perspective=perspective, diff_sha256=sha)
    result = run_verify(tmp_path, sha, parse_diff_files(diff), diff_text=diff)
    _failure(result, "unwitnessed hunk mod.py:2-2", 5, 4)


def test_legacy_incomplete_pass_keeps_all_previous_check_coordinates(tmp_path):
    rd = _setup(tmp_path)
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    _change(rd, pass_status="error")
    _failure(_verify(tmp_path, hardened=False),
             "pass did not complete: c2p1 status=error -- that pass contributed no review, so the cycle cannot attest",
             8, 7)


def test_anchor_missing_file_reports_empty_identity(tmp_path):
    rd = _setup(tmp_path)
    _change(rd, anchors=[{}])
    _failure(_verify(tmp_path), "anchor file  not in diff", 3, 2)


@pytest.mark.parametrize("hardened", [False, True])
@pytest.mark.parametrize("has_diff", [False, True])
def test_fallback_log_matches_selected_verification_path(tmp_path, caplog, hardened, has_diff):
    _setup(tmp_path)
    (tmp_path / "mod.py").write_text("a\nb\nc\nd\ne\n")
    caplog.set_level(logging.INFO, logger="code_forge.verify")
    result = _verify(tmp_path, hardened=hardened, diff_text=DIFF if has_diff else None)
    assert result.passed is True
    messages = [r.getMessage() for r in caplog.records if r.name == "code_forge.verify"]
    expected = ["hardened=True but diff_text=None, using legacy checks"]
    assert messages == (expected if hardened and not has_diff else [])


def test_legacy_read_error_log_retains_os_error(tmp_path, caplog):
    _setup(tmp_path)
    path = tmp_path / "mod.py"
    path.mkdir()
    with pytest.raises(IsADirectoryError) as exc_info:
        path.read_text(encoding="utf-8")
    caplog.set_level(logging.WARNING)
    result = _verify(tmp_path, hardened=False)
    _failure(result, "excerpt line range error mod.py:1-5", 5, 4)
    assert [r.getMessage() for r in caplog.records] == [
        f"check 5 legacy: {exc_info.value}"]


@pytest.mark.parametrize("has_left,has_right", [(False, False), (False, True), (True, False)])
def test_empty_diff_overlap_retains_empty_coverage_verdict(tmp_path, has_left, has_right):
    _setup(tmp_path, floor=2, cycles=(2, 3), excerpts=[], findings=[{"description": "open"}])
    binary_diff = "diff --git a/mod.py b/mod.py\nBinary files a/mod.py and b/mod.py differ\n"
    sha = hashlib.sha256(binary_diff.encode()).hexdigest()
    rd = tmp_path / ".code-forge" / "receipts"
    for cycle, present in ((2, has_left), (3, has_right)):
        for perspective in (1, 2, 3):
            _change(rd, cycle=cycle, perspective=perspective, diff_sha256=sha,
                    code_excerpts=[EXCERPT] if present else [])
    result = run_verify(tmp_path, sha, {}, diff_text=binary_diff)
    _failure(result,
             "no excerpt coverage in cycles 2 and 3 (findings present but excerpts empty)",
             7, 5)


@pytest.mark.parametrize("mode", ["missing", "mismatch", "complete"])
def test_legacy_file_verification_reports_exact_result(tmp_path, mode):
    _setup(tmp_path)
    if mode != "missing":
        (tmp_path / "mod.py").write_text("wrong\n" if mode == "mismatch" else "a\nb\nc\nd\ne\n")
    result = _verify(tmp_path, hardened=False)
    if mode == "missing":
        _failure(result, "excerpt file missing: mod.py (c2p1)", 5, 4)
    elif mode == "mismatch":
        _failure(result, "excerpt mismatch mod.py:1-5 c2p1", 5, 4)
    else:
        assert result.passed is True
        assert (result.reason, result.checks_run, result.checks_passed) == ("all 8 checks passed", 8, 8)

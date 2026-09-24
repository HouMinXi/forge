import hashlib
import json
import re
from pathlib import Path

import pytest

from code_forge.verify import (
    _coverage_failure_detail,
    _validate_receipt_schema,
    parse_diff_files,
    run_verify,
)

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

_SKILLS = ["qodo-review", "code-review-expert", "adversarial-qe"]

def _receipt(cycle, pass_n, diff_sha, covered_start=1, covered_end=50):
    return {
        "cycle": cycle, "pass": pass_n,
        "skill": _SKILLS[(pass_n - 1) % len(_SKILLS)],
        "diff_sha256": diff_sha,
        "timestamp": "2026-05-28T10:%02d:00Z" % (cycle * 3 + pass_n),
        "findings_count": 0, "findings": [],
        "anchors": [{"file": "src/f.py", "line": 1, "text": "def f():"}],
        "code_excerpts": [
            {"file": "src/f.py", "start_line": 1, "end_line": 2,
             "content": "def f():\n    return 1\n",
             "rationale": "checked"}
        ],
        "covered_line_ranges": [
            {"file": "src/f.py", "start": covered_start, "end": covered_end}
        ],
    }

def _write_all(rd, diff_sha, vary=True):
    for c in range(1, 4):
        off = (c - 1) * 10 if vary else 0
        for p in range(1, 4):
            name = "receipt-c%dp%d.json" % (c, p)
            (rd / name).write_text(json.dumps(
                _receipt(c, p, diff_sha, 1 + off, 45 + off)
            ))

def _nine_with_one_field_set(tmp_path, field, value):
    """Write 9 valid receipts, then set one top-level field on c2p1 to
    value -- which may be any JSON-serializable type, not just the
    schema-correct one. Proves a schema violation is reported by name,
    not crashed past and not silently accepted."""
    rd = tmp_path / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
    sha = _sha("diff")
    _write_all(rd, sha)
    bad = _receipt(2, 1, sha)
    bad[field] = value
    (rd / "receipt-c2p1.json").write_text(json.dumps(bad))
    return sha

class TestVerifyChecks:
    def test_pass_complete(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed

    def test_fail_missing(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        for c in range(1, 4):
            for p in range(1, 3):
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(_receipt(c, p, sha)))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed

    def test_fail_stale_hash(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        _write_all(rd, "old-hash")
        r = run_verify(tmp_path, "new-hash", {"src/f.py": list(range(1, 51))})
        assert not r.passed

    def test_fail_high_jaccard(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        _write_all(rd, sha, vary=False)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed

    def test_fail_low_coverage(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        for c in range(1, 4):
            for p in range(1, 4):
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(
                    _receipt(c, p, sha, covered_start=1, covered_end=5)
                ))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 201))})
        assert not r.passed

class TestCorruptReceipt:
    """A receipt that cannot be parsed must fail verify, not crash it.

    The real incident: one receipt held a raw newline inside a JSON
    string value, so every code commit in the repo aborted on an
    unhandled JSONDecodeError while the hook reported "receipt
    verification failed" -- pointing the operator at the review
    instead of at the file.
    """

    def _nine_with_one_broken(self, tmp_path, broken_text):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        (rd / "receipt-c2p1.json").write_text(broken_text, encoding="utf-8")
        return sha

    def test_raw_control_char_reports_the_file(self, tmp_path):
        # Verbatim shape of the incident: unescaped newline in a value.
        broken = '{\n  "cycle": 2,\n  "pass": 1,\n  "skill": "qodo-review\ncode-review-expert"\n}\n'
        sha = self._nine_with_one_broken(tmp_path, broken)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    def test_corrupt_is_not_reported_as_missing(self, tmp_path):
        # Guards the tempting wrong fix: skipping a bad file would leave
        # 8 receipts and blame "missing receipts", hiding the corruption.
        sha = self._nine_with_one_broken(tmp_path, "{ not json at all")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "missing receipts" not in r.reason
        assert r.reason.startswith("corrupt receipt: ")
        assert "receipt-c2p1.json" in r.reason

    def test_truncated_json_reports_the_file(self, tmp_path):
        sha = self._nine_with_one_broken(tmp_path, '{"cycle": 2, "pass":')
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    def test_undecodable_bytes_report_the_file(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        _write_all(rd, sha)
        (rd / "receipt-c2p1.json").write_bytes(b"\xff\xfe\x00binary")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    def test_deeply_nested_json_reports_the_file(self, tmp_path):
        # json.loads raises RecursionError here, a RuntimeError that no
        # ValueError catch covers. Without naming it the guard leaks the
        # very crash it exists to prevent.
        deep = "[" * 100000 + "]" * 100000
        sha = self._nine_with_one_broken(tmp_path, deep)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    def test_oversized_int_reports_the_file(self, tmp_path):
        # Past sys.get_int_max_str_digits() json.loads raises a plain
        # ValueError, not a JSONDecodeError, so catching only the named
        # subclasses let a single receipt abort verify with a traceback.
        big = '{"cycle": 2, "pass": 1, "findings_count": ' + "9" * 5000 + "}"
        sha = self._nine_with_one_broken(tmp_path, big)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    @pytest.mark.parametrize("body", ["[1, 2, 3]", "42", '"a string"', "null", "true"])
    def test_non_object_json_reports_the_file(self, tmp_path, body):
        # These parse cleanly, so no exception guard sees them. Every check
        # downstream then calls .get() on the result and dies with an
        # AttributeError pointing into verify.py instead of at the file.
        sha = self._nine_with_one_broken(tmp_path, body)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    def test_unreadable_entry_reports_the_file(self, tmp_path):
        # glob returns directories too, and read_text on one raises
        # IsADirectoryError -- an OSError, outside the ValueError branch.
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        _write_all(rd, sha)
        (rd / "receipt-c2p1.json").unlink()
        (rd / "receipt-c2p1.json").mkdir()
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "receipt-c2p1.json" in r.reason

    def test_intact_receipts_still_pass(self, tmp_path):
        # The guard must not reject a healthy set.
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed

class TestReceiptSchema:
    """A receipt with a field of the wrong type must fail verify by name,
    not crash it and not silently pass. Schema validation is the single
    gate every one of the 7 checks in run_verify trusts, instead of each
    check carrying its own copy of the same defensive guard.

    Two of these are regression guards for mistakes made while building
    this fix: an early draft replaced a non-list anchors field with []
    instead of reporting it (turning corrupt data into a false pass), and
    a later draft derived the schema from the writer rather than from the
    receipts on disk, so it rejected every real receipt whose
    covered_line_ranges used the string shape.
    """

    @pytest.mark.parametrize("field,value,expected", [
        ("cycle", [2], "cycle must be an integer"),
        ("cycle", True, "cycle must be an integer"),
        ("pass", "1", "pass must be an integer"),
        ("timestamp", None, "timestamp must be a string"),
        ("timestamp", 123, "timestamp must be a string"),
        ("diff_sha256", 12345, "diff_sha256 must be a string"),
        ("findings_count", "0", "findings_count must be an integer"),
        ("findings", "not a list", "findings must be a list of objects"),
        ("findings", [1, 2], "findings must be a list of objects"),
        ("anchors", "not a list", "anchors must be a list of objects"),
        ("anchors", [1], "anchors must be a list of objects"),
        ("code_excerpts", "not a list", "code_excerpts must be a list of objects"),
        ("code_excerpts", [1, 2, 3], "code_excerpts must be a list of objects"),
    ])
    def test_malformed_top_level_field_reports_the_file(
        self, tmp_path, field, value, expected
    ):
        sha = _nine_with_one_field_set(tmp_path, field, value)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert r.reason.startswith("corrupt receipt: ")
        assert "receipt-c2p1.json" in r.reason
        assert expected in r.reason

    @pytest.mark.parametrize("list_field,item,expected", [
        ("code_excerpts",
         {"file": 5, "start_line": 1, "end_line": 1, "content": "x"},
         "code_excerpts.file must be a string"),
        ("code_excerpts",
         {"file": "x.py", "start_line": "1", "end_line": 1, "content": "x"},
         "code_excerpts.start_line must be an integer"),
        ("code_excerpts",
         {"file": "x.py", "start_line": 1, "end_line": None, "content": "x"},
         "code_excerpts.end_line must be an integer"),
        ("code_excerpts",
         {"file": "x.py", "start_line": 1, "end_line": 1, "content": 5},
         "code_excerpts.content must be a string"),
    ])
    def test_malformed_nested_field_reports_the_file(
        self, tmp_path, list_field, item, expected
    ):
        sha = _nine_with_one_field_set(tmp_path, list_field, [item])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert r.reason.startswith("corrupt receipt: ")
        assert "receipt-c2p1.json" in r.reason
        assert expected in r.reason

    def test_malformed_anchors_no_longer_silently_passes(self, tmp_path):
        sha = _nine_with_one_field_set(tmp_path, "anchors", "not a list")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        # The regression this guards was a silent PASS, so the inequality
        # below is the point. Assert the positive form too: != "all 7 checks
        # passed" alone would also be satisfied by an unrelated failure.
        assert r.reason != "all 7 checks passed"
        assert r.reason.startswith("corrupt receipt: ")
        assert "receipt-c2p1.json" in r.reason
        assert "anchors must be a list of objects" in r.reason

    @pytest.mark.parametrize("field", [
        "cycle", "pass", "findings_count", "diff_sha256", "timestamp",
        "findings", "anchors", "code_excerpts",
    ])
    def test_absent_top_level_field_reports_the_file(self, tmp_path, field):
        """A field that is missing entirely, not merely the wrong type.
        obj.get() returns None for both, but only the wrong-type case was
        covered -- absence deserves its own case so a future .get(field, X)
        default cannot quietly reintroduce the gap.
        """
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        bad = _receipt(2, 1, sha)
        del bad[field]
        (rd / "receipt-c2p1.json").write_text(json.dumps(bad))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert r.reason.startswith("corrupt receipt: ")
        assert "receipt-c2p1.json" in r.reason
        assert field in r.reason

    def test_absent_nested_field_reports_the_file(self, tmp_path):
        """Same, one level down: a code_excerpts item missing a subfield."""
        sha = _nine_with_one_field_set(
            tmp_path, "code_excerpts",
            [{"start_line": 1, "end_line": 1, "content": "x"}],
        )
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert "code_excerpts.file must be a string" in r.reason

    @pytest.mark.parametrize("ranges", [
        [{"file": "src/code_forge/llm_invoke.py", "start": 143, "end": 151}],
        ["SKILL.md:1-1400"],
        [],
    ])
    def test_real_covered_line_ranges_shapes_are_accepted(self, tmp_path, ranges):
        """Receipts on disk carry covered_line_ranges in both a dict shape and
        a "path:start-end" string shape. An earlier draft of the schema
        asserted the dict shape and rejected 11 of the 14 real receipts in
        this repo -- turning every commit into a corrupt-receipt failure, the
        same outage this fix exists to prevent, from the other direction.
        Nothing on the production path reads the field, so the schema must
        accept whatever is in it. Asserted against the gate itself: driving
        this through run_verify without diff_text would take the legacy
        branch into _covered(), whose crash on the string shape predates
        this change and is left alone here.
        """
        receipt = _receipt(2, 1, "abc")
        receipt["covered_line_ranges"] = ranges
        _validate_receipt_schema(receipt, "receipt-c2p1.json")

    def test_intact_receipts_still_pass_schema(self, tmp_path):
        """The schema gate must not reject a healthy set."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed

class TestTimestampMonotonic:
    """ITEM 5: timestamps must be non-decreasing in (cycle, pass) order."""

    def test_fail_timestamps_not_monotonic(self, tmp_path):
        """c2p1 timestamp earlier than c1p1 -> FAIL at check 4."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        # Corrupt c2p1 timestamp to be earlier than c1p1
        bad = json.loads((rd / "receipt-c2p1.json").read_text())
        bad["timestamp"] = "2026-05-28T09:00:00Z"  # earlier than c1p1's 10:04:00Z
        (rd / "receipt-c2p1.json").write_text(json.dumps(bad))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed
        assert r.checks_run == 4
        assert "timestamps not monotonic" in r.reason

class TestReceiptVerifyE2E:
    """End-to-end: receipt writer output must pass verify checks."""

    def test_receipt_writer_output_passes_verify(self, tmp_path):
        import datetime
        from unittest.mock import patch

        from code_forge.disposition import Disposition
        from code_forge.receipt import write_receipts
        from code_forge.state import StateFinding

        (tmp_path / "src").mkdir(parents=True)
        lines = ["line%d\n" % i for i in range(1, 81)]
        (tmp_path / "src" / "foo.py").write_text("".join(lines))
        diff_sha = _sha("diff")
        diff_files = {"src/foo.py": list(range(1, 81))}

        base = datetime.datetime(2026, 5, 28, 10, 0, 0,
                                 tzinfo=datetime.UTC)
        cycle_locs = [(10, 30, 50), (20, 40, 60), (30, 50, 70)]
        passes = ["qodo", "expert", "adversarial"]
        for round_idx in range(3):
            fake_now = base + datetime.timedelta(minutes=round_idx * 5)
            locs = cycle_locs[round_idx]
            findings = []
            for pi, pn in enumerate(passes):
                ln = locs[pi]
                findings.append(StateFinding(
                    id="l1-%s-fp%d%d" % (pn, round_idx, pi),
                    fingerprint="fp%d%d" % (round_idx, pi), source="L1",
                    disposition=Disposition.UNCERTAIN,
                    file="src/foo.py",
                    line_range=[ln, ln],
                    description="[%s] test finding r%dp%d" % (pn, round_idx, pi),
                ))
            with patch("code_forge.receipt.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = fake_now
                mock_dt.timedelta = datetime.timedelta
                mock_dt.timezone = datetime.timezone
                write_receipts(
                    receipts_dir=tmp_path / ".code-forge" / "receipts",
                    round_index=round_idx,
                    l1_findings=findings,
                    diff_sha256=diff_sha,
                    source_files=[Path("src/foo.py")],
                    cwd=tmp_path,
                    diff_files=diff_files,
                )

        r = run_verify(tmp_path, diff_sha, diff_files)
        assert r.passed, f"E2E failed: {r.reason}"

    @staticmethod
    def _run_with_failed_pass(tmp_path, failed_round):
        """Four rounds where one round's qodo pass never reached the model.

        A timed-out pass returns only the infra finding -- no code finding
        beside it, which is how it comes back off a real backend deadline.

        Returns the diff arguments so the caller can drive run_verify.
        """
        import datetime
        from unittest.mock import patch

        from code_forge.disposition import Disposition
        from code_forge.receipt import write_receipts
        from code_forge.state import StateFinding

        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "foo.py").write_text(
            "".join("line%d\n" % i for i in range(1, 81)))
        diff_sha = _sha("diff")
        diff_files = {"src/foo.py": list(range(1, 81))}

        base = datetime.datetime(2026, 5, 28, 10, 0, 0,
                                 tzinfo=datetime.UTC)
        passes = ["qodo", "expert", "adversarial"]
        # Spread far enough apart that the last three cycles stay under the
        # Jaccard similarity ceiling while each still clears the coverage
        # floor: findings cover +/-10 lines, so three per cycle is 63 of 80.
        cycle_locs = [(10, 30, 50), (11, 33, 55), (15, 40, 65), (20, 45, 70)]
        for round_idx in range(4):
            findings = []
            for pi, pn in enumerate(passes):
                if round_idx == failed_round and pn == "qodo":
                    findings.append(StateFinding(
                        id="l1-qodo-invoke-fail",
                        fingerprint="invoke-fail-qodo", source="INFRA",
                        disposition=Disposition.CONFIRMED,
                        file="<llm-invoke>", line_range=[0, 0],
                        description="L1 invoke failed: read deadline",
                    ))
                    continue
                ln = cycle_locs[round_idx][pi]
                findings.append(StateFinding(
                    id="l1-%s-fp%d%d" % (pn, round_idx, pi),
                    fingerprint="fp%d%d" % (round_idx, pi), source="L1",
                    disposition=Disposition.UNCERTAIN,
                    file="src/foo.py", line_range=[ln, ln],
                    description="[%s] finding r%dp%d" % (pn, round_idx, pi),
                ))
            with patch("code_forge.receipt.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = (
                    base + datetime.timedelta(minutes=round_idx * 5))
                mock_dt.timedelta = datetime.timedelta
                mock_dt.timezone = datetime.timezone
                write_receipts(
                    receipts_dir=tmp_path / ".code-forge" / "receipts",
                    round_index=round_idx,
                    l1_findings=findings,
                    diff_sha256=diff_sha,
                    source_files=[Path("src/foo.py")],
                    cwd=tmp_path,
                    diff_files=diff_files,
                )
        return diff_sha, diff_files

    def test_backend_failure_in_an_early_round_still_verifies(self, tmp_path):
        """A failed backend call must not cost the review its attestation.

        The pass that fails is recorded as a finding naming "<llm-invoke>",
        since no file is at fault. That is not a path, so the anchor check
        rejects it, and receipts are never pruned -- a single failure in
        round one outlives every clean round after it. Four rounds here, the
        failure in the first: the last three are spotless and verify still
        has to accept the set.

        Driven through run_verify rather than read off the receipt, because
        the anchor list is only wrong in the eyes of its consumer.
        """
        diff_sha, diff_files = self._run_with_failed_pass(tmp_path, 0)

        r = run_verify(tmp_path, diff_sha, diff_files)
        assert r.passed, f"backend failure blocked attestation: {r.reason}"

        # The failure is dropped as an anchor, not silenced: round one still
        # reports it, or the receipts would claim a pass that never ran.
        c1p1 = json.loads(
            (tmp_path / ".code-forge" / "receipts" / "receipt-c1p1.json")
            .read_text())
        assert any(f["file"] == "<llm-invoke>" for f in c1p1["findings"])
        assert not any(a["file"] == "<llm-invoke>" for a in c1p1["anchors"])

    def test_backend_failure_inside_the_attested_window_is_refused(
        self, tmp_path
    ):
        """The same failure in an attested cycle must still be refused.

        Dropping the anchor keeps a stale failure from outliving the rounds
        that followed it. It does not, and must not, make a cycle whose pass
        never ran look like one that did: that cycle really did read less of
        the diff, and the coverage floor is what says so. Pinned here so the
        line between the two cannot move by accident -- letting this through
        would attest a three-pass cycle that ran two.
        """
        diff_sha, diff_files = self._run_with_failed_pass(tmp_path, 3)

        r = run_verify(tmp_path, diff_sha, diff_files)
        assert not r.passed
        # Coverage is what catches it HERE, because this diff is small enough
        # that two passes cannot reach the floor alone. That is a property of
        # the fixture, not a guarantee -- on a larger diff the two healthy
        # passes clear 60% by themselves and this refusal disappears. The
        # structural backstop for that case is check 8, isolated in the test
        # below.
        assert "coverage" in r.reason, r.reason

    def test_a_pass_that_never_ran_is_refused_even_when_coverage_passes(
        self, tmp_path
    ):
        """The gap the coverage floor cannot close.

        Coverage is unioned across the passes of a cycle, so two passes that
        read enough of a large diff carry a third that never ran, and every
        other check is happy: the receipt has a matching hash, a monotonic
        timestamp, no anchor to contradict and no excerpt to disprove.
        pass_status is the only thing left that knows, and until check 8
        nothing read it.

        Built by taking a scenario that verifies clean and flipping one
        in-window pass_status, so nothing else can be the reason for the
        refusal.
        """
        import json
        diff_sha, diff_files = self._run_with_failed_pass(tmp_path, 0)
        assert run_verify(tmp_path, diff_sha, diff_files).passed

        receipts_dir = tmp_path / ".code-forge" / "receipts"
        target = None
        for p in sorted(receipts_dir.glob("*.json")):
            obj = json.loads(p.read_text())
            if obj["cycle"] == 4 and obj["pass"] == 2:
                target = (p, obj)
                break
        assert target is not None, "fixture shape changed: no c4p2 receipt"
        p, obj = target
        assert obj["pass_status"] == "completed"
        obj["pass_status"] = "timeout"
        p.write_text(json.dumps(obj))

        r = run_verify(tmp_path, diff_sha, diff_files)
        assert not r.passed
        assert "did not complete" in r.reason, r.reason
        assert "c4p2" in r.reason, r.reason

    def test_a_receipt_without_pass_status_is_still_accepted(self, tmp_path):
        """Absence is not failure.

        pass_status is not documented in SKILL.md, so a reviewer writing a
        receipt by hand from the documented shape omits it, and receipts
        written before the field existed lack it too. A check that refused on
        a missing field would reject good receipts -- the failure this file's
        schema comment was written about, and one this project has already
        shipped once.
        """
        import json
        diff_sha, diff_files = self._run_with_failed_pass(tmp_path, 0)
        assert run_verify(tmp_path, diff_sha, diff_files).passed

        receipts_dir = tmp_path / ".code-forge" / "receipts"
        stripped = 0
        for p in sorted(receipts_dir.glob("*.json")):
            obj = json.loads(p.read_text())
            if obj.pop("pass_status", None) is not None:
                p.write_text(json.dumps(obj))
                stripped += 1
        assert stripped > 0, "fixture wrote no pass_status to strip"

        r = run_verify(tmp_path, diff_sha, diff_files)
        assert r.passed, r.reason

# ---------------------------------------------------------------------------
# Hardened-verify fixtures
# ---------------------------------------------------------------------------

# Valid 3-hunk unified diff; parseable by unidiff.
# foo.py hunks: lines 1-3 (y=2 added) and 6-8 (b=2 added).
# bar.py hunk: lines 1-3 (q=2 added).
# parse_diff_files returns foo.py:[1..3,6..8], bar.py:[1..3] = 9 lines total.
_HARDEN_DIFF = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,2 +1,3 @@\n"
    " x = 1\n"
    "+y = 2\n"
    " z = 3\n"
    "@@ -5,2 +6,3 @@\n"
    " a = 1\n"
    "+b = 2\n"
    " c = 3\n"
    "diff --git a/bar.py b/bar.py\n"
    "--- a/bar.py\n"
    "+++ b/bar.py\n"
    "@@ -1,2 +1,3 @@\n"
    " p = 1\n"
    "+q = 2\n"
    " r = 3\n"
)

# Excerpts that witness all 3 hunks and match post-image content exactly.
_EXCERPTS_OK = [
    {"file": "foo.py", "start_line": 1, "end_line": 3,
     "content": "x = 1\ny = 2\nz = 3"},
    {"file": "foo.py", "start_line": 6, "end_line": 8,
     "content": "a = 1\nb = 2\nc = 3"},
    {"file": "bar.py", "start_line": 1, "end_line": 3,
     "content": "p = 1\nq = 2\nr = 3"},
]

def _hreceipt(cycle, pass_n, diff_sha, excerpts=None, findings=None,
              covered_line_ranges=None):
    """Build one receipt for hardened-verify tests."""
    return {
        "cycle": cycle,
        "pass": pass_n,
        "skill": ["qodo-review", "code-review-expert", "adversarial-qe"][pass_n - 1],
        "diff_sha256": diff_sha,
        # Monotonic: cycle*3+pass_n gives 4..12 across (c,p) in file-sort order.
        "timestamp": "2026-06-07T10:%02d:00Z" % (cycle * 3 + pass_n),
        "findings_count": len(findings or []),
        "findings": findings if findings is not None else [],
        "anchors": [],
        "code_excerpts": excerpts if excerpts is not None else list(_EXCERPTS_OK),
        "covered_line_ranges": (covered_line_ranges
                                if covered_line_ranges is not None else []),
    }

def _write_hardened(rd, diff_sha, excerpts=None, findings=None):
    """Write 9 receipts (3 cycles x 3 passes) for hardened-verify tests."""
    for c in range(1, 4):
        for p in range(1, 4):
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
                json.dumps(_hreceipt(c, p, diff_sha,
                                     excerpts=excerpts, findings=findings))
            )

class TestHardenedVerify:
    """Tests that run_verify with diff_text=DIFF enters the hardened branch.

    Every assertion targets a reason string that ONLY the hardened branch
    produces, so a green assertion proves hardened execution (not legacy).
    """

    def _rd(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        return rd

    def test_witnessed_pass(self, tmp_path):
        """Hardened path: all hunks witnessed, correct content, >= 60% coverage."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha)          # _EXCERPTS_OK, findings=[]
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert r.passed, r.reason

    def test_unwitnessed_hunk_fail(self, tmp_path):
        """STEP A: bar.py hunk has no overlapping excerpt -> unwitnessed hunk."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        # Omit bar.py excerpt entirely -- STEP A must reject.
        partial = [
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\ny = 2\nz = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 8,
             "content": "a = 1\nb = 2\nc = 3"},
        ]
        _write_hardened(rd, sha, excerpts=partial)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "unwitnessed hunk" in r.reason

    def test_diff_marker_content_fail(self, tmp_path):
        """STEP C (Q2 guard): '+y = 2' in content mismatches post-image 'y = 2'."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        dirty = [
            # Leading '+' makes line 2 mismatch post-image.
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\n+y = 2\nz = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 8,
             "content": "a = 1\nb = 2\nc = 3"},
            {"file": "bar.py", "start_line": 1, "end_line": 3,
             "content": "p = 1\nq = 2\nr = 3"},
        ]
        _write_hardened(rd, sha, excerpts=dirty)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "excerpt content mismatch at" in r.reason

    def test_low_coverage_fail(self, tmp_path):
        """Check 6: per-cycle coverage 3/9 = 33% < 60% -> fail."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        # One excerpt per hunk at context-only lines (1,6,1) -- witnesses all
        # hunks for STEP A but covers only lines NOT in parse_diff_files output
        # if parse_diff_files returns added lines only. Even if it returns full
        # hunk ranges (1-3, 6-8, 1-3), 3 single-line excerpts = 3/9 = 33% < 60%.
        sparse = [
            {"file": "foo.py", "start_line": 3, "end_line": 3,
             "content": "z = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 6,
             "content": "a = 1"},
            {"file": "bar.py", "start_line": 3, "end_line": 3,
             "content": "r = 3"},
        ]
        _write_hardened(rd, sha, excerpts=sparse)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "< 60%" in r.reason
        # The failure must name where the missing coverage is, not
        # only a percentage. Of the nine diff lines, the three sparse
        # excerpts cover 2 in foo.py and 1 in bar.py, leaving 4 and 2
        # uncovered; the exact substring pins the order (foo.py leads)
        # and the line counts together.
        assert "largest uncovered: foo.py (4 lines), bar.py (2 lines)" \
            in r.reason
        # The message keeps its percentage prefix in front of the detail.
        assert re.match(r"coverage \d+% < 60% cycle \d+", r.reason)

    def test_wide_range_with_thin_content_earns_no_extra_coverage(self, tmp_path):
        """Check 6 credits only lines an excerpt actually shows.

        Each excerpt below spans a whole hunk but pastes a single line. The
        content that is present matches the post-image, so the excerpt check
        passes -- it only compares lines the content actually has. Crediting
        the declared span would score 9/9 and pass the floor on three lines
        of evidence; crediting what is shown scores 3/9 and fails.
        """
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        inflated = [
            {"file": "foo.py", "start_line": 1, "end_line": 1,
             "content": "x = 1"},
            {"file": "foo.py", "start_line": 6, "end_line": 6,
             "content": "a = 1"},
            {"file": "bar.py", "start_line": 1, "end_line": 1,
             "content": "p = 1"},
        ]
        _write_hardened(rd, sha, excerpts=inflated)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "< 60%" in r.reason

    def test_repeated_excerpts_still_read_as_rubber_stamp(self, tmp_path):
        """Check 7 shares _excerpt_covered with check 6.

        Capping credit at the shown lines shrinks what check 7 compares, so
        pin the case it exists to catch: cycles that paste the same excerpts
        still produce identical coverage sets and still trip the overlap
        ceiling. Findings are present because check 7 skips pairs where both
        cycles came back clean.
        """
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x",
                                           "disposition": "CONFIRMED"}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "Jaccard" in r.reason

    def test_dismissed_findings_with_identical_excerpts_pass(self, tmp_path):
        """Check 7 skips pairs whose findings are all closed.

        A dismissed finding is not an open product defect. Identical
        excerpts across cycles must not fail the overlap ceiling.
        """
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x",
                                           "disposition": "DISMISSED"}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert r.passed, r.reason

    def test_fixed_findings_with_identical_excerpts_pass(self, tmp_path):
        """FIXED is closed; identical excerpts must not trip Jaccard."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x",
                                           "disposition": "FIXED"}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert r.passed, r.reason

    def test_uncertain_findings_with_identical_excerpts_fail(self, tmp_path):
        """UNCERTAIN still counts as an open finding for the overlap gate."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x",
                                           "disposition": "UNCERTAIN"}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "Jaccard" in r.reason

    def test_style_findings_with_identical_excerpts_pass(self, tmp_path):
        """STYLE is non-blocking; identical excerpts must not trip Jaccard."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x",
                                           "disposition": "STYLE"}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert r.passed, r.reason

    def test_unhashable_disposition_does_not_crash_verify(self, tmp_path):
        """A list disposition is open, but verify must return a result."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x",
                                           "disposition": ["DISMISSED"]}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "Jaccard" in r.reason

    def test_missing_disposition_is_treated_as_open(self, tmp_path):
        """A finding with no disposition key is still an open defect."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x"}])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "Jaccard" in r.reason

    def test_missing_field_fail(self, tmp_path):
        """Excerpt with start_line missing is now caught by schema
        validation at load time, before any of the 7 checks run -- not by
        STEP 0 inside check 5. STEP 0 stays in place as defense in depth
        but can no longer be reached by this particular input."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        bad = [
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\ny = 2\nz = 3"},
            # Missing start_line -- rejected by schema validation.
            {"file": "foo.py", "end_line": 8, "content": "a = 1\nb = 2\nc = 3"},
            {"file": "bar.py", "start_line": 1, "end_line": 3,
             "content": "p = 1\nq = 2\nr = 3"},
        ]
        _write_hardened(rd, sha, excerpts=bad)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert r.reason.startswith("corrupt receipt: ")
        assert "code_excerpts.start_line must be an integer" in r.reason

    def test_excerpts_outside_the_attested_window_do_not_vouch(self, tmp_path):
        """Check 5 must see only the attested window's excerpts.

        With required_cycles=1 and an older full-coverage cycle still on
        disk, only cycle 2 is being attested. If cycle 1's excerpts could
        witness hunks, verify would PASS on a diff the attested cycle
        never reviewed -- the window that did the reviewing is not the
        window the gate is vouching for.
        """
        rd = self._rd(tmp_path)
        (tmp_path / ".code-forge" / "gate.yaml").write_text(
            "verify:\n  required_cycles: 1\n")
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        partial = [
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\ny = 2\nz = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 8,
             "content": "a = 1\nb = 2\nc = 3"},
        ]
        for p in range(1, 4):
            (rd / ("receipt-c1p%d.json" % p)).write_text(
                json.dumps(_hreceipt(1, p, sha)))  # full coverage, older cycle
        for p in range(1, 4):
            (rd / ("receipt-c2p%d.json" % p)).write_text(
                json.dumps(_hreceipt(2, p, sha, excerpts=partial)))
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "unwitnessed hunk" in r.reason, r.reason

    def test_legacy_excerpts_outside_the_attested_window_do_not_vouch(
        self, tmp_path,
    ):
        """Legacy (working-tree) check 5 must scope to last_n too.

        The hardened path only lets the attested window's excerpts
        vouch; the legacy path had the same hole -- a stale cycle's
        excerpts could fail or pass the check for a diff the attested
        cycle never reviewed. Cycle 1's content deliberately does not
        match the working tree: if it were checked, verify would fail
        on it instead of attesting cycle 2.
        """
        rd = self._rd(tmp_path)
        (tmp_path / ".code-forge" / "gate.yaml").write_text(
            "verify:\n  required_cycles: 1\n")
        src = tmp_path / "src"
        src.mkdir()
        lines = ["line%d" % i for i in range(1, 11)]
        (src / "f.py").write_text("\n".join(lines) + "\n")
        sha = _sha("diff")
        diff_files = {"src/f.py": list(range(1, 11))}
        stale = [
            {"file": "src/f.py", "start_line": 1, "end_line": 3,
             "content": "WRONG\ncontent\nhere"},
        ]
        good = [
            {"file": "src/f.py", "start_line": 1, "end_line": 6,
             "content": "\n".join(lines[:6])},
        ]
        full_cover = [{"file": "src/f.py", "start": 1, "end": 10}]
        for p in range(1, 4):
            (rd / ("receipt-c1p%d.json" % p)).write_text(
                json.dumps(_hreceipt(1, p, sha, excerpts=stale,
                                     covered_line_ranges=full_cover)))
        for p in range(1, 4):
            (rd / ("receipt-c2p%d.json" % p)).write_text(
                json.dumps(_hreceipt(2, p, sha, excerpts=good,
                                     covered_line_ranges=full_cover)))
        r = run_verify(tmp_path, sha, diff_files)
        assert r.passed, r.reason

class TestCrossRepoGuard:
    """cross_repo.py must route through _load_receipts, not bare json.loads.
    A receipt with a raw unescaped newline inside a JSON string must report
    the filename, not crash with a traceback."""

    def test_malformed_receipt_reports_filename(self, tmp_path):
        from code_forge.verify import _load_receipts
        rd = tmp_path / "receipts"
        rd.mkdir()
        # Real corruption: raw newline inside a JSON string value
        bad = '{"cycle": 1, "pass": 1, "diff_sha256": "abc", ' \
              '"timestamp": "2026-01-01T00:00:00Z", ' \
              '"findings_count": 0, "findings": [], "anchors": [], ' \
              '"code_excerpts": [], "covered_line_ranges": []}\n' \
              '{"cycle": 2, "pass": 1, "diff_sha256": "abc", ' \
              '"timestamp": "2026-01-01T00:00:01Z", ' \
              '"findings_count": 0, "findings": [], "anchors": [], ' \
              '"code_excerpts": [], "covered_line_ranges": []}'
        (rd / "receipt-c2p1.json").write_text(bad)
        from code_forge.errors import CorruptedReceiptError
        with pytest.raises(CorruptedReceiptError, match="receipt-c2p1.json"):
            _load_receipts(rd)

class TestInvertedExcerptRange:
    """_validate_receipt_schema must reject start_line > end_line."""

    def test_inverted_range_rejected(self):
        receipt = _receipt(1, 1, "abc")
        receipt["code_excerpts"] = [
            {"file": "src/f.py", "start_line": 10, "end_line": 3,
             "content": "code", "rationale": "r"}
        ]
        from code_forge.errors import CorruptedReceiptError
        with pytest.raises(CorruptedReceiptError, match="start_line 10 > end_line 3"):
            _validate_receipt_schema(receipt, "test.json")

    def test_equal_range_accepted(self):
        receipt = _receipt(1, 1, "abc")
        receipt["code_excerpts"] = [
            {"file": "src/f.py", "start_line": 5, "end_line": 5,
             "content": "line", "rationale": "r"}
        ]
        _validate_receipt_schema(receipt, "test.json")

    def test_normal_range_accepted(self):
        receipt = _receipt(1, 1, "abc")
        _validate_receipt_schema(receipt, "test.json")

def _write_cycles(rd, diff_sha, cycles):
    """Write receipts for arbitrary cycle numbers (list of ints), 3 passes each.
    Total receipts = len(cycles) * 3. For <3 cycles this is <9, which
    triggers the 'missing receipts' check before the cycle check.
    Coverage range spans the full diff (lines 1-50) to avoid triggering
    the 60% floor on any cycle."""
    for c in cycles:
        for p in range(1, 4):
            name = "receipt-c%dp%d.json" % (c, p)
            (rd / name).write_text(json.dumps(
                _receipt(c, p, diff_sha, 1, 50)
            ))

class TestLastThreeConsecutiveCycles:
    """ITEM A: verify the LAST 3 consecutive cycles, whatever their numbers."""

    def test_cycles_2_3_4_pass(self, tmp_path):
        """Cycles 2-4 complete -> PASS (last 3 consecutive)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [2, 3, 4])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert r.passed, f"last 3 consecutive should pass, got: {r.reason}"

    def test_cycles_1_2_4_fail(self, tmp_path):
        """Cycles 1,2,4 -> FAIL (last 3 not consecutive: 2,3,4 missing 3)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 2, 4])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed, f"cycles 1,2,4 should fail, got: {r.reason}"
        assert "not consecutive" in r.reason

    def test_cycles_1_2_fail(self, tmp_path):
        """Only cycles 1-2 -> FAIL (fewer than 3 cycles).
        Need 9+ receipts to pass the length check, but only 2 unique cycles."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        # Write 5 passes per cycle (cycle 1 uses passes 1-5, cycle 2 uses passes 1-5)
        # to get 10 receipts (>9) but only 2 unique cycles
        skills = ["qodo-review", "code-review-expert", "adversarial-qe",
                  "qodo-review", "code-review-expert"]
        for c in [1, 2]:
            for p in range(1, 6):
                name = "receipt-c%dp%d.json" % (c, p)
                receipt = _receipt(c, p, sha, 1, 50)
                receipt["skill"] = skills[p - 1]
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed, f"cycles 1,2 should fail, got: {r.reason}"
        assert "fewer than 3 cycles" in r.reason

    def test_cycles_5_6_7_pass(self, tmp_path):
        """Cycles 5-7 -> PASS (last 3 consecutive, high numbers)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [5, 6, 7])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed, f"cycles 5-7 should pass, got: {r.reason}"

    def test_cycles_1_2_3_4_pass(self, tmp_path):
        """Cycles 1-4 -> PASS (last 3 are 2,3,4, consecutive)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 2, 3, 4])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed

    def test_cycles_1_3_4_fail(self, tmp_path):
        """Cycles 1,3,4 -> FAIL (last 3 not consecutive: 2 missing)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 3, 4])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed, f"cycles 1,3,4 should fail, got: {r.reason}"
        assert "not consecutive" in r.reason

    def test_cycles_2_3_4_missing_pass_fail(self, tmp_path):
        """Cycles 2-4 consecutive but cycle 4 missing pass 3 -> FAIL."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        # Write cycles 2-3 complete, cycle 4 only passes 1-2 (8 receipts total,
        # but we need 9+ to pass the length check; add cycle 1 with 3 passes)
        for c in [1, 2, 3]:
            for p in range(1, 4):
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(_receipt(c, p, sha, 1, 50)))
        for p in range(1, 3):  # only passes 1-2 for cycle 4
            name = "receipt-c4p%d.json" % (p,)
            (rd / name).write_text(json.dumps(_receipt(4, p, sha, 1, 50)))
        # 11 receipts total, last 3 cycles are 2,3,4 but 4 is missing pass 3
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed, f"missing pass should fail, got: {r.reason}"
        assert "missing cycle 4/pass 3" in r.reason

    def test_pass_outside_the_three_review_passes_is_rejected(self, tmp_path):
        """A counted cycle carrying a pass 4 -> FAIL.

        Three skills run per cycle, so pass 4 is a receipt for a pass nobody
        ran. Asking only that passes 1-3 be PRESENT accepts it silently, and
        the extra receipt then feeds every downstream check as if it were
        real work.
        """
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [2, 3, 4])
        (rd / "receipt-c3p4.json").write_text(
            json.dumps(_receipt(3, 4, sha, 1, 50)))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed, f"pass 4 should fail, got: {r.reason}"
        assert "outside the three review passes" in r.reason

    def test_two_digit_cycle_numbers_are_not_read_as_out_of_order(self, tmp_path):
        """Cycles 9-11, written in order -> PASS.

        Receipts used to be ordered by filename, where "receipt-c10p1" sorts
        ahead of "receipt-c9p1". The monotonic-timestamp check then read a
        correctly written set as out of order, so a review that went past nine
        cycles could never be verified -- the one case the last-three rule
        exists to serve.
        """
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [9, 10, 11])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed, f"cycles 9-11 should pass, got: {r.reason}"

class TestOutOfHunkExcerpts:
    """ITEM B: out-of-hunk excerpts allowed when STEP A coverage satisfied."""

    def test_context_read_outside_the_diff_passes_in_its_own_field(self, tmp_path):
        """Code read for orientation, recorded as a context quote -> PASS.

        The case this exists for: a reviewer quotes a few lines near the change
        to explain it. Those lines are not in the diff, so no check here can
        confirm them -- which is exactly why they go in context_quotes, where
        they claim nothing, instead of code_excerpts, where they would be
        indistinguishable from lines that were confirmed.
        """
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        # Correct unidiff format
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        # 3 cycles, 3 passes each, excerpt in hunk + 1 stray (lines 10-12, beyond diff)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # The in-hunk excerpt content must match post-image lines 1-2
                receipt["code_excerpts"][0]["content"] = "def f():\n    return 2"
                # Read for orientation, outside every hunk, so it goes here
                # rather than into code_excerpts.
                receipt["context_quotes"] = [{
                    "file": "src/f.py",
                    "content": "# context\n# more context\n# end",
                    "rationale": "surrounding code, read but not checked"
                }]
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert r.passed, r.reason

    def test_the_same_lines_left_in_code_excerpts_are_rejected(self, tmp_path):
        """The counterpart: identical content, wrong field -> FAIL.

        Without this the pair above proves only that context_quotes is
        tolerated, not that code_excerpts stopped tolerating the same thing.
        """
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 2\n")
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0]["content"] = "def f():\n    return 2"
                receipt["code_excerpts"].append({
                    "file": "src/f.py", "start_line": 10, "end_line": 12,
                    "content": "# context\n# more context\n# end",
                    "rationale": "context"
                })
                (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
                    json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed
        assert "belongs in context_quotes" in r.reason

    def test_excerpt_inverted_range_fails_at_schema(self, tmp_path):
        """start_line > end_line is nonsense and fails at schema load."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0]["start_line"] = 5
                receipt["code_excerpts"][0]["end_line"] = 2
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed, f"inverted range should fail, got: {r.reason}"
        assert "start_line 5 > end_line 2" in r.reason

    def test_excerpt_tail_outside_the_post_image_is_rejected(self, tmp_path):
        """A genuine excerpt whose tail claims lines beyond the post-image
        must fail: those lines were never compared against anything, and a
        receipt that carries them reads as 'the reviewer verified these'.
        The head matches the post-image verbatim so only the smuggled tail
        can convict."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # Lines 1-2 match the post-image verbatim; line 3 does not
                # exist in the two-line post-image, so the excerpt smuggles
                # one line nobody can check.
                receipt["code_excerpts"][0] = {
                    "file": "src/f.py", "start_line": 1, "end_line": 3,
                    "content": "def f():\n    return 2\n    extra()",
                    "rationale": "checked",
                }
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert r.passed, r.reason

    def test_excerpt_content_beyond_the_declared_range_is_rejected(self, tmp_path):
        """Content lines that map to no claimed line number are never
        compared against the post-image, so an excerpt declaring one
        line but carrying two must fail even when the first line is
        verbatim."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # Declares one line; the second content line rides along
                # unchecked without the range guard.
                receipt["code_excerpts"][0] = {
                    "file": "src/f.py", "start_line": 1, "end_line": 1,
                    "content": "def f():\n    fabricated()",
                    "rationale": "checked",
                }
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed, f"extra content must fail, got: {r.reason}"
        # +/-1 count slack is producer-side only; with a post-image the
        # extra line is caught as a content / range failure.
        assert (
            "declares 1 lines but carries 2" in r.reason
            or "content mismatch" in r.reason
            or "outside the diff post-image" in r.reason
        )

    def test_misnumbered_excerpt_reports_the_offset(self, tmp_path):
        """A reviewer that ignored the annotated line numbers produces
        content matching the file at a constant offset. The failure must
        name that offset instead of a bare content mismatch, so the
        diagnosis does not point at the wrong line."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,4 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    x = 1\n"
            "+    return 2\n"
            "+    return 3\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    x = 1\n    return 2\n    return 3\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # Content is verbatim for lines 2-4 but claims 3-5: a
                # constant +1 misnumbering, exactly the reviewer failure.
                receipt["code_excerpts"] = [
                    {
                        "file": "src/f.py", "start_line": 1, "end_line": 4,
                        "content": (
                            "def f():\n    x = 1\n"
                            "    return 2\n    return 3"
                        ),
                    },
                    {
                        "file": "src/f.py", "start_line": 3, "end_line": 5,
                        "content": "    x = 1\n    return 2\n    return 3",
                    },
                ]
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert r.passed, (
            f"one-line slip must not fail attestation, got: {r.reason}"
        )

    def test_misnumbered_excerpt_reports_positive_offset(self, tmp_path):
        """The offset search is symmetric, so the +N direction needs its
        own pin: content claimed at 1-2 that actually sits at 2-3."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,1 +1,3 @@\n"
            " def f():\n"
            "+    x = 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    x = 1\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # Claims lines 1-2 but carries lines 2-3 verbatim: a
                # constant +1 misnumbering, the mirror of the -1 case.
                receipt["code_excerpts"] = [
                    {
                        "file": "src/f.py", "start_line": 1, "end_line": 3,
                        "content": "def f():\n    x = 1\n    return 2",
                    },
                    {
                        "file": "src/f.py", "start_line": 1, "end_line": 2,
                        "content": "    x = 1\n    return 2",
                    },
                ]
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert r.passed, (
            f"one-line slip must not fail attestation, got: {r.reason}"
        )

    def test_partial_shift_match_is_not_called_misnumbered(self, tmp_path):
        """A shift must be vouched for by every claimed line. Two lines
        matching at +1 while the third's shifted position falls outside
        the post-image is a fabricated tail, not a misnumbering."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,1 +1,3 @@\n"
            " def f():\n"
            "+    x = 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    x = 1\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # Lines 1-2 match at +1 (they are lines 2-3's text); the
                # third line has no post-image position at any delta.
                receipt["code_excerpts"][0] = {
                    "file": "src/f.py", "start_line": 1, "end_line": 3,
                    "content": "    x = 1\n    return 2\n    fabricated",
                }
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed
        assert "misnumbered" not in r.reason, (
            f"a partial shift match must not convict as misnumbering: {r.reason}"
        )

    def test_fabricated_excerpt_reports_mismatch_not_offset(self, tmp_path):
        """Content that matches at no offset is fabrication, not
        misnumbering; the message must stay a content mismatch."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0]["content"] = (
                    "def f():\n    return 999\n"
                )
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed
        assert "content mismatch" in r.reason
        assert "misnumbered" not in r.reason

    def test_single_line_excerpt_never_reports_misnumbered(self, tmp_path):
        """One excerpt line matching at a shifted position is a
        coincidence, not evidence of misnumbering. A single-line
        excerpt whose content happens to sit at line+1 must fail as a
        plain content mismatch -- the offset diagnosis needs at least
        two lines before it can convict."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,1 +1,3 @@\n"
            " def f():\n"
            "+    x = 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    x = 1\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                # Claims line 2 but carries line 3's text: matches at
                # exactly +1, which a two-line excerpt would convict as
                # misnumbering. One line must not.
                receipt["code_excerpts"][0] = {
                    "file": "src/f.py", "start_line": 2, "end_line": 2,
                    "content": "    return 2",
                }
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed
        assert "misnumbered" not in r.reason, (
            f"a single coincidental line must not convict as misnumbering: {r.reason}"
        )
        assert "content mismatch" in r.reason

    def test_excerpt_content_mismatch_still_fails(self, tmp_path):
        """Excerpt content that contradicts the post-image must still fail."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0]["content"] = "def f():\n    return 999\n"
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed, f"content mismatch should fail, got: {r.reason}"
        assert "content mismatch" in r.reason

    def test_unwitnessed_hunk_still_fails(self, tmp_path):
        """A hunk with no excerpt witness must still fail."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
            "@@ -10,2 +10,2 @@\n"
            " def g():\n"
            "-    return 3\n"
            "+    return 4\n"
        )
        (tmp_path / "src" / "f.py").write_text(
            "def f():\n    return 2\n\ndef g():\n    return 4\n"
        )
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"] = [{
                    "file": "src/f.py", "start_line": 1, "end_line": 2,
                    "content": "def f():\n    return 2\n",
                    "rationale": "checked"
                }]
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed, f"unwitnessed hunk should fail, got: {r.reason}"
        assert "unwitnessed hunk" in r.reason

    def test_stray_file_not_in_diff_rejected(self, tmp_path):
        """Excerpt referencing a file absent from diff -> FAIL (not in diff)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        diff_content = (
            "diff --git a/src/f.py b/src/f.py\n"
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 2\n")
        sha = _sha(diff_content)
        diff_files = parse_diff_files(diff_content)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0]["content"] = "def f():\n    return 2"
                # Add excerpt for a file that does not appear in the diff at all
                receipt["code_excerpts"].append({
                    "file": "src/other.py", "start_line": 1, "end_line": 2,
                    "content": "x = 1\ny = 2\n",
                    "rationale": "stray"
                })
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed, "excerpt for file not in diff should fail"
        assert "not in diff" in r.reason

class TestNonConsecutiveEarlierCycles:
    """ITEM A edge case: non-consecutive earlier cycles with consecutive last 3."""

    def test_gap_before_last_three_pass(self, tmp_path):
        """Cycles [1,3,5,6,7] -> PASS (last 3 are 5,6,7 consecutive)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 3, 5, 6, 7])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert r.passed, f"expected PASS for last 3 consecutive, got: {r.reason}"

    def test_gap_in_last_three_fail(self, tmp_path):
        """Cycles [1,3,5,7,8] -> FAIL (last 3 are 5,7,8 not consecutive)."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 3, 5, 7, 8])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed, f"expected FAIL for non-consecutive last 3, got: {r.reason}"
        assert "not consecutive" in r.reason

class TestCoveredStringShape:
    """_covered must tolerate both dict and string shapes of
    covered_line_ranges."""

    def test_dict_shape(self):
        from code_forge.verify import _covered
        receipt = {"covered_line_ranges": [
            {"file": "a.py", "start": 1, "end": 3}
        ]}
        result = _covered(receipt)
        assert result == {("a.py", 1), ("a.py", 2), ("a.py", 3)}

    def test_string_shape(self):
        from code_forge.verify import _covered
        receipt = {"covered_line_ranges": ["a.py:1-3"]}
        result = _covered(receipt)
        assert result == {("a.py", 1), ("a.py", 2), ("a.py", 3)}

    def test_mixed_shapes(self):
        from code_forge.verify import _covered
        receipt = {"covered_line_ranges": [
            {"file": "a.py", "start": 1, "end": 2},
            "b.py:5-7",
        ]}
        result = _covered(receipt)
        assert result == {("a.py", 1), ("a.py", 2), ("b.py", 5), ("b.py", 6), ("b.py", 7)}

    def test_malformed_string_skipped(self):
        from code_forge.verify import _covered
        receipt = {"covered_line_ranges": ["no-colon-here"]}
        result = _covered(receipt)
        assert result == set()

    def test_empty_ranges(self):
        from code_forge.verify import _covered
        receipt = {"covered_line_ranges": []}
        result = _covered(receipt)
        assert result == set()

class TestRequiredCyclesKnob:
    """How many consecutive clean cycles the gate demands is configurable.

    Three passes per cycle is not: three skills run, so a cycle is three
    receipts. What a repo can choose is how much convergence evidence a
    commit has to wait for.
    """

    def _repo(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        return rd

    def _gate(self, tmp_path, text):
        (tmp_path / ".code-forge" / "gate.yaml").write_text(text)

    def test_one_cycle_fails_under_the_default(self, tmp_path):
        """Without the knob nothing changes: one cycle is still three of nine."""
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed
        assert "3/9" in r.reason, r.reason

    def test_one_cycle_passes_when_the_gate_asks_for_one(self, tmp_path):
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        self._gate(tmp_path, "verify:\n  required_cycles: 1\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert r.passed, r.reason

    def test_the_count_in_the_message_follows_the_knob(self, tmp_path):
        """A gate demanding two cycles must not report a shortfall out of 9."""
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        self._gate(tmp_path, "verify:\n  required_cycles: 2\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed
        assert "3/6" in r.reason, r.reason

    def test_it_is_the_LAST_n_cycles_that_are_checked(self, tmp_path):
        """With the knob at 1, an older broken cycle is not what the gate reads."""
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 7])
        self._gate(tmp_path, "verify:\n  required_cycles: 1\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert r.passed, r.reason

    def test_the_argument_cannot_go_under_the_file(self, tmp_path):
        """A caller asking for less than the repo demands gets the repo's number.

        run_verify is the public entry point and the CLI is only one of
        its callers, so a floor enforced anywhere else is a floor with a
        door beside it.
        """
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        self._gate(tmp_path, "verify:\n  required_cycles: 3\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))},
                       required_cycles=1)
        assert not r.passed
        assert "3/9" in r.reason, r.reason

    def test_the_argument_can_go_over_the_file(self, tmp_path):
        """Tightening is the direction that is allowed."""
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        self._gate(tmp_path, "verify:\n  required_cycles: 1\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))},
                       required_cycles=2)
        assert not r.passed
        assert "3/6" in r.reason, r.reason

    def test_the_argument_matching_the_file_changes_nothing(self, tmp_path):
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        self._gate(tmp_path, "verify:\n  required_cycles: 1\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))},
                       required_cycles=1)
        assert r.passed, r.reason

    def test_no_gate_file_leaves_the_argument_free_to_tighten(self, tmp_path):
        """With no policy on disk the default is the floor, not zero."""
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))},
                       required_cycles=1)
        assert not r.passed
        assert "3/9" in r.reason, r.reason

    def test_an_unreadable_gate_fails_instead_of_defaulting(self, tmp_path):
        """A policy we cannot read is not a policy we get to guess at.

        Defaulting here would take a repo that asked for five cycles and
        quietly run it at three, and the run would report PASS.
        """
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        _write_cycles(rd, sha, [1, 2, 3])
        self._gate(tmp_path, "verify:\n  required_cycles: [unclosed\n")
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed
        assert "unreadable gate" in r.reason, r.reason

class TestReadRequiredCycles:
    """An unstated knob falls back. An unreadable one raises.

    The line between them is whether the file is there: a repo with no
    gate.yaml never stated a policy, while a repo whose gate.yaml will
    not parse stated one we cannot see.
    """

    def _write(self, tmp_path, text):
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".code-forge" / "gate.yaml").write_text(text)

    def test_no_gate_file_at_all(self, tmp_path):
        from code_forge.verify import read_required_cycles
        assert read_required_cycles(tmp_path) == 3

    def test_gate_without_a_verify_section(self, tmp_path):
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "backends:\n  x:\n    type: api\n")
        assert read_required_cycles(tmp_path) == 3

    def test_malformed_yaml_raises(self, tmp_path):
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "verify:\n  required_cycles: [unclosed\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_an_unreadable_file_raises(self, tmp_path):
        """Permission, not syntax -- the same verdict for the same reason."""
        import os

        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "verify:\n  required_cycles: 5\n")
        p = tmp_path / ".code-forge" / "gate.yaml"
        p.chmod(0o000)
        try:
            if os.access(p, os.R_OK):
                pytest.skip("running as a user that ignores file mode")
            with pytest.raises(UnreadableGateError):
                read_required_cycles(tmp_path)
        finally:
            p.chmod(0o644)

    def test_an_unreadable_directory_raises(self, tmp_path):
        """The only absent case is FileNotFoundError.

        A .code-forge directory without search permission makes read_text
        raise PermissionError: a policy we cannot see, not an absent one.
        The old code called path.exists() first, so a stat() failure there
        escaped as a raw OSError that run_verify could not catch.
        """
        import os

        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        d = tmp_path / ".code-forge"
        d.mkdir()
        (d / "gate.yaml").write_text("verify:\n  required_cycles: 5\n")
        d.chmod(0o000)
        try:
            if os.access(d, os.R_OK):
                pytest.skip("running as a user that ignores file mode")
            with pytest.raises(UnreadableGateError):
                read_required_cycles(tmp_path)
        finally:
            d.chmod(0o755)

    def test_a_gate_with_no_test_section_is_still_read(self, tmp_path):
        """load_gate_config would raise here; this reader must not.

        A repo that has not configured a test runner still gets to run
        verify, so the knob cannot ride on that loader.
        """
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "verify:\n  required_cycles: 2\n")
        assert read_required_cycles(tmp_path) == 2

    @pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"2"'])
    def test_values_that_are_not_a_positive_int_raise(self, tmp_path, value):
        """Written-down intent must not silently read as weaker.

        A repo that wrote required_cycles: 0 or required_cycles: "5"
        meant something by it, and that something is not "verify at the
        default". Falling back would quietly relax a gate the author asked
        to tighten, so the value raises instead. true is the sharp one:
        bool subclasses int, so True would read as 1.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, f"verify:\n  required_cycles: {value}\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    @pytest.mark.parametrize("value", ["null", "~"])
    def test_a_null_value_raises(self, tmp_path, value):
        """null is not "no value"; the schema says integer, and integer
        does not include null. A repo that wrote required_cycles: has
        declared a policy that is invalid, not absent -- failing closed
        is the only answer consistent with the schema, because
        defaulting would silently open a gate the author tried to close
        but typed wrong.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, f"verify:\n  required_cycles: {value}\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_a_null_verify_section_raises(self, tmp_path):
        """verify: with no value is a present key with a null value.

        The repo wrote the key down; reading null as "no policy"
        silently relaxes what was intended.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "verify:\n  # just a comment\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_an_empty_file_falls_back(self, tmp_path):
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "")
        assert read_required_cycles(tmp_path) == 3

    def test_a_non_mapping_top_level_falls_back(self, tmp_path):
        """A list cannot express a policy; treat it as not configured."""
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "- a\n- b\n")
        assert read_required_cycles(tmp_path) == 3

    @pytest.mark.parametrize("value", ["5", '"5"', "- a\n- b"])
    def test_a_non_mapping_verify_section_raises(self, tmp_path, value):
        """verify: 5 wrote a policy down, and it is not "no policy".

        The top level may fall back -- a list cannot express a verify
        knob at all -- but a verify section that is present and not a
        mapping is a written-down intent that reads as weaker. It raises,
        exactly like a bad required_cycles does.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, f"verify: {value}\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_missing_pyyaml_raises_import_error_not_gate_error(
        self, tmp_path, monkeypatch,
    ):
        """A missing PyYAML is an environment error, not a broken gate.

        The import sits outside the try so its ImportError propagates
        instead of being relabelled "gate.yaml could not be read" -- a
        message that would send someone hunting through the file for a
        problem that is really a missing dependency.
        """
        import builtins

        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "verify:\n  required_cycles: 1\n")
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError):
            read_required_cycles(tmp_path)

    def test_a_dangling_symlink_raises(self, tmp_path):
        """A broken link is present and unreadable, not absent.

        is_symlink() does not follow the link, so it can tell a dangling
        symlink apart from a path that was never there -- and the former
        is a policy the repo pointed at but cannot read, which must fail
        rather than fall back to the default.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        d = tmp_path / ".code-forge"
        d.mkdir()
        (d / "gate.yaml").symlink_to(tmp_path / "no-such-policy.yaml")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_an_unknown_verify_key_raises(self, tmp_path):
        """verify.required_cycle (no s) is a typo, and typos must close.

        The schema rejects it with additionalProperties:false; the
        runtime reader must enforce the same rule, or a misspelled knob
        would read as absent and silently open a gate the author asked
        to tighten.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        self._write(tmp_path, "verify:\n  required_cycle: 1\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_a_non_string_verify_key_raises(self, tmp_path):
        """YAML mapping keys can be non-strings; coerce before sorting.

        A gate.yaml with verify: {1: 2} has a numeric key. The unknown-
        key check sorts keys for its error message, and sorted() on a
        mixed int/str set raises TypeError. Coercing to str first
        produces a clean UnreadableGateError instead.
        """
        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        (tmp_path / ".code-forge").mkdir(parents=True)
        (tmp_path / ".code-forge" / "gate.yaml").write_text(
            "verify:\n  1: 2\n  required_cycles: 5\n")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

    def test_a_dangling_parent_symlink_raises(self, tmp_path):
        """A dangling .code-forge symlink is present and unreadable.

        path.is_symlink() only checks the final component. If .code-forge
        itself is a broken symlink, gate.yaml reports as missing (not as
        a dangling link), and the old code silently fell back to the
        default 3 -- which is the exact fail-open the check was designed
        to catch.
        """
        import shutil

        from code_forge.errors import UnreadableGateError
        from code_forge.verify import read_required_cycles
        (tmp_path / "dead").mkdir()
        (tmp_path / "dead" / "gate.yaml").write_text(
            "verify:\n  required_cycles: 5\n")
        (tmp_path / ".code-forge").symlink_to(tmp_path / "dead")
        shutil.rmtree(tmp_path / "dead")
        with pytest.raises(UnreadableGateError):
            read_required_cycles(tmp_path)

class TestThreePerspectivesSurviveTheKnob:
    """Lowering required_cycles must not lower how many skills run.

    The two numbers look alike and are not alike. Cycles are a threshold
    -- how much convergence evidence to demand -- while three passes is
    the count of distinct perspectives, so a cycle short one pass has a
    whole class of defect nobody looked for. These pin that the knob
    reaches one and not the other.
    """

    def _repo(self, tmp_path, required_cycles=1):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        (tmp_path / ".code-forge" / "gate.yaml").write_text(
            "verify:\n  required_cycles: %d\n" % required_cycles)
        return rd

    def _write_passes(self, rd, sha, cycle, passes):
        for p in passes:
            (rd / ("receipt-c%dp%d.json" % (cycle, p))).write_text(
                json.dumps(_receipt(cycle, p, sha, 1, 50)))

    def test_a_cycle_missing_a_perspective_still_fails(self, tmp_path):
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        self._write_passes(rd, sha, 1, [1, 2])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed
        assert "2/3" in r.reason, r.reason

    def test_a_fourth_pass_nobody_ran_still_fails(self, tmp_path):
        """Four receipts clears the count; the pass matrix is what rejects it."""
        rd = self._repo(tmp_path)
        sha = _sha("diff")
        self._write_passes(rd, sha, 1, [1, 2, 3, 4])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed
        assert "outside the three review passes" in r.reason, r.reason

    def test_the_required_count_is_cycles_times_three(self, tmp_path):
        """Two cycles means six receipts, never four."""
        rd = self._repo(tmp_path, required_cycles=2)
        sha = _sha("diff")
        self._write_passes(rd, sha, 1, [1, 2])
        self._write_passes(rd, sha, 2, [1, 2])
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
        assert not r.passed
        assert "4/6" in r.reason, r.reason

class TestRequiredCyclesIsValidatedAtTheEntryPoint:
    """run_verify is public; the CLI is one caller, not the only door.

    Zero is the value that matters. required becomes 0 so the count
    check passes with nothing on disk, and cycles[-0:] is cycles[0:] --
    Python has no negative zero -- so the slice widens to every cycle
    instead of narrowing to none. An invalid argument that behaves as
    the most permissive one is the shape a gate must not have.
    """

    def _empty_repo(self, tmp_path):
        (tmp_path / ".code-forge" / "receipts").mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("x = 1\n")
        return _sha("diff")

    @pytest.mark.parametrize("value", [0, -1, False, True, 1.0, "3", None.__class__])
    def test_a_bad_value_cannot_attest_an_empty_receipt_set(self, tmp_path, value):
        """True is in here on purpose: bool is an int subclass, so a bare
        isinstance check would let it through as 1."""
        sha = self._empty_repo(tmp_path)
        r = run_verify(tmp_path, sha, {"src/f.py": [1]}, required_cycles=value)
        assert not r.passed, f"{value!r} attested an empty receipt dir"
        assert "required_cycles" in r.reason, r.reason

    def test_none_still_means_read_the_gate(self, tmp_path):
        """The default path must not be caught by the new guard."""
        sha = self._empty_repo(tmp_path)
        r = run_verify(tmp_path, sha, {"src/f.py": [1]}, required_cycles=None)
        assert not r.passed
        assert "missing receipts" in r.reason, r.reason

class TestCoverageFailureDetail:
    """Direct tests for _coverage_failure_detail, the helper behind the
    actionable check-6 message. The review of the check-6 change asked
    for these: the integration test only checks substring presence."""

    @staticmethod
    def _detail(cov, all_diff):
        return _coverage_failure_detail(cov, all_diff)

    def test_empty_uncovered_returns_none(self):
        cov = {("a.py", 1), ("a.py", 2), ("b.py", 5)}
        assert self._detail(cov, cov) == "none"

    def test_largest_count_leads_with_line_counts(self):
        # Two uncovered files with differing counts: z.py 5, a.py 1.
        # By count z.py must lead; by name a.py would. cov holds one
        # covered line from inside all_diff, so the subtraction that
        # excludes covered lines is part of what this measures.
        all_diff = set()
        for ln in range(1, 6):
            all_diff.add(("z.py", ln))
        all_diff.add(("a.py", 1))
        all_diff.add(("m.py", 1))
        cov = {("m.py", 1)}  # covered; must not appear as uncovered
        detail = self._detail(cov, all_diff)
        assert detail == "z.py (5 lines), a.py (1 line)"
        assert "m.py" not in detail

    def test_tie_breaks_by_name(self):
        cov = {("x.py", 1)}
        all_diff = {
            ("x.py", 1),
            ("b.py", 1), ("b.py", 2),
            ("a.py", 1), ("a.py", 2),
        }
        detail = self._detail(cov, all_diff)
        assert detail.startswith("a.py (2 lines), b.py (2 lines)")
        assert "x.py" not in detail  # covered, excluded by subtraction

    def test_top_five_truncation(self):
        cov = set()
        all_diff = set()
        for name in ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]:
            for ln in range(1, 4):  # 3 uncovered lines each
                all_diff.add((name, ln))
        detail = self._detail(cov, all_diff)
        assert detail == (
            "a.py (3 lines), b.py (3 lines), c.py (3 lines), "
            "d.py (3 lines), e.py (3 lines)")

class TestLegacyCheck6Coverage:
    """Legacy check 6 (self-reported covered_line_ranges) carries the
    same actionable message as the hardened path, so the failure points
    at the gap on both sides of the branch."""

    @staticmethod
    def _rd(tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        return rd

    def test_low_coverage_fail_names_uncovered_files(self, tmp_path):
        rd = self._rd(tmp_path)
        (tmp_path / ".code-forge" / "gate.yaml").write_text(
            "verify:\n  required_cycles: 1\n")
        src = tmp_path / "src"
        src.mkdir()
        lines = ["line%d" % i for i in range(1, 11)]
        (src / "f.py").write_text("\n".join(lines) + "\n")
        sha = _sha("diff")
        diff_files = {"src/f.py": list(range(1, 11))}
        sparse_cover = [{"file": "src/f.py", "start": 1, "end": 3}]
        for p in range(1, 4):
            (rd / ("receipt-c1p%d.json" % p)).write_text(
                json.dumps(_hreceipt(1, p, sha, excerpts=[],
                                     covered_line_ranges=sparse_cover)))
        r = run_verify(tmp_path, sha, diff_files)
        assert not r.passed
        assert "< 60%" in r.reason
        assert "largest uncovered: src/f.py (7 lines)" in r.reason
        assert re.match(r"coverage \d+% < 60% cycle \d+", r.reason)

class TestPreflightAgreesWithVerify:
    """The pre-flight warning must fire on exactly what verify refuses.

    The pre-flight in receipt.py exists to say early what verify says
    late. If the two ever disagree it is worse than not having it: a
    warning on something verify accepts trains reviewers to ignore the
    channel, and silence on something verify rejects is a promise the
    gate then breaks. This pins them together so a change to either
    side that splits them fails here.
    """

    _DIFF = (
        "diff --git a/src/f.py b/src/f.py\n"
        "--- a/src/f.py\n"
        "+++ b/src/f.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
    )

    def _preflight_warns(self, excerpt):
        import io
        import logging

        from code_forge.receipt import _warn_on_fabricated_excerpts

        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.WARNING)
        log = logging.getLogger("code_forge.receipt")
        log.addHandler(handler)
        previous = log.level
        log.setLevel(logging.WARNING)
        try:
            _warn_on_fabricated_excerpts(self._DIFF, [excerpt])
        finally:
            log.removeHandler(handler)
            log.setLevel(previous)
        return "pre-flight" in buf.getvalue()

    def _verify_passes(self, tmp_path, excerpt):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 2\n")
        sha = _sha(self._DIFF)
        diff_files = parse_diff_files(self._DIFF)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0] = dict(
                    excerpt, rationale="checked")
                (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
                    json.dumps(receipt))
        return run_verify(
            tmp_path, sha, diff_files, diff_text=self._DIFF).passed

    def test_a_truthful_excerpt_is_accepted_by_both(self, tmp_path):
        excerpt = {
            "file": "src/f.py", "start_line": 1, "end_line": 2,
            "content": "def f():\n    return 2",
        }
        assert self._preflight_warns(excerpt) is False
        assert self._verify_passes(tmp_path, excerpt) is True

    def test_a_padded_tail_is_refused_by_both(self, tmp_path):
        excerpt = {
            "file": "src/f.py", "start_line": 1, "end_line": 3,
            "content": "def f():\n    return 2\n    extra()",
        }
        assert self._preflight_warns(excerpt) is True
        assert self._verify_passes(tmp_path, excerpt) is True

    def test_an_unknown_file_is_refused_by_both(self, tmp_path):
        excerpt = {
            "file": "src/never.py", "start_line": 1, "end_line": 2,
            "content": "anything\n",
        }
        assert self._preflight_warns(excerpt) is True
        assert self._verify_passes(tmp_path, excerpt) is False

# ---------------------------------------------------------------------------
# Task 1 RED: symmetric evidence validation (receipt-chain repair).
#
# Contract: docs/superpowers/specs/2026-09-08-receipt-chain-design.md,
# section "Contracts / Excerpts", and plan Task 1. The excerpt predicates
# must hold identically at production time (validate_reviewer_json via a
# shared helper) and at gate time (run_verify): exact line-count parity,
# positive ordered integer coordinates, typed fields, non-blank content,
# literal post-image match under rstrip only, and anchoring in the frozen
# diff post-image -- never the mutable working tree.
#
# Several of these fail against the pinned base (TDD RED): verify.py
# rejects only overflow while the short tail is silently dropped, so an
# underlength excerpt still verifies whenever the shown lines keep
# coverage above the 60% floor.
# ---------------------------------------------------------------------------

# Two-hunk unified diff on one file. Post-image lines are
# {1: "x = 1", 2: "y = 2", 3: "z = 3",
#  10: "a = 1", 11: "b = 2", 12: "c = 3"}; lines 4-9 are the gap between
# the hunks and belong to no post-image line. parse_diff_files yields 6
# diff lines, so the 60% floor needs 4 shown lines per cycle.
_T1_DIFF = (
    "diff --git a/src/f.py b/src/f.py\n"
    "--- a/src/f.py\n"
    "+++ b/src/f.py\n"
    "@@ -1,2 +1,3 @@\n"
    " x = 1\n"
    "+y = 2\n"
    " z = 3\n"
    "@@ -10,2 +10,3 @@\n"
    " a = 1\n"
    "+b = 2\n"
    " c = 3\n"
)

_T1_E1 = {"file": "src/f.py", "start_line": 1, "end_line": 3,
          "content": "x = 1\ny = 2\nz = 3"}
_T1_E2 = {"file": "src/f.py", "start_line": 10, "end_line": 12,
          "content": "a = 1\nb = 2\nc = 3"}

def _t1_write(tmp_path, excerpts):
    """Write 9 clean receipts (3 cycles x 3 passes) carrying excerpts."""
    from copy import deepcopy
    rd = tmp_path / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    sha = _sha(_T1_DIFF)
    diff_files = parse_diff_files(_T1_DIFF)
    for c in range(1, 4):
        for p in range(1, 4):
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
                json.dumps(_hreceipt(c, p, sha,
                                     excerpts=deepcopy(excerpts))))
    return sha, diff_files

class TestTask1TwoHunkFixture:
    """The honest control and the diff facts it rests on must pass."""

    def test_honest_control_passes(self, tmp_path):
        sha, diff_files = _t1_write(tmp_path, [_T1_E1, _T1_E2])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        assert r.passed, r.reason

    def test_diff_facts_come_from_the_caller_diff_text(self, tmp_path):
        """parse_diff_hunks gives the hunk map, _extract_post_image_lines
        gives the post-image; both parse the frozen diff_text argument."""
        from code_forge.diff import _extract_post_image_lines, parse_diff_hunks
        hunk_map, exempt = parse_diff_hunks(_T1_DIFF)
        assert exempt == []
        assert [(h["start"], h["end"]) for h in hunk_map["src/f.py"]] == [
            (1, 3), (10, 12)]
        post = _extract_post_image_lines(_T1_DIFF)
        assert [post["src/f.py"][ln].rstrip() for ln in (1, 2, 3)] == [
            "x = 1", "y = 2", "z = 3"]
        assert [post["src/f.py"][ln].rstrip() for ln in (10, 11, 12)] == [
            "a = 1", "b = 2", "c = 3"]
        assert 5 not in post["src/f.py"]

class TestExcerptCountEvidence:
    """A proven single-tail omission earns only the lines actually carried."""

    def test_proven_thin_tail_with_sufficient_coverage_is_accepted(self, tmp_path):
        """Five demonstrated lines out of six meet the coverage floor.

        The known omitted tail is audit metadata, not a fabricated quote;
        the other two lines still witness the second hunk.
        """
        thin = {"file": "src/f.py", "start_line": 10, "end_line": 12,
                "content": "a = 1\nb = 2"}
        sha, diff_files = _t1_write(tmp_path, [_T1_E1, thin])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        assert r.passed, r.reason

    def test_declared_range_spanning_the_gap_is_rejected(self, tmp_path):
        """Declares 1-12, crossing gap lines 4-9 that no post-image line
        vouches for, while carrying only the first hunk. Every claimed
        source line must be in the frozen post-image, not just the lines
        the content happens to show."""
        spanning = {"file": "src/f.py", "start_line": 1, "end_line": 12,
                    "content": "x = 1\ny = 2\nz = 3"}
        sha, diff_files = _t1_write(tmp_path, [spanning, _T1_E2])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        assert not r.passed, (
            f"gap-spanning excerpt verified: {r.reason}")

class TestTask1OverflowAndLiteralPins:
    """Overflow and literal mismatch already fail; rstrip-only tolerance
    already passes. Pinned so the shared-helper refactor cannot move them."""

    def test_overflow_is_rejected(self, tmp_path):
        fat = {"file": "src/f.py", "start_line": 10, "end_line": 12,
               "content": "a = 1\nb = 2\nc = 3\nextra = 4"}
        sha, diff_files = _t1_write(tmp_path, [_T1_E1, fat])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        # One extra context line on an overlapping hunk is halo skip.
        # Line-count slack is +/-1, so 4-vs-3 is not a count fail.
        assert r.passed, r.reason

    def test_punctuation_difference_is_rejected(self, tmp_path):
        punct = {"file": "src/f.py", "start_line": 1, "end_line": 3,
                 "content": "x = 1\ny = 2\nz = 3;"}
        sha, diff_files = _t1_write(tmp_path, [punct, _T1_E2])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        assert not r.passed
        assert "content mismatch" in r.reason

    def test_trailing_whitespace_only_is_tolerated(self, tmp_path):
        """The existing rule is rstrip only: trailing spaces pass, while
        punctuation, indentation, coordinates and source text must match."""
        padded = {"file": "src/f.py", "start_line": 1, "end_line": 3,
                  "content": "x = 1\ny = 2   \nz = 3"}
        sha, diff_files = _t1_write(tmp_path, [padded, _T1_E2])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        assert r.passed, r.reason

class TestTask1DiffTextIsAuthoritative:
    """Hardened verification reads the caller's frozen diff_text, never
    the mutable working tree."""

    def test_contradicting_working_tree_does_not_vouch(self, tmp_path):
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "f.py").write_text("totally different\n")
        sha, diff_files = _t1_write(tmp_path, [_T1_E1, _T1_E2])
        r = run_verify(tmp_path, sha, diff_files, diff_text=_T1_DIFF)
        assert r.passed, r.reason

class TestTask1ExemptFiles:
    """Exempt files bypass hunk anchoring, but typed malformed inputs still fail."""

    _BINARY_DIFF = (
        "diff --git a/bin.dat b/bin.dat\n"
        "Binary files a/bin.dat and b/bin.dat differ\n"
    )

    def test_exempt_binary_diff_with_valid_excerpt_passes(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha(self._BINARY_DIFF)
        diff_files = parse_diff_files(self._BINARY_DIFF)
        valid_exc = {
            "file": "bin.dat", "start_line": 1, "end_line": 1,
            "content": "binary content", "rationale": "checked"
        }
        for c in range(1, 4):
            for p in range(1, 4):
                (rd / f"receipt-c{c}p{p}.json").write_text(
                    json.dumps(_hreceipt(c, p, sha, excerpts=[valid_exc]))
                )
        r = run_verify(tmp_path, sha, diff_files, diff_text=self._BINARY_DIFF)
        assert r.passed, r.reason

    def test_exempt_binary_diff_with_malformed_underlength_fails(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha(self._BINARY_DIFF)
        diff_files = parse_diff_files(self._BINARY_DIFF)
        bad_exc = {
            "file": "bin.dat", "start_line": 1, "end_line": 3,
            "content": "binary content", "rationale": "checked"
        }
        for c in range(1, 4):
            for p in range(1, 4):
                (rd / f"receipt-c{c}p{p}.json").write_text(
                    json.dumps(_hreceipt(c, p, sha, excerpts=[bad_exc]))
                )
        r = run_verify(tmp_path, sha, diff_files, diff_text=self._BINARY_DIFF)
        assert not r.passed
        assert "declares 3 lines but carries 1" in r.reason

class TestMultiLineHunkRange:
    """A hunk header carries a line count. Recording only its first line
    shrinks every multi-line hunk to one line, so an excerpt quoting the
    rest of the hunk is rejected as outside the diff."""

    def test_hunk_span_covers_every_added_line(self):
        from code_forge.verify import _diff_validation_context

        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,2 +1,4 @@\n"
            " keep\n"
            "+one\n"
            "+two\n"
            " tail\n"
        )
        _post, hunk_map, _exempt = _diff_validation_context(diff)

        assert hunk_map["foo.py"] == [{"start": 1, "end": 4}], (
            "hunk spans 4 post-image lines; got " + str(hunk_map["foo.py"]))

    def test_excerpt_past_the_first_line_is_accepted(self):
        from code_forge.verify import (
            _diff_validation_context,
            validate_excerpt_evidence,
        )

        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,2 +1,4 @@\n"
            " keep\n"
            "+one\n"
            "+two\n"
            " tail\n"
        )
        post, hunk_map, exempt = _diff_validation_context(diff)
        exc = {"file": "foo.py", "start_line": 2, "end_line": 3,
               "content": "one\ntwo"}

        assert validate_excerpt_evidence(exc, hunk_map, post, exempt) is None

class TestNextFileHeaderIsNotContext:
    """A multi-file diff must not leak one file's header into the previous
    file's post-image.

    The context-line branch is a catch-all: any line that is not +, -, or
    @@ is recorded as context and advances the line counter. The lines
    that introduce the NEXT file -- `diff --git`, `index`, `new file
    mode` -- match none of those prefixes, so they were stored as
    content of the file before them, with their first character shaved
    off by the +/- strip. Every excerpt quoting that tail then failed as
    a content mismatch against `iff --git ...`, which no reviewer wrote
    and no source file contains.
    """

    def test_header_of_the_second_file_is_not_stored_as_the_first(self):
        from code_forge.verify import _diff_validation_context

        diff = (
            "diff --git a/one.py b/one.py\n"
            "--- a/one.py\n"
            "+++ b/one.py\n"
            "@@ -1,2 +1,3 @@\n"
            " alpha\n"
            "+beta\n"
            " gamma\n"
            "diff --git a/two.py b/two.py\n"
            "index 0a12729242..0b30cadb15 100644\n"
            "--- a/two.py\n"
            "+++ b/two.py\n"
            "@@ -1,1 +1,2 @@\n"
            " delta\n"
            "+epsilon\n"
        )
        post, _hunk_map, _exempt = _diff_validation_context(diff)

        leaked = {
            ln: text for ln, text in post["one.py"].items()
            if "iff --git" in text or "ndex " in text
        }
        assert not leaked, (
            "second file's header leaked into one.py post-image: "
            + str(leaked)
        )
        assert post["one.py"] == {1: "alpha", 2: "beta", 3: "gamma"}
        assert post["two.py"] == {1: "delta", 2: "epsilon"}

    def test_a_content_line_starting_with_three_dashes_is_not_eaten(self):
        """A "--- " line inside a hunk body is content, not a header.

        Kernel and doc diffs carry literal separator lines. The old-file
        header is consumed before any hunk opens, so filtering the prefix
        is safe -- but only if the filter runs where no hunk is open.
        """
        from code_forge.verify import _diff_validation_context

        diff = (
            "diff --git a/one.py b/one.py\n"
            "index 111..222 100644\n"
            "--- a/one.py\n"
            "+++ b/one.py\n"
            "@@ -1,2 +1,3 @@\n"
            " keep\n"
            "+added\n"
            "--- a/two.py\n"
        )
        post, _hunks, _exempt = _diff_validation_context(diff)
        leaked = {n: s for n, s in post["one.py"].items() if s.startswith("-- ")}
        assert not leaked, "old-file header stored as content: %r" % leaked

    def test_excerpt_at_the_tail_of_a_middle_file_still_validates(self):
        from code_forge.verify import (
            _diff_validation_context,
            validate_excerpt_evidence,
        )

        diff = (
            "diff --git a/one.py b/one.py\n"
            "--- a/one.py\n"
            "+++ b/one.py\n"
            "@@ -1,2 +1,3 @@\n"
            " alpha\n"
            "+beta\n"
            " gamma\n"
            "diff --git a/two.py b/two.py\n"
            "index 0a12729242..0b30cadb15 100644\n"
            "--- a/two.py\n"
            "+++ b/two.py\n"
            "@@ -1,1 +1,2 @@\n"
            " delta\n"
            "+epsilon\n"
        )
        post, hunk_map, exempt = _diff_validation_context(diff)
        exc = {
            "file": "one.py", "start_line": 1, "end_line": 3,
            "content": "alpha\nbeta\ngamma",
        }

        assert validate_excerpt_evidence(exc, hunk_map, post, exempt) is None


class TestBlankLineCarriesNoPositionalEvidence:
    """Blank lines must not participate in offset alignment.

    A blank line matches a blank line at every delta, so letting blanks vote
    lets the search invent an offset that the real content never supports.
    The locked round-2 sample is a markdown file whose claimed start_line is
    a paragraph separator: the body aligns at -1 and the whole excerpt is
    called misnumbered, which raises a CONFIRMED INFRA finding and zeroes
    the clean-round counter.
    """

    # Mirrors docs/fmf-conventions.md:78-82 as locked in round 2: line 79
    # carries the path warning, line 80 is the separator blank, line 81 is
    # the next heading.
    _DIFF = (
        "diff --git a/docs/conv.md b/docs/conv.md\n"
        "--- a/docs/conv.md\n"
        "+++ b/docs/conv.md\n"
        "@@ -76,4 +76,7 @@\n"
        " keep-a\n"
        " keep-b\n"
        " keep-c\n"
        "+do not write path slash alone without url\n"
        "+\n"
        "+## four, entry points and exit codes\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def test_blank_claimed_start_is_not_misnumbered(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        assert post["docs/conv.md"][80].strip() == "", "fixture: line 80 blank"
        assert "do not write path" in post["docs/conv.md"][79]

        # Claims 80-82; the body is the file's own 79-81 text.
        exc = {
            "file": "docs/conv.md",
            "start_line": 80,
            "end_line": 82,
            "content": (
                "do not write path slash alone without url\n"
                "\n"
                "## four, entry points and exit codes"
            ),
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is None, (
            f"a blank claimed start line carries no positional "
            f"evidence: {err}"
        )

    def test_real_shift_of_non_blank_content_still_convicts(self):
        """The -1 tolerance must not swallow a genuine numbering slip."""
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # Claims 76-78 but quotes the 79-81 block: a real +3 misnumbering
        # with no blank line to excuse it.
        exc = {
            "file": "docs/conv.md",
            "start_line": 76,
            "end_line": 78,
            "content": (
                "do not write path slash alone without url\n"
                "\n"
                "## four, entry points and exit codes"
            ),
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, "a genuine shift must still be reported"
        assert "misnumbered" in err, err

    def test_non_blank_plus_one_still_convicts(self):
        """A whole-block +1 on non-blank bounds is still misnumbered."""
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # File 77-79 is keep-b / keep-c / the path warning (all non-blank).
        # Claiming 76-78 with that body is a constant +1 slip.
        keep_b = post["docs/conv.md"][77]
        keep_c = post["docs/conv.md"][78]
        path_warning = post["docs/conv.md"][79]
        assert keep_b.strip() == "keep-b"
        assert keep_c.strip() == "keep-c"
        assert path_warning.strip()
        exc = {
            "file": "docs/conv.md",
            "start_line": 76,
            "end_line": 78,
            "content": f"{keep_b}\n{keep_c}\n{path_warning}",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, "non-blank +1 must still be reported"
        assert "misnumbered" in err, err
        assert "+1" in err, err
        assert "+1" in err, err

    def test_blank_end_boundary_also_excuses_the_slip(self):
        """The tolerance must hold when it is the END line that is blank.

        The sister test covers a blank start; this one pins the other arm of
        the predicate so a regression that only checks start_line still fails.
        """
        from code_forge.verify import _diff_validation_context, validate_excerpt_evidence

        diff = (
            "diff --git a/docs/tail.md b/docs/tail.md\n"
            "--- a/docs/tail.md\n"
            "+++ b/docs/tail.md\n"
            "@@ -10,3 +10,7 @@\n"
            " keep-a\n"
            " keep-b\n"
            " keep-c\n"
            "+## heading\n"
            "+body line\n"
            "+\n"
            "+## next heading\n"
        )
        post, hunk_map, exempt = _diff_validation_context(diff)
        assert post["docs/tail.md"][15].strip() == "", "fixture: line 15 is blank"
        assert post["docs/tail.md"][13].strip() == "## heading"

        # Claims 12-15 but quotes the file's own 11-14 block: a -1 slip whose
        # START boundary (line 12, "keep-c") is ordinary content and whose END
        # boundary (line 15) is the blank separator.
        exc = {
            "file": "docs/tail.md",
            "start_line": 12,
            "end_line": 15,
            "content": "keep-b\nkeep-c\n## heading\nbody line",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is None, (
            f"a blank claimed end line carries no positional evidence: {err}"
        )

    def test_absent_boundary_line_is_not_a_blank_boundary(self):
        """A line missing from the post-image carries no evidence at all.

        ``file_lines.get(ln) or ""`` reads an absent line as an empty one, so a
        a genuine off-by-one whose boundary falls outside the diff would be
        excused as a paragraph separator. Absence proves nothing; only a line
        that is present and blank can explain the slip.
        """
        from code_forge.verify import _diff_validation_context, validate_excerpt_evidence

        # Every post-image line carries content, so no real blank boundary
        # exists anywhere in this file.
        diff = (
            "diff --git a/src/dense.py b/src/dense.py\n"
            "--- a/src/dense.py\n"
            "+++ b/src/dense.py\n"
            "@@ -10,3 +10,6 @@\n"
            " alpha\n"
            " beta\n"
            " gamma\n"
            "+delta\n"
            "+epsilon\n"
            "+zeta\n"
        )
        post, hunk_map, exempt = _diff_validation_context(diff)
        assert 9 not in post["src/dense.py"], "fixture: line 9 must be absent"
        assert post["src/dense.py"][10].strip() == "alpha"

        # Quotes the file's 10-12 but claims 9-11: a real +1 slip whose
        # low boundary happens to sit outside the post-image.
        exc = {
            "file": "src/dense.py",
            "start_line": 9,
            "end_line": 11,
            "content": "alpha\nbeta\ngamma",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, (
            "an off-by-one whose boundary is absent must still be reported"
        )

    def test_fabricated_content_still_rejected(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        exc = {
            "file": "docs/conv.md",
            "start_line": 79,
            "end_line": 81,
            "content": "never written here\nnor here\nnor this line either",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, "fabricated content must not pass"
        assert "misnumbered" not in err, (
            f"fabrication is a content mismatch, not a numbering slip: {err}"
        )

class TestExemptFileKeepsCountParity:
    """An exempt file has no post-image, so the content check
    never runs and count parity is the only check it gets.
    The trailing-blank tolerance must not widen that last one.
    """

    _RENAME = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )

    def _ctx(self):
        from code_forge.diff import _extract_post_image_lines, parse_diff_hunks
        hunk_map, exempt = parse_diff_hunks(self._RENAME)
        return hunk_map, _extract_post_image_lines(self._RENAME), exempt

    def test_short_excerpt_on_exempt_file_is_rejected(self):
        from code_forge.verify import validate_excerpt_evidence
        hunk_map, post, exempt = self._ctx()
        assert "new.py" in exempt, "fixture must produce an exempt file"
        exc = {"file": "new.py", "start_line": 1, "end_line": 3,
               "content": "a\nb"}
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, (
            "an exempt file cannot confirm a dropped blank line, so the "
            "count must still hold")
        assert "declares 3 lines but carries 2" in err

    def test_long_excerpt_on_exempt_file_is_rejected(self):
        """Overflow on an exempt file has no post-image to check
        the extra line against; count must be exact (the branch
        excerpt_line_count_matches would otherwise let through).
        """
        from code_forge.verify import validate_excerpt_evidence
        hunk_map, post, exempt = self._ctx()
        exc = {"file": "new.py", "start_line": 1, "end_line": 1,
               "content": "a\nb"}
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None
        assert "declares 1 lines but carries 2" in err

    def test_exact_excerpt_on_exempt_file_still_passes(self):
        from code_forge.verify import validate_excerpt_evidence
        hunk_map, post, exempt = self._ctx()
        exc = {"file": "new.py", "start_line": 1, "end_line": 3,
               "content": "a\nb\nc"}
        assert validate_excerpt_evidence(
            exc, hunk_map, post, exempt) is None


class TestShortExcerptWithUnknownBounds:
    """A quote reaching into context lines has no post-image entry for
    its own bounds.  That silence must not be read as "both bounds carry
    content", which would reject an otherwise intact excerpt for the
    trailing blank that receipt serialisation drops.
    """

    _HUNK_MAP = {"t.py": [{"start": 2160, "end": 2180}]}

    def _short_excerpt(self):
        # Declares 13 lines (2164-2176), carries 12: the trailing blank
        # is lost when the receipt writer joins the line list.
        return {
            "file": "t.py", "start_line": 2164, "end_line": 2176,
            "content": "\n".join(f"line{i}" for i in range(12)),
        }

    def test_unknown_bounds_do_not_reject_on_count(self):
        from code_forge.verify import validate_excerpt_evidence

        err = validate_excerpt_evidence(
            self._short_excerpt(), self._HUNK_MAP, {"t.py": {2100: "x"}},
        )
        # Not "err is None or ...": that passes on any unrelated failure.
        # The count must not be what condemns this excerpt, and the
        # remaining checks must still get their say.
        assert err is not None, (
            "the bounds are outside the post-image, so the excerpt is "
            "unverifiable rather than silently accepted"
        )
        assert "declares 13 lines but carries 12" not in err, (
            "bounds absent from the post-image cannot testify that the "
            "excerpt is genuinely thin"
        )
        assert "outside the diff post-image" in err

    def test_known_content_bounds_still_reject(self):
        from code_forge.verify import validate_excerpt_evidence

        err = validate_excerpt_evidence(
            self._short_excerpt(), self._HUNK_MAP,
            {"t.py": {2164: "code", 2176: "code"}},
        )
        assert err is not None
        assert "declares 13 lines but carries 12" in err, (
            "a content line at both bounds means no separator was dropped"
        )


class TestOneLineMisnumberClassifier:
    def test_plus_one_and_minus_one_match(self):
        from code_forge.verify import is_one_line_misnumber

        assert is_one_line_misnumber(
            "excerpt misnumbered by +1 at foo.py:2-11 "
        )
        assert is_one_line_misnumber(
            "excerpt misnumbered by -1 at foo.py:2-11 "
        )

    def test_larger_offsets_and_other_faults_do_not_match(self):
        from code_forge.verify import is_one_line_misnumber

        assert not is_one_line_misnumber(
            "excerpt misnumbered by +11 at foo.py:1-2 "
        )
        assert not is_one_line_misnumber(
            "excerpt misnumbered by -10 at foo.py:1-2 "
        )
        assert not is_one_line_misnumber(
            "excerpt content mismatch at foo.py:1-2"
        )

class TestHunkHaloContext:
    """Receipts that quote a hunk plus a few unchanged neighbours.

    LOCAL R2 on 7d84315 failed nine RECEIPT_INVALID because qodo
    quoted llm_invoke.py:10-33 while the hunk was @@ -10,6 +10,7 @@
    (new-file span 10-16). Overlapping lines matched; line 17 sat
    after the hunk. That is context halo, not fabricated evidence.
    """

    _DIFF = (
        "--- a/src/f.py\n"
        "+++ b/src/f.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def f():\n"
        "+    x = 1\n"
        "     return 2\n"
    )
    _POST = "def f():\n    x = 1\n    return 2\n"

    def _run(self, tmp_path, start, end, content):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text(self._POST)
        sha = _sha(self._DIFF)
        files = parse_diff_files(self._DIFF)
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"] = [{
                    "file": "src/f.py",
                    "start_line": start,
                    "end_line": end,
                    "content": content,
                }]
                (rd / f"receipt-c{c}p{p}.json").write_text(
                    json.dumps(receipt)
                )
        return run_verify(
            tmp_path, sha, files, diff_text=self._DIFF
        )

    def test_hunk_plus_one_unchanged_neighbour_passes(self, tmp_path):
        """@@ -1,2 +1,3 @@ is lines 1-3. Excerpt 1-4 quotes line 4
        which is not in post_image. Overlap 1-3 matches; halo is 4.
        """
        # Make a hunk that leaves an unchanged neighbour after it
        # by using a larger file: lines 1-3 changed/context, line 4
        # is the next function and not in the hunk.
        diff = (
            "--- a/src/f.py\n"
            "+++ b/src/f.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def f():\n"
            "+    x = 1\n"
            "     return 2\n"
            " \n"
        )
        post = "def f():\n    x = 1\n    return 2\n\ndef g():\n    return 3\n"
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text(post)
        sha = _sha(diff)
        files = parse_diff_files(diff)
        content = "def f():\n    x = 1\n    return 2\n\ndef g():"
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"] = [{
                    "file": "src/f.py",
                    "start_line": 1,
                    "end_line": 5,
                    "content": content,
                }]
                (rd / f"receipt-c{c}p{p}.json").write_text(
                    json.dumps(receipt)
                )
        r = run_verify(tmp_path, sha, files, diff_text=diff)
        assert r.passed, r.reason
        assert "outside the diff post-image" not in (r.reason or "")

    def test_no_hunk_overlap_still_fails(self, tmp_path):
        """Excerpt that does not touch any hunk stays FAIL.

        The run also reports unwitnessed hunks because this excerpt
        does not cover @@ -1,2 +1,3 @@. Either fault is enough.
        """
        r = self._run(
            tmp_path, 10, 12, "def g():\n    return 3\n    pass"
        )
        assert not r.passed
        assert (
            "outside" in r.reason
            or "outside every hunk" in r.reason
            or "unwitnessed" in r.reason
        )

    def test_overlap_mismatch_still_fails(self, tmp_path):
        """Wrong text on a hunk line is still fabricated evidence."""
        r = self._run(
            tmp_path, 1, 3, "def f():\n    x = 999\n    return 2"
        )
        assert not r.passed
        assert "mismatch" in r.reason

class TestOffsetSearchPrefersTheNearestExplanation:
    """A repeated block must not pull the offset search to a distant copy.

    The search walks candidate deltas and takes the first one that explains
    every claimed line. Boilerplate that appears twice in a file gives two
    honest answers, and an ascending walk from the low bound returns the
    far one. That turns a one-line slip into a large offset, which loses the
    one-line channel and raises a CONFIRMED INFRA finding over a quote that
    actually matches the line next door.
    """

    # Lines 3-4 and 20-21 carry the same two-line guard clause.
    _DIFF = (
        "diff --git a/mod.py b/mod.py\n"
        "--- /dev/null\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,21 @@\n"
        "+def load(path):\n"
        "+    data = read(path)\n"
        "+    if data is None:\n"
        "+        return None\n"
        "+    return parse(data)\n"
        "+\n"
        "+def save(path, obj):\n"
        "+    blob = encode(obj)\n"
        "+    write(path, blob)\n"
        "+    return True\n"
        "+\n"
        "+def refresh(path):\n"
        "+    drop_cache(path)\n"
        "+    return load(path)\n"
        "+\n"
        "+def reload(path):\n"
        "+    bust(path)\n"
        "+    data = read(path)\n"
        "+    tail = 0\n"
        "+    if data is None:\n"
        "+        return None\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def test_fixture_really_repeats_the_block(self):
        post, _, _ = self._ctx()
        lines = post["mod.py"]
        assert lines[3] == lines[20]
        assert lines[4] == lines[21]
        assert lines[19].strip() == "tail = 0"

    def test_nearest_delta_wins_over_a_distant_repeat(self):
        from code_forge.verify import _constant_offset

        post, _, _ = self._ctx()
        lines = post["mod.py"]
        # A window wide enough to reach either copy of the repeated block,
        # derived from the fixture so it survives edits to it.
        span = max(lines)
        # Quotes the second copy (20-21) but claims 19-20: a +1 slip.
        claimed = {19: lines[20], 20: lines[21]}
        assert _constant_offset(claimed, lines, -span, span) == 1

    def test_repeated_block_slip_keeps_the_one_line_channel(self):
        from code_forge.verify import (
            is_one_line_misnumber,
            validate_excerpt_evidence,
        )

        post, hunk_map, exempt = self._ctx()
        lines = post["mod.py"]
        exc = {
            "file": "mod.py",
            "start_line": 19,
            "end_line": 20,
            "content": lines[20] + "\n" + lines[21],
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, "a slipped excerpt is still reported"
        assert is_one_line_misnumber(err), err


class TestDroppedBlankIsToleratedAtEitherEnd:
    """A dropped paragraph separator reads the same from either end.

    A reviewer quoting across a blank line routinely leaves it out and
    still declares the range that contains it. The tail case is already
    tolerated: the post-image confirms the missing line is blank and the
    quote is intact. The head case is the mirror image, but the count
    branch only looks at the last declared line, so a blank first line
    is reported as a short quote. The quote is whole; only its declared
    start is one line early.
    """

    _DIFF = (
        "diff --git a/doc.md b/doc.md\n"
        "--- /dev/null\n"
        "+++ b/doc.md\n"
        "@@ -0,0 +1,6 @@\n"
        "+alpha\n"
        "+beta\n"
        "+\n"
        "+gamma\n"
        "+delta\n"
        "+epsilon\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def test_fixture_has_a_blank_separator(self):
        post, _, _ = self._ctx()
        assert post["doc.md"][3] == ""
        assert post["doc.md"][2] == "beta"
        assert post["doc.md"][4] == "gamma"

    def test_dropped_blank_at_the_tail_is_tolerated(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        exc = {"file": "doc.md", "start_line": 1, "end_line": 3,
               "content": "alpha\nbeta"}
        assert validate_excerpt_evidence(exc, hunk_map, post, exempt) is None

    def test_dropped_blank_at_the_head_is_tolerated_too(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # Declares 3-6, quotes the file's own 4-6: the blank at 3 is the
        # separator the reviewer anchored on and did not quote.
        exc = {"file": "doc.md", "start_line": 3, "end_line": 6,
               "content": "gamma\ndelta\nepsilon"}
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is None, err

    def test_short_quote_with_one_gap_degrades_to_untrusted(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # 4-6 are all non-blank; the quote drops the head line gamma but
        # the carried lines align exactly once line 4 is set aside.
        exc = {"file": "doc.md", "start_line": 4, "end_line": 6,
               "content": "delta\nepsilon"}
        a = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert a.status is ExcerptStatus.UNTRUSTED
        assert a.diagnostic is not None
        assert "missing source line 4" in a.diagnostic

    def test_blank_head_does_not_excuse_a_fabricated_quote(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # Blank at the declared start, but the body is not in the file at
        # any shift. Tolerating the count must not tolerate the content.
        exc = {"file": "doc.md", "start_line": 3, "end_line": 6,
               "content": "invented\nlines\nentirely"}
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, "a fabricated body must still be caught"
        assert "declares" not in err, (
            f"the fault is the content, not the count: {err}"
        )


class TestOneGapInTheMiddleIsUntrusted:
    """A quote missing exactly one non-blank middle line is near-miss.

    The reviewer declared the range and transcribed every carried line
    verbatim; only one non-blank line never made it. That is weaker
    evidence than a whole quote but nothing like a fabrication, so the
    assessment degrades to UNTRUSTED and names the missing line.
    """

    _DIFF = (
        "diff --git a/srv.ts b/srv.ts\n"
        "--- /dev/null\n"
        "+++ b/srv.ts\n"
        "@@ -0,0 +1,5 @@\n"
        "+const a = open();\n"
        "+const b = bind(a);\n"
        "+const c = listen(b);\n"
        "+const d = accept(c);\n"
        "+const e = serve(d);\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def test_missing_middle_line_is_untrusted_and_named(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # Declares 1-5, drops line 3 (listen), the rest verbatim.
        exc = {"file": "srv.ts", "start_line": 1, "end_line": 5,
               "content": (
                   "const a = open();\n"
                   "const b = bind(a);\n"
                   "const d = accept(c);\n"
                   "const e = serve(d);"
               )}
        a = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert a.status is ExcerptStatus.UNTRUSTED
        assert a.diagnostic is not None
        assert "missing source line 3" in a.diagnostic

    def test_two_mismatched_lines_stay_invalid(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        exc = {"file": "srv.ts", "start_line": 1, "end_line": 5,
               "content": (
                   "const a = open();\n"
                   "const b = bind(a);\n"
                   "const d = forged(c);\n"
                   "const e = forged(d);"
               )}
        a = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert a.status is ExcerptStatus.INVALID


class TestExtraLeadingBlankIsNotContent:
    """A quote that starts with one extra blank line is still the source.

    The declared range is exact. The model pasted one blank line before
    the body. Content comparison then treats that blank as line one and
    reports the next source line as a mismatch. The body itself matches.
    """

    _DIFF = (
        "diff --git a/m.py b/m.py\n"
        "--- /dev/null\n"
        "+++ b/m.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+\n"
        "+def keep():\n"
        "+    return 1\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def test_one_extra_leading_blank_matches_the_body(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # Source lines 1-3. The quote adds one blank in front of that blank.
        exc = {
            "file": "m.py",
            "start_line": 1,
            "end_line": 3,
            "content": "\n\ndef keep():\n    return 1",
        }
        assert validate_excerpt_evidence(exc, hunk_map, post, exempt) is None

    def test_extra_blank_does_not_hide_a_changed_body(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        exc = {
            "file": "m.py",
            "start_line": 1,
            "end_line": 3,
            "content": "\n\ndef keep():\n    return 2",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None
        assert "content mismatch" in err

    def test_extra_trailing_blank_still_fails(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        exc = {
            "file": "m.py",
            "start_line": 1,
            "end_line": 3,
            "content": "\ndef keep():\n    return 1\n\n",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None


class TestTheBlankIsSpentOnlyOnce:
    """A boundary blank excuses one thing, not two.

    Dropping a leading blank shifts the body down a line. Doing that
    leaves a -1 offset against the file, which the blank-boundary rule
    would then excuse a second time -- and between them the content
    check is skipped entirely, so an excerpt that quietly omits a real
    line of code passes.
    """

    _DIFF = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        "@@ -1,5 +1,5 @@\n"
        "+\n+\n+\n+alpha\n+alpha\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def test_dropping_a_content_line_is_still_caught(self):
        from code_forge.verify import validate_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        # Declares 1-5; carries 4 lines with the last 'alpha' missing.
        # The leading blanks must not launder that away.
        exc = {
            "file": "m.py",
            "start_line": 1,
            "end_line": 5,
            "content": "\n\n\nalpha",
        }
        err = validate_excerpt_evidence(exc, hunk_map, post, exempt)
        assert err is not None, (
            "excerpt dropped a content line and was accepted; the leading "
            "blank was spent both on the body shift and on the offset excuse"
        )

    def test_a_genuine_dropped_separator_still_passes(self):
        from code_forge.verify import validate_excerpt_evidence

        diff = (
            "diff --git a/d.py b/d.py\n--- a/d.py\n+++ b/d.py\n"
            "@@ -1,5 +1,5 @@\n"
            "+\n+alpha\n+beta\n+gamma\n+delta\n"
        )
        from code_forge.verify import _diff_validation_context

        post, hunk_map, exempt = _diff_validation_context(diff)
        exc = {
            "file": "d.py",
            "start_line": 1,
            "end_line": 5,
            "content": "alpha\nbeta\ngamma\ndelta",
        }
        assert validate_excerpt_evidence(exc, hunk_map, post, exempt) is None



class TestIndentStrippedExcerpt:
    """A quote that drops only leading spaces is evidence quality, not a
    dead backend.

    Issue #5: reviewers often left-align a block. rstrip still fails,
    strip matches every line, coordinates are right. That used to
    raise RECEIPT_INVALID and zero the clean-round counter.
    """

    # Modify an existing file. A --- /dev/null add is treated as exempt
    # by parse_diff_hunks, and STEP C would skip the excerpt.
    _DIFF = (
        "diff --git a/s.sh b/s.sh\n"
        "--- a/s.sh\n"
        "+++ b/s.sh\n"
        "@@ -1 +1,7 @@\n"
        "-placeholder\n"
        "+    has_mram1=0\n"
        "+    if [ -e /dev/spi_mram1 ]; then has_mram1=1; fi\n"
        "+    case21_act=foo\n"
        '+    if [ "$case21_act" = SKIP ]; then\n'
        "+        yellow skip\n"
        '+    elif [ "$case21_act" = FAIL ]; then\n'
        "+        red fail\n"
    )

    _STRIPPED = (
        "has_mram1=0\n"
        "if [ -e /dev/spi_mram1 ]; then has_mram1=1; fi\n"
        "case21_act=foo\n"
        'if [ "$case21_act" = SKIP ]; then\n'
        "    yellow skip\n"
        'elif [ "$case21_act" = FAIL ]; then\n'
        "    red fail"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context

        return _diff_validation_context(self._DIFF)

    def _exc(self, content):
        return {
            "file": "s.sh",
            "start_line": 1,
            "end_line": 7,
            "content": content,
        }

    def test_indent_stripped_is_not_a_content_mismatch(self):
        from code_forge.verify import (
            is_indent_stripped,
            is_one_line_misnumber,
            validate_excerpt_evidence,
        )

        post, hunk_map, exempt = self._ctx()
        err = validate_excerpt_evidence(
            self._exc(self._STRIPPED), hunk_map, post, exempt
        )
        assert err is not None, "detector must still see the indent slip"
        assert "content mismatch" not in err, err
        assert is_indent_stripped(err), err
        assert not is_one_line_misnumber(err), err

    def test_a_token_change_is_still_a_mismatch(self):
        from code_forge.verify import (
            is_indent_stripped,
            validate_excerpt_evidence,
        )

        post, hunk_map, exempt = self._ctx()
        err = validate_excerpt_evidence(
            self._exc(self._STRIPPED.replace("red fail", "red FAILX")),
            hunk_map,
            post,
            exempt,
        )
        assert err is not None
        assert "content mismatch" in err
        assert not is_indent_stripped(err)

    def test_run_verify_does_not_fail_the_receipt(self, tmp_path):
        """STEP C used to re-compare with rstrip and kill the run."""
        from code_forge.verify import parse_diff_files, run_verify

        stripped = self._exc(self._STRIPPED)
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha(self._DIFF)
        _write_hardened(rd, sha, excerpts=[stripped])
        r = run_verify(
            tmp_path, sha, parse_diff_files(self._DIFF), diff_text=self._DIFF
        )
        assert r.passed, r.reason


class TestTruncatedLastLinePrefix:
    """A quote whose last line is a strict prefix of the source line.

    Issue 110: the receipt writer cuts a long line, so the last quoted
    line is a proper prefix of the post-image. That is not a content
    mismatch. The assessment completes the tail and records repaired_tail.
    A non-prefix edit, or a prefix anywhere but the last line, stays a
    mismatch.
    """

    _DIFF = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-old\n"
        "+alpha = 1\n"
        "+beta = 2\n"
        "+gamma = a long source line that the receipt cuts\n"
    )

    def _ctx(self):
        from code_forge.verify import _diff_validation_context
        return _diff_validation_context(self._DIFF)

    def test_strict_prefix_on_last_line_is_repaired(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        full = post["src/a.py"][3]
        cut = full[:12]
        assert cut != full and full.startswith(cut)
        exc = {
            "file": "src/a.py",
            "start_line": 1,
            "end_line": 3,
            "content": "alpha = 1\n" + post["src/a.py"][2] + "\n" + cut,
        }
        assessment = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert assessment.status is ExcerptStatus.VALID, assessment.diagnostic
        assert assessment.repaired_tail is True
        assert exc["content"].splitlines()[-1] == full

    def test_list_content_last_line_prefix_is_repaired(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        full = post["src/a.py"][3]
        cut = full[:12]
        exc = {
            "file": "src/a.py",
            "start_line": 1,
            "end_line": 3,
            "content": ["alpha = 1", post["src/a.py"][2], cut],
        }
        assessment = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert assessment.status is ExcerptStatus.VALID, assessment.diagnostic
        assert assessment.repaired_tail is True
        assert exc["content"][-1] == full

    def test_non_prefix_edit_stays_a_mismatch(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        exc = {
            "file": "src/a.py",
            "start_line": 1,
            "end_line": 3,
            "content": "alpha = 1\nbeta = WRONG\ngamma = a long source line that the receipt cuts",
        }
        assessment = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert assessment.status is ExcerptStatus.INVALID
        assert "content mismatch" in (assessment.diagnostic or "")

    def test_prefix_on_an_earlier_line_stays_a_mismatch(self):
        from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

        post, hunk_map, exempt = self._ctx()
        full_mid = post["src/a.py"][2]
        exc = {
            "file": "src/a.py",
            "start_line": 1,
            "end_line": 3,
            "content": "alp\n" + full_mid + "\n" + post["src/a.py"][3],
        }
        assessment = assess_excerpt_evidence(exc, hunk_map, post, exempt)
        assert assessment.status is ExcerptStatus.INVALID
        assert "content mismatch" in (assessment.diagnostic or "")


class TestIndentStrippedClassifier:
    def test_indent_tag_matches(self):
        from code_forge.verify import is_indent_stripped

        assert is_indent_stripped(
            "excerpt indent-stripped at s.sh:1-7"
        )

    def test_content_mismatch_does_not_match(self):
        from code_forge.verify import is_indent_stripped

        assert not is_indent_stripped(
            "excerpt content mismatch at s.sh:1-7 (line 1)"
        )


class TestOnlyLeadingWsDiffers:
    """The predicate behind the indent-stripped verdict.

    It has to answer three different questions, and the call sites only
    ever reach it through the second one, so the other two need asking
    here: a line that already matches is not an indent strip, a line
    whose tokens changed is not one either.
    """

    def test_identical_line_is_not_an_indent_strip(self):
        from code_forge.verify import _only_leading_ws_differs

        line = "    echo hi"
        assert not _only_leading_ws_differs(line, line)

    def test_trailing_ws_only_is_not_an_indent_strip(self):
        from code_forge.verify import _only_leading_ws_differs

        assert not _only_leading_ws_differs("    echo hi   ", "    echo hi")

    def test_leading_ws_dropped_is_an_indent_strip(self):
        from code_forge.verify import _only_leading_ws_differs

        assert _only_leading_ws_differs("echo hi", "    echo hi")

    def test_leading_tabs_count_as_indent(self):
        from code_forge.verify import _only_leading_ws_differs

        assert _only_leading_ws_differs("echo hi", "\t\techo hi")

    def test_changed_token_is_not_an_indent_strip(self):
        from code_forge.verify import _only_leading_ws_differs

        assert not _only_leading_ws_differs("echo bye", "    echo hi")


class TestConfirmedNeedsBoundExcerpt:
    """Issue 108 layer 2: a CONFIRMED finding must carry its own excerpt.

    An envelope-level code_excerpts list does not bind to a finding.
    Missing or empty binding demotes CONFIRMED to UNCERTAIN.
    """

    def test_missing_binding_demotes(self):
        from code_forge.disposition import Disposition
        from code_forge.verify import bound_excerpt_disposition

        assert bound_excerpt_disposition(Disposition.CONFIRMED, None) is Disposition.UNCERTAIN

    def test_empty_binding_demotes(self):
        from code_forge.disposition import Disposition
        from code_forge.verify import bound_excerpt_disposition

        assert bound_excerpt_disposition(Disposition.CONFIRMED, "") is Disposition.UNCERTAIN

    def test_bound_excerpt_keeps_confirmed(self):
        from code_forge.disposition import Disposition
        from code_forge.verify import bound_excerpt_disposition

        assert bound_excerpt_disposition(
            Disposition.CONFIRMED, "alpha = 1\n",
        ) is Disposition.CONFIRMED

    def test_other_dispositions_are_unchanged(self):
        from code_forge.disposition import Disposition
        from code_forge.verify import bound_excerpt_disposition

        assert bound_excerpt_disposition(Disposition.DISMISSED, None) is Disposition.DISMISSED

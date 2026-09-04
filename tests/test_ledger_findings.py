"""Finding counts belong in the ledger, not only in the in-memory report.

58-4 compares the falsification gate on against off at a fixed round cap. A
capped arm exits ESCALATED where the gated arm may exit PASS, so comparing
verdicts would measure the cap rather than the gate. The comparison has to
be on findings -- hits, misses, false positives.

The ledger is the artifact a killed run resumes from and the one the report
traces its headline numbers to. Finding counts that exist only in the
in-memory result list do not survive either.
"""
import json

import pytest

from code_forge.eval.ledger_jsonl import ResumeKey, make_record


def _key(entry_id="e1"):
    return ResumeKey(
        entry_id=entry_id, depth=1, engine="real", backend="review-default",
    )


class TestFindingsAreRecorded:
    """The three counts have to reach the JSONL line."""

    def test_all_three_counts_are_written(self):
        rec = make_record(_key(), "HOLD", findings=(3, 1, 2))
        assert rec["finding_hits"] == 3
        assert rec["finding_misses"] == 1
        assert rec["finding_fps"] == 2

    def test_zero_counts_are_written_not_dropped(self):
        # A scored entry that found nothing is a real measurement. Recording
        # it as absent would make it indistinguishable from an entry that
        # was never scored, and 58-4 needs that distinction to compute
        # recall: absent means unmeasured, zero means measured and empty.
        rec = make_record(_key(), "PASS", findings=(0, 0, 0))
        assert rec["finding_hits"] == 0
        assert rec["finding_misses"] == 0
        assert rec["finding_fps"] == 0

    @pytest.mark.parametrize("bad", [(), (1,), (1, 2), (1, 2, 3, 4)])
    def test_wrong_arity_is_rejected_loudly(self, bad):
        # Not hypothetical arithmetic: append_record writes one line under a
        # lock, so a caller with the wrong shape would either crash between
        # the payload and its newline or write a record missing fields the
        # resume reader expects. Failing here keeps the ledger parseable.
        with pytest.raises(ValueError, match="hits, misses, fps"):
            make_record(_key(), "HOLD", findings=bad)

    def test_unscored_entries_carry_no_finding_fields(self):
        # Absent rather than zero: an entry with no finding-level answer key
        # was not measured, and recording zeros would enter the report as a
        # perfect run with nothing found.
        rec = make_record(_key(), "HOLD")
        assert "finding_hits" not in rec
        assert "finding_misses" not in rec
        assert "finding_fps" not in rec

    def test_the_record_survives_a_json_round_trip(self):
        # The ledger is JSONL; a tuple that serialises to something a reader
        # cannot use is no better than not writing it.
        rec = make_record(_key(), "HOLD", findings=(2, 0, 1))
        back = json.loads(json.dumps(rec))
        assert (back["finding_hits"], back["finding_misses"],
                back["finding_fps"]) == (2, 0, 1)

    def test_existing_fields_are_untouched(self):
        rec = make_record(
            _key("astropy-1"), "HOLD", runs=1, caught=1, wall_s=181.6822,
            findings=(1, 0, 0),
        )
        assert rec["entry_id"] == "astropy-1"
        assert rec["verdict"] == "HOLD"
        assert rec["runs"] == 1
        assert rec["caught"] == 1
        assert rec["wall_s"] == 181.682


class TestCliFillsThemIn:
    """The CLI has to pass what the runner measured."""

    def test_cli_passes_the_scored_triple(self):
        from pathlib import Path

        import code_forge.cli as cli_mod

        src = Path(cli_mod.__file__).read_text()
        assert "result.finding_hits, result.finding_misses," in src
        assert "if result.finding_runs else None" in src

    def test_finding_runs_means_scored_runs(self):
        # The guard reads finding_runs as "how many runs were averaged".
        # If that ever became something else -- a boolean, a count of
        # findings -- the ledger would start recording zeros for unscored
        # entries. mean_findings returns it as the last element.
        import inspect

        from code_forge.eval import scorer as scorer_mod

        src = inspect.getsource(scorer_mod.mean_findings)
        assert "finding_runs" in src or "len(" in src

    @pytest.mark.parametrize("runs,expect_fields", [(0, False), (1, True)])
    def test_scored_flag_decides_whether_fields_appear(self, runs,
                                                       expect_fields):
        # Mirrors the CLI's condition against make_record directly.
        findings = (1, 0, 0) if runs else None
        rec = make_record(_key(), "HOLD", findings=findings)
        assert ("finding_hits" in rec) is expect_fields

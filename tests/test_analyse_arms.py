"""Reporting numbers that can be traced, and refusing ones that cannot.

Phase 58 reports precision, recall and F1 per arm. Two failure modes matter
more than the arithmetic.

A zero that should be n/a. Precision with no reported findings is not 0.0 --
0.0 says the reviewer was wrong every time, when it reported nothing. Same
for standard error at one run per entry: 0.0 reads as "repeated runs agreed"
when nothing was repeated.

An average across arms that were not one experiment. A ledger whose rows
disagree about their own depth or engine is two runs stacked, and averaging
it produces a number for a configuration that never ran.
"""
import importlib.util
import json
import pathlib

import pytest

# Loaded by path, matching test_forge_provider.py. scripts/ is not a package
# and analyse_arms.py is not importable as scripts.analyse_arms unless the
# repo root happens to be on sys.path -- which depends on how pytest was
# invoked rather than on anything this test controls.
#
# Under mutmut the suite runs from a mutants/ copy that holds only the
# mutated package, so the sibling scripts/ directory is absent there and
# the walk up from __file__ lands on a path that was never copied. Fall
# back to the real repo root in that case: this test exercises the script,
# not the mutants, and collection must not error out before it can say so.
def _script_path() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    candidate = here.parents[1] / "scripts" / "analyse_arms.py"
    if candidate.exists():
        return candidate
    for parent in here.parents:
        if parent.name == "mutants":
            outside = parent.parent / "scripts" / "analyse_arms.py"
            if outside.exists():
                return outside
    return candidate


SCRIPT = _script_path()


def _load():
    spec = importlib.util.spec_from_file_location("analyse_arms", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_aa = _load()
_fmt = _aa._fmt
_lines = _aa._lines
_mean_se = _aa._mean_se
load = _aa.load
main = _aa.main
summarise = _aa.summarise


def _row(entry_id="e1", depth=1, engine="real", verdict="HOLD",
         wall_s=100.0, findings=None):
    r = {
        "entry_id": entry_id, "depth": depth, "engine": engine,
        "backend": "review-default", "verdict": verdict, "runs": 1,
        "caught": 0, "wall_s": wall_s,
    }
    if findings is not None:
        r["finding_hits"], r["finding_misses"], r["finding_fps"] = findings
    return r


class TestUndefinedIsNotZero:
    """n/a and 0.0 are different claims."""

    def test_precision_is_none_when_nothing_was_reported(self):
        s = summarise([_row(findings=(0, 3, 0))], "x")
        assert s["precision"] is None, (
            "0.0 would say every reported finding was wrong; none were "
            "reported"
        )
        assert s["recall"] == 0.0, "three misses and no hits is a real zero"

    def test_recall_is_none_when_there_was_nothing_to_find(self):
        s = summarise([_row(findings=(0, 0, 2))], "x")
        assert s["recall"] is None
        assert s["precision"] == 0.0, "two false positives is a real zero"

    def test_f1_is_none_when_either_input_is(self):
        s = summarise([_row(findings=(0, 0, 0))], "x")
        assert s["f1"] is None

    def test_standard_error_is_none_at_n_of_one(self):
        mean, se = _mean_se([5.0])
        assert mean == 5.0
        assert se is None, (
            "0.0 would claim repeated runs agreed; nothing was repeated"
        )

    def test_standard_error_exists_at_n_of_two(self):
        mean, se = _mean_se([4.0, 6.0])
        assert mean == 5.0
        assert se is not None and se > 0

    def test_none_formats_as_na_not_zero(self):
        assert _fmt(None) == "n/a"
        assert _fmt(None, pct=True) == "n/a"
        assert _fmt(0.0, pct=True) == "0.0%"


class TestArithmetic:
    """The ordinary path, so the guards above are not the only coverage."""

    def test_precision_recall_f1(self):
        s = summarise([_row(findings=(6, 2, 3))], "x")
        assert s["precision"] == pytest.approx(6 / 9)
        assert s["recall"] == pytest.approx(6 / 8)
        assert s["f1"] == pytest.approx(2 * (6 / 9) * (6 / 8)
                                        / ((6 / 9) + (6 / 8)))

    def test_counts_sum_across_entries(self):
        rows = [_row("a", findings=(1, 1, 0)), _row("b", findings=(2, 0, 1))]
        s = summarise(rows, "x")
        assert (s["hits"], s["misses"], s["fps"]) == (3, 1, 1)

    def test_unscored_entries_are_excluded_not_zeroed(self):
        # An entry with no answer key must not enter recall as a miss.
        rows = [_row("a", findings=(2, 0, 0)), _row("b")]
        s = summarise(rows, "x")
        assert s["scored"] == 1
        assert s["entries"] == 2
        assert s["recall"] == 1.0, (
            "the unscored entry was counted as a miss; recall would fall "
            "as the corpus grows entries nobody scored"
        )


class TestMixedCoordinatesAreRefused:
    """A ledger holding two configurations is not one arm."""

    def test_mixed_depth_is_reported_not_averaged(self, tmp_path, capsys):
        path = tmp_path / "mixed.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in [
            _row("a", depth=1), _row("b", depth=3),
        ]))
        main([str(path)])
        out = capsys.readouterr().out
        assert "MIXED COORDINATES" in out
        assert "not scorable" in out

    def test_mixed_engine_is_reported_not_averaged(self, tmp_path, capsys):
        path = tmp_path / "mixed.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in [
            _row("a", engine="real"), _row("b", engine="stub"),
        ]))
        main([str(path)])
        assert "MIXED COORDINATES" in capsys.readouterr().out

    def test_missing_coordinates_do_not_crash_the_report(self, tmp_path,
                                                         capsys):
        # Older ledger rows can lack a field entirely. Sorting None against
        # a string raises; the report has to survive reading real history.
        path = tmp_path / "old.jsonl"
        rows = [_row("a"), _row("b")]
        del rows[1]["engine"]
        path.write_text("\n".join(json.dumps(r) for r in rows))
        main([str(path)])
        assert "MIXED COORDINATES" in capsys.readouterr().out


class TestNumbersAreTraceable:
    """The plan requires every headline number to point at ledger lines."""

    def test_verdict_lines_partition_the_file_exactly(self):
        rows = [
            _row("a", verdict="HOLD"), _row("b", verdict="PASS"),
            _row("c", verdict="HOLD"), _row("d", verdict="SKIPPED"),
        ]
        s = summarise(rows, "x")
        claimed = [n for nums in s["verdict_lines"].values() for n in nums]
        assert sorted(claimed) == [1, 2, 3, 4], (
            "line sets must cover every row exactly once, or a count and "
            "its citation disagree"
        )

    def test_each_claimed_line_carries_the_verdict_attributed_to_it(self):
        rows = [
            _row("a", verdict="HOLD"), _row("b", verdict="PASS"),
            _row("c", verdict="HOLD"),
        ]
        s = summarise(rows, "x")
        for verdict, nums in s["verdict_lines"].items():
            for n in nums:
                assert rows[n - 1]["verdict"] == verdict

    def test_line_numbers_are_one_based(self):
        # They name lines in a file a human will open, not list indices.
        s = summarise([_row("only", verdict="HOLD")], "x")
        assert s["verdict_lines"]["HOLD"] == [1]

    def test_scored_lines_name_the_rows_behind_the_metrics(self):
        rows = [_row("a"), _row("b", findings=(1, 0, 0)), _row("c")]
        s = summarise(rows, "x")
        assert s["scored_lines"] == [2]

    def test_truncated_line_lists_say_how_many_were_hidden(self):
        # A citation that trails off cannot be checked for completeness.
        from_lines = _lines(list(range(1, 21)), cap=3)
        assert from_lines.startswith("1,2,3")
        assert "+17 more" in from_lines

    def test_short_line_lists_are_not_truncated(self):
        assert _lines([1, 2, 3]) == "1,2,3"

    def test_traceability_holds_on_the_real_arm_ledger(self):
        # Guards against the citation logic drifting from the ledger format
        # actually being produced. Skipped when no run has happened here.
        import pathlib

        led = pathlib.Path(
            "/home/houminxi/code/forge/.planning/eval/"
            "phase-58-3/arm-d1.jsonl"
        )
        if not led.exists():
            pytest.skip("no 58-3 ledger on this machine")
        rows = load(led)
        s = summarise(rows, "x")
        raw = led.read_text().splitlines()
        for verdict, nums in s["verdict_lines"].items():
            for n in nums:
                assert json.loads(raw[n - 1])["verdict"] == verdict
        claimed = [n for nums in s["verdict_lines"].values() for n in nums]
        assert sorted(claimed) == list(range(1, len(raw) + 1))


class TestLedgerShapes:
    """Both ledger generations have to be readable."""

    def test_verdict_only_ledger_says_so(self, tmp_path, capsys):
        # The 58-3 depth arms were launched before finding counts were
        # recorded. Reporting zeros for them would be an invented result.
        path = tmp_path / "old.jsonl"
        path.write_text("\n".join(json.dumps(_row(str(i))) for i in range(3)))
        main([str(path)])
        out = capsys.readouterr().out
        assert "not recorded in this ledger" in out
        assert "precision" not in out

    def test_missing_file_is_reported_not_crashed(self, tmp_path, capsys):
        main([str(tmp_path / "absent.jsonl")])
        assert "empty or missing" in capsys.readouterr().out

    def test_load_skips_blank_lines(self, tmp_path):
        path = tmp_path / "gappy.jsonl"
        path.write_text(json.dumps(_row()) + "\n\n" + json.dumps(_row("b")))
        assert len(load(path)) == 2

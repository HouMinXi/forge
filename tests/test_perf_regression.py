"""Pin the perf regression check.

The check compares a fresh median against a stored one. These tests drive the
comparison with numbers, so they do not depend on how fast the machine is. The
one test that runs the workload only checks that it returns a positive number.
"""

import json
import subprocess
import sys
import unittest

import scripts.perf_regression as perf


class Compare(unittest.TestCase):
    def record(self, median, definition=perf.DEFINITION, workload=perf.WORKLOAD):
        return {"definition": definition, "workload": workload, "median_s": median}

    def test_a_small_change_stays_within(self):
        self.assertEqual(perf.compare(self.record(1.0), 1.2), "within")

    def test_past_the_limit_is_slower(self):
        self.assertEqual(perf.compare(self.record(1.0), 1.6), "slower")

    def test_exactly_at_the_limit_stays_within(self):
        self.assertEqual(perf.compare(self.record(1.0), 1.5), "within")

    def test_a_faster_result_stays_within(self):
        self.assertEqual(perf.compare(self.record(1.0), 0.4), "within")

    def test_a_non_finite_baseline_is_incomparable(self):
        for value in (float("nan"), float("inf"), 0.0, -1.0):
            self.assertEqual(perf.compare(self.record(value), 1.0), "incomparable", value)

    def test_a_bool_baseline_is_incomparable(self):
        # True is an int in Python, so a type check that stops at int accepts it
        # and treats the baseline as one second.
        self.assertEqual(perf.compare(self.record(True), 1.0), "incomparable")

    def test_an_integer_too_large_to_convert_is_incomparable(self):
        # math.isfinite accepts any int, but multiplying one past the float range
        # raises. That is not a slowdown.
        self.assertEqual(perf.compare(self.record(10**10000), 0.001), "incomparable")

    def test_a_non_finite_result_is_still_json(self):
        result = perf.report(self.record(float("nan")), 1.0)
        self.assertEqual(result["verdict"], "incomparable")
        self.assertIsNone(result["baseline_s"])
        json.dumps(result)

    def test_a_different_workload_is_incomparable(self):
        self.assertEqual(perf.compare(self.record(1.0, workload="other"), 1.0), "incomparable")

    def test_the_limit_is_part_of_the_definition(self):
        self.assertEqual(perf.SLOWDOWN, 0.5)
        self.assertEqual(perf.compare(self.record(1.0), 1.6, slowdown=2.0), "within")


class Report(unittest.TestCase):
    def test_a_slow_result_carries_the_definition(self):
        result = perf.report(
            {"definition": perf.DEFINITION, "workload": perf.WORKLOAD, "median_s": 1.0}, 1.6
        )
        self.assertEqual(result["definition"], "forge-perf-regression-v1")
        self.assertEqual(result["workload"], "parse_diff_files")
        self.assertEqual(result["verdict"], "slower")
        self.assertEqual(result["baseline_s"], 1.0)
        self.assertEqual(result["median_s"], 1.6)

    def test_the_command_exits_nonzero_when_slower(self):
        import tempfile

        record = {"definition": perf.DEFINITION, "workload": perf.WORKLOAD, "median_s": 1e-7}
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(record, handle)
            handle.flush()
            run = subprocess.run(
                [sys.executable, "scripts/perf_regression.py", "--baseline", handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["verdict"], "slower")

    def test_the_command_rejects_a_record_it_cannot_read(self):
        run = subprocess.run(
            [sys.executable, "scripts/perf_regression.py", "--baseline", "no-such-file.json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["verdict"], "incomparable")

    def test_a_record_from_another_definition_exits_incomparable(self):
        import tempfile

        record = {"definition": "other", "workload": perf.WORKLOAD, "median_s": 1.0}
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(record, handle)
            handle.flush()
            run = subprocess.run(
                [sys.executable, "scripts/perf_regression.py", "--baseline", handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["verdict"], "incomparable")

    def test_a_non_utf8_record_exits_incomparable(self):
        import tempfile

        with tempfile.NamedTemporaryFile("wb", suffix=".json") as handle:
            handle.write(b"\xff\xfe not json")
            handle.flush()
            run = subprocess.run(
                [sys.executable, "scripts/perf_regression.py", "--baseline", handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["verdict"], "incomparable")


class Workload(unittest.TestCase):
    def test_the_median_is_a_positive_number(self):
        median = perf.measure(runs=3)
        self.assertGreater(median, 0.0)

    def test_a_parser_that_returns_nothing_is_refused(self):
        # The timed call has to do the work. A parser that hands back an empty
        # result must not be allowed to report a time.
        import unittest.mock

        with unittest.mock.patch.object(perf, "parse_diff_files", return_value={}):
            with self.assertRaises(RuntimeError):
                perf.measure(runs=1)

    def test_one_slow_sample_does_not_move_the_median(self):
        # Five fast samples and two slow ones: the median is one of the fast
        # ones, so a single stall is not reported as a regression.
        samples = [0.01, 0.01, 0.01, 0.01, 0.01, 9.0, 9.0]
        import statistics

        self.assertEqual(statistics.median(samples), 0.01)
        self.assertEqual(
            perf.compare(
                {"definition": perf.DEFINITION, "workload": perf.WORKLOAD, "median_s": 0.01},
                statistics.median(samples),
            ),
            "within",
        )


if __name__ == "__main__":
    unittest.main()

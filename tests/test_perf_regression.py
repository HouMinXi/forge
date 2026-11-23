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
    def test_a_small_change_stays_within(self):
        self.assertEqual(perf.compare(1.0, 1.2), "within")

    def test_past_the_limit_is_slower(self):
        self.assertEqual(perf.compare(1.0, 1.6), "slower")

    def test_exactly_at_the_limit_stays_within(self):
        self.assertEqual(perf.compare(1.0, 1.5), "within")

    def test_a_faster_result_stays_within(self):
        self.assertEqual(perf.compare(1.0, 0.4), "within")

    def test_a_zero_baseline_is_refused(self):
        with self.assertRaises(ValueError):
            perf.compare(0.0, 1.0)

    def test_the_limit_is_part_of_the_definition(self):
        # A caller that picks its own fraction can make any change pass.
        self.assertEqual(perf.SLOWDOWN, 0.5)
        self.assertEqual(perf.compare(1.0, 1.6, slowdown=2.0), "within")


class Report(unittest.TestCase):
    def test_a_slow_result_carries_the_definition(self):
        result = perf.report(1.0, 1.6)
        self.assertEqual(result["definition"], "forge-perf-regression-v1")
        self.assertEqual(result["workload"], "parse_diff_files")
        self.assertEqual(result["verdict"], "slower")
        self.assertEqual(result["baseline_s"], 1.0)
        self.assertEqual(result["median_s"], 1.6)

    def test_the_command_exits_nonzero_when_slower(self):
        run = subprocess.run(
            [sys.executable, "scripts/perf_regression.py", "--baseline", "0.0000001"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["verdict"], "slower")


class Workload(unittest.TestCase):
    def test_the_median_is_a_positive_number(self):
        median = perf.measure(runs=3)
        self.assertGreater(median, 0.0)

    def test_one_slow_sample_does_not_move_the_median(self):
        # Five fast samples and two slow ones: the median is one of the fast
        # ones, so a single stall is not reported as a regression.
        samples = [0.01, 0.01, 0.01, 0.01, 0.01, 9.0, 9.0]
        import statistics

        self.assertEqual(statistics.median(samples), 0.01)
        self.assertEqual(perf.compare(0.01, statistics.median(samples)), "within")


if __name__ == "__main__":
    unittest.main()

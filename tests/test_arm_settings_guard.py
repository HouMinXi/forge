"""An arm must not spend a review whose settings did not arrive.

Phase 58 varies review depth and the falsification engine across arms and
compares the results. Each knob crosses two boundaries: parent to pool
worker, then worker to the review subprocess. Neither crossing raises when
it fails. The arm runs at forge's defaults, its numbers are real, and they
answer a different question than the one asked -- indistinguishable, from
the output alone, from a genuine result.

This was not hypothetical. Measured earlier in 58-3: with the depth
inherited rather than passed, arm 1's value reached its workers and arms 2
and 3 silently ran at arm 1's depth.

The guard turns that into a refusal before the first review is spent.
"""
import pytest

from code_forge.eval.runner import _ARM_KNOBS, _missing_arm_settings


class TestUnclaimedRunsAreUntouched:
    """Ordinary eval runs set no arm knobs and must not be affected."""

    def test_no_claim_means_no_requirement(self):
        assert _missing_arm_settings({}) == []

    def test_blank_claim_means_no_requirement(self):
        assert _missing_arm_settings({"FORGE_ARM_REQUIRES": "  "}) == []

    def test_knobs_present_without_a_claim_are_not_checked(self):
        # A knob set by something other than an arm is that caller's
        # business; the guard only enforces what an arm claimed.
        env = {"FORGE_CLEAN_ROUND_THRESHOLD": "2"}
        assert _missing_arm_settings(env) == []


class TestClaimedSettingsAreEnforced:
    """A claimed knob that did not arrive stops the arm."""

    @pytest.mark.parametrize("knob", _ARM_KNOBS)
    def test_each_knob_is_enforceable(self, knob):
        assert _missing_arm_settings({"FORGE_ARM_REQUIRES": knob}) == [knob]

    @pytest.mark.parametrize("knob", _ARM_KNOBS)
    def test_a_present_knob_satisfies_its_claim(self, knob):
        env = {"FORGE_ARM_REQUIRES": knob, knob: "2"}
        assert _missing_arm_settings(env) == []

    def test_several_claims_report_every_missing_one(self):
        env = {
            "FORGE_ARM_REQUIRES":
                "FORGE_CLEAN_ROUND_THRESHOLD,FORGE_MAX_TOTAL_ROUNDS",
            "FORGE_CLEAN_ROUND_THRESHOLD": "3",
        }
        assert _missing_arm_settings(env) == ["FORGE_MAX_TOTAL_ROUNDS"]

    def test_empty_string_counts_as_absent(self):
        # How a shell exports an unset variable, and how forge's
        # env_resolver already reads it. Accepting it would defeat the
        # check for the case most likely to happen.
        env = {
            "FORGE_ARM_REQUIRES": "FORGE_MAX_TOTAL_ROUNDS",
            "FORGE_MAX_TOTAL_ROUNDS": "",
        }
        assert _missing_arm_settings(env) == ["FORGE_MAX_TOTAL_ROUNDS"]

    def test_whitespace_only_counts_as_absent(self):
        env = {
            "FORGE_ARM_REQUIRES": "FORGE_FALSIFICATION_ENGINE",
            "FORGE_FALSIFICATION_ENGINE": "   ",
        }
        assert _missing_arm_settings(env) == ["FORGE_FALSIFICATION_ENGINE"]

    def test_a_typo_in_the_claim_is_reported_not_ignored(self):
        # A claim naming a knob that does not exist would otherwise guard
        # nothing while looking like it guards something.
        env = {"FORGE_ARM_REQUIRES": "FORGE_CLEAN_ROUND_THRESHOL"}
        out = _missing_arm_settings(env)
        assert out and "unknown knob" in out[0]

    def test_claim_parsing_tolerates_spacing(self):
        env = {
            "FORGE_ARM_REQUIRES":
                " FORGE_CLEAN_ROUND_THRESHOLD , FORGE_MAX_TOTAL_ROUNDS ",
            "FORGE_CLEAN_ROUND_THRESHOLD": "1",
            "FORGE_MAX_TOTAL_ROUNDS": "8",
        }
        assert _missing_arm_settings(env) == []


class TestWiring:
    """The guard has to sit before the review, and the CLI has to claim."""

    def test_runner_checks_before_spending_a_review(self):
        import inspect

        from code_forge.eval import runner as runner_mod

        src = inspect.getsource(runner_mod)
        body = src.split("timeout_s = _review_timeout_s()")[1]
        guard = body.index("_missing_arm_settings(eval_env)")
        review = body.index("_run_review(")
        assert guard < review, (
            "the guard must run before _run_review, or the arm has already "
            "paid for the review it should have refused"
        )

    def test_the_refusal_is_an_infra_failure(self):
        # Scored as infrastructure, not as the reviewer finding nothing.
        # A silent zero would enter the metrics as a real miss.
        import inspect

        from code_forge.eval import runner as runner_mod

        src = inspect.getsource(runner_mod)
        assert 'infra: arm settings absent' in src

    def test_cli_claims_the_depth_knob(self):
        # Behavioural rather than textual: the arm builder must claim the
        # depth knob it sets. An earlier version asserted on a literal in
        # cli.py's source and broke when the same behaviour moved into
        # _arm_env_overrides -- it was tracking where the code lived, not
        # what it did.
        from code_forge.cli import _arm_env_overrides

        class _Args:
            arm_depth = 2
            arm_engine = "real"

        out = _arm_env_overrides(_Args())
        assert out["FORGE_CLEAN_ROUND_THRESHOLD"] == "2"
        assert "FORGE_CLEAN_ROUND_THRESHOLD" in out["FORGE_ARM_REQUIRES"]

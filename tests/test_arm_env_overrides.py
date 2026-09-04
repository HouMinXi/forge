"""What an eval arm imposes on the reviews it runs.

58-4 compares the falsification gate on against off. Both arms must run at
the same round cap, because with the gate off no candidate is ever rejected,
every round resets the clean-round counter, and the loop runs to whatever
bound exists -- at forge's default of 20 that is roughly 95 hours per arm on
this corpus.

The cap and the engine only mean anything if they reach the review. This
covers the CLI end: what the arm carries to its workers, and what it claims
so the runner can refuse a review that lost one.
"""
import pytest

from code_forge.cli import _arm_env_overrides


class _Args:
    def __init__(self, arm_depth=1, arm_engine="real"):
        self.arm_depth = arm_depth
        self.arm_engine = arm_engine


class TestDepthIsAlwaysCarried:
    """Every arm varies depth, so it is always set and always claimed."""

    @pytest.mark.parametrize("depth", [1, 2, 3])
    def test_depth_is_passed_by_value(self, depth, monkeypatch):
        monkeypatch.delenv("FORGE_FALSIFICATION_ENGINE", raising=False)
        monkeypatch.delenv("FORGE_MAX_TOTAL_ROUNDS", raising=False)
        out = _arm_env_overrides(_Args(arm_depth=depth))
        assert out["FORGE_CLEAN_ROUND_THRESHOLD"] == str(depth)

    def test_depth_is_a_string(self, monkeypatch):
        # os.environ rejects non-strings; an int here raises inside the
        # pool worker, after the run has already started.
        monkeypatch.delenv("FORGE_FALSIFICATION_ENGINE", raising=False)
        monkeypatch.delenv("FORGE_MAX_TOTAL_ROUNDS", raising=False)
        out = _arm_env_overrides(_Args(arm_depth=2))
        assert isinstance(out["FORGE_CLEAN_ROUND_THRESHOLD"], str)

    def test_depth_is_always_claimed(self, monkeypatch):
        monkeypatch.delenv("FORGE_FALSIFICATION_ENGINE", raising=False)
        monkeypatch.delenv("FORGE_MAX_TOTAL_ROUNDS", raising=False)
        out = _arm_env_overrides(_Args())
        assert out["FORGE_ARM_REQUIRES"] == "FORGE_CLEAN_ROUND_THRESHOLD"


class TestAblationKnobsAreCarriedAndClaimed:
    """58-4's knobs must reach the review and be guarded on the way."""

    def test_engine_is_carried_when_set(self, monkeypatch):
        monkeypatch.setenv("FORGE_FALSIFICATION_ENGINE", "stub")
        monkeypatch.delenv("FORGE_MAX_TOTAL_ROUNDS", raising=False)
        out = _arm_env_overrides(_Args())
        assert out["FORGE_FALSIFICATION_ENGINE"] == "stub"
        assert "FORGE_FALSIFICATION_ENGINE" in out["FORGE_ARM_REQUIRES"]

    def test_cap_is_carried_when_set(self, monkeypatch):
        monkeypatch.delenv("FORGE_FALSIFICATION_ENGINE", raising=False)
        monkeypatch.setenv("FORGE_MAX_TOTAL_ROUNDS", "3")
        out = _arm_env_overrides(_Args())
        assert out["FORGE_MAX_TOTAL_ROUNDS"] == "3"
        assert "FORGE_MAX_TOTAL_ROUNDS" in out["FORGE_ARM_REQUIRES"]

    def test_the_full_ablation_arm_claims_all_three(self, monkeypatch):
        # The shape run_58_4.sh produces. Losing the cap here is the
        # expensive failure: the gate-off arm would run to 20 rounds.
        monkeypatch.setenv("FORGE_FALSIFICATION_ENGINE", "stub")
        monkeypatch.setenv("FORGE_MAX_TOTAL_ROUNDS", "3")
        out = _arm_env_overrides(_Args(arm_depth=3, arm_engine="stub"))
        claimed = set(out["FORGE_ARM_REQUIRES"].split(","))
        assert claimed == {
            "FORGE_CLEAN_ROUND_THRESHOLD",
            "FORGE_FALSIFICATION_ENGINE",
            "FORGE_MAX_TOTAL_ROUNDS",
        }


class TestUnsetKnobsAreNotClaimed:
    """Ordinary eval runs set neither knob and must not be broken."""

    def test_nothing_extra_claimed_when_unset(self, monkeypatch):
        monkeypatch.delenv("FORGE_FALSIFICATION_ENGINE", raising=False)
        monkeypatch.delenv("FORGE_MAX_TOTAL_ROUNDS", raising=False)
        out = _arm_env_overrides(_Args())
        assert "FORGE_FALSIFICATION_ENGINE" not in out
        assert "FORGE_MAX_TOTAL_ROUNDS" not in out

    def test_empty_env_value_is_not_claimed(self, monkeypatch):
        # An exported-but-empty variable is how a shell passes an unset one.
        # Claiming it would make the runner refuse every review in an
        # ordinary run.
        monkeypatch.setenv("FORGE_MAX_TOTAL_ROUNDS", "")
        monkeypatch.delenv("FORGE_FALSIFICATION_ENGINE", raising=False)
        out = _arm_env_overrides(_Args())
        assert "FORGE_MAX_TOTAL_ROUNDS" not in out
        assert out["FORGE_ARM_REQUIRES"] == "FORGE_CLEAN_ROUND_THRESHOLD"

    def test_whitespace_env_value_is_not_claimed(self, monkeypatch):
        monkeypatch.setenv("FORGE_FALSIFICATION_ENGINE", "  ")
        monkeypatch.delenv("FORGE_MAX_TOTAL_ROUNDS", raising=False)
        out = _arm_env_overrides(_Args())
        assert "FORGE_FALSIFICATION_ENGINE" not in out


class TestGuardAcceptsWhatTheArmProduces:
    """The two halves have to agree, or the guard rejects valid arms."""

    @pytest.mark.parametrize("engine,cap", [
        (None, None), ("stub", "3"), ("auto", "3"), ("stub", None),
    ])
    def test_runner_accepts_every_arm_shape(self, engine, cap, monkeypatch):
        from code_forge.eval.runner import _missing_arm_settings

        for name, value in (
            ("FORGE_FALSIFICATION_ENGINE", engine),
            ("FORGE_MAX_TOTAL_ROUNDS", cap),
        ):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

        overrides = _arm_env_overrides(_Args(arm_depth=3))
        # The environment the review subprocess would receive.
        env = dict(overrides)
        assert _missing_arm_settings(env) == [], (
            "the guard rejects an arm the CLI just built; the claim and the "
            "check disagree"
        )

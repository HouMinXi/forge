"""#282: idle gate for the L2 mutation baseline."""

import pytest

from code_forge import host_idle, mutation
from code_forge.host_idle import IdleConfig, IdleConfigError, busy_reasons, load_config, wait_for_idle

CFG = IdleConfig(max_wait=100, poll=10, settle=3, load=0.5, psi_cpu=10, psi_mem=5, mem_avail=0.15)
IDLE = {"load_per_cpu": 0.1, "psi_cpu_some_avg10": 1.0, "psi_mem_some_avg10": 0.0, "mem_avail_ratio": 0.5}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_defaults_and_overrides():
    cfg = load_config({})
    assert (cfg.max_wait, cfg.poll, cfg.settle, cfg.load) == (1800.0, 10.0, 3, 0.5)
    cfg = load_config({"FORGE_MUTATION_IDLE_MAX_WAIT": "0", "FORGE_MUTATION_IDLE_LOAD": "0.8"})
    assert cfg.max_wait == 0 and cfg.load == 0.8


@pytest.mark.parametrize("raw", ["x", "-1", "nan", "inf"])
def test_invalid_config_is_an_error(raw):
    with pytest.raises(IdleConfigError, match="FORGE_MUTATION_IDLE_LOAD"):
        load_config({"FORGE_MUTATION_IDLE_LOAD": raw})
    with pytest.raises(IdleConfigError):
        load_config({"FORGE_MUTATION_IDLE_POLL": "0"})


def test_busy_reasons_each_signal_and_unavailable_ignored():
    assert busy_reasons(IDLE, CFG) == []
    assert busy_reasons({k: None for k in IDLE}, CFG) == []
    busy = {"load_per_cpu": 0.9, "psi_cpu_some_avg10": 20.0, "psi_mem_some_avg10": 6.0, "mem_avail_ratio": 0.1}
    assert busy_reasons(busy, CFG) == [
        "loadavg/cpu 0.90 > 0.5", "cpu PSI some avg10 20.0 > 10",
        "memory PSI some avg10 6.0 > 5", "MemAvailable 10% < 15%",
    ]
    # Thresholds are inclusive limits.
    assert busy_reasons({"load_per_cpu": 0.5, "mem_avail_ratio": 0.15}, CFG) == []


def test_waits_for_settled_idle():
    clock = FakeClock()
    seq = iter([{"load_per_cpu": 2.0}, IDLE, {"load_per_cpu": 2.0}, IDLE, IDLE, IDLE])
    seen = []
    result = wait_for_idle(CFG, read=lambda: next(seq), sleep=clock.sleep, clock=clock, on_wait=seen.append)
    assert result.idle and result.samples == 6 and result.waited_s == 50
    assert len(seen) == 2


def test_never_idle_gives_up_at_max_wait():
    clock = FakeClock()
    result = wait_for_idle(CFG, read=lambda: {"psi_mem_some_avg10": 11.5}, sleep=clock.sleep, clock=clock)
    assert not result.idle and result.waited_s <= CFG.max_wait
    assert result.reasons == ["memory PSI some avg10 11.5 > 5"]
    assert result.record()["idle"] is False


def test_disabled_never_samples():
    def boom():
        raise AssertionError("sampled")
    result = wait_for_idle(load_config({"FORGE_MUTATION_IDLE_MAX_WAIT": "0"}), read=boom)
    assert result.idle and result.samples == 0


def test_read_signals_real_host():
    signals = host_idle.read_signals()
    assert set(signals) == set(IDLE)


def test_run_mutation_bad_idle_config_is_error(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/add.py").write_text("def add(a, b):\n    return a + b\n")
    monkeypatch.setenv("FORGE_MUTATION_IDLE_MAX_WAIT", "soon")
    called = []
    monkeypatch.setattr(mutation, "run_owned_command", lambda *a, **k: called.append(a))
    findings, infra = mutation.run_mutation(["src/add.py"], ["pytest"], cwd=tmp_path)
    assert not called
    assert any("mutation idle gate: FORGE_MUTATION_IDLE_MAX_WAIT" in e for e in infra)
    assert [f.id for f in findings] == ["MUTATION_ERROR"]

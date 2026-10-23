"""Wait for an idle host before the L2 mutation baseline (#282).

The 3x baseline is a serial ~27 minute suite whose subprocess timeouts flake
when other suites, forges or memory stalls share the box. Running it only on an
idle host removes that noise. If the host never settles within the max wait,
the caller reports an explicit ``mutation-host-busy`` skip (never a pass).

Thresholds are host properties, so they come from the environment, not
gate.yaml. ``FORGE_MUTATION_IDLE_MAX_WAIT=0`` disables the gate.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

_ENV = {
    "max_wait": ("FORGE_MUTATION_IDLE_MAX_WAIT", 1800.0),
    "poll": ("FORGE_MUTATION_IDLE_POLL", 10.0),
    "settle": ("FORGE_MUTATION_IDLE_SETTLE", 3.0),
    "load": ("FORGE_MUTATION_IDLE_LOAD", 0.5),
    "psi_cpu": ("FORGE_MUTATION_IDLE_PSI_CPU", 10.0),
    "psi_mem": ("FORGE_MUTATION_IDLE_PSI_MEM", 5.0),
    "mem_avail": ("FORGE_MUTATION_IDLE_MEM_AVAIL", 0.15),
}


class IdleConfigError(ValueError):
    """An idle-gate environment variable is not a valid number."""


@dataclass(frozen=True)
class IdleConfig:
    max_wait: float
    poll: float
    settle: int
    load: float
    psi_cpu: float
    psi_mem: float
    mem_avail: float


@dataclass
class IdleResult:
    idle: bool
    waited_s: float
    samples: int
    last: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def record(self) -> dict:
        return {"idle": self.idle, "waited_s": round(self.waited_s, 1), "samples": self.samples,
                "last": self.last, "reasons": list(self.reasons)}


def load_config(env: dict | None = None) -> IdleConfig:
    env = os.environ if env is None else env
    values = {}
    for key, (name, default) in _ENV.items():
        raw = env.get(name)
        if raw is None or raw == "":
            values[key] = default
            continue
        try:
            value = float(raw)
        except ValueError:
            raise IdleConfigError(f"{name} must be a number, got {raw!r}") from None
        if value != value or value < 0 or value == float("inf"):
            raise IdleConfigError(f"{name} must be a finite number >= 0, got {raw!r}")
        values[key] = value
    if values["poll"] <= 0:
        raise IdleConfigError("FORGE_MUTATION_IDLE_POLL must be > 0")
    values["settle"] = max(1, int(values["settle"]))
    return IdleConfig(**values)


def _psi_some_avg10(path: str) -> float | None:
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("some "):
                    for part in line.split()[1:]:
                        if part.startswith("avg10="):
                            return float(part[6:])
    except (OSError, ValueError):
        return None
    return None


def _mem_avail_ratio() -> float | None:
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0])
        return info["MemAvailable"] / info["MemTotal"]
    except (OSError, ValueError, KeyError, ZeroDivisionError, IndexError):
        return None


def read_signals() -> dict:
    """Current readings; a signal that cannot be read is None (and ignored)."""
    try:
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
    except OSError:
        load = None
    return {
        "load_per_cpu": load,
        "psi_cpu_some_avg10": _psi_some_avg10("/proc/pressure/cpu"),
        "psi_mem_some_avg10": _psi_some_avg10("/proc/pressure/memory"),
        "mem_avail_ratio": _mem_avail_ratio(),
    }


def busy_reasons(signals: dict, cfg: IdleConfig) -> list[str]:
    reasons = []
    checks = (
        ("load_per_cpu", cfg.load, "loadavg/cpu {v:.2f} > {t}", False),
        ("psi_cpu_some_avg10", cfg.psi_cpu, "cpu PSI some avg10 {v:.1f} > {t}", False),
        ("psi_mem_some_avg10", cfg.psi_mem, "memory PSI some avg10 {v:.1f} > {t}", False),
        ("mem_avail_ratio", cfg.mem_avail, "MemAvailable {v:.0%} < {t:.0%}", True),
    )
    for key, threshold, fmt, minimum in checks:
        value = signals.get(key)
        if value is None:
            continue
        if (value < threshold) if minimum else (value > threshold):
            reasons.append(fmt.format(v=value, t=threshold))
    return reasons


def wait_for_idle(
    cfg: IdleConfig,
    *,
    read: Callable[[], dict] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    on_wait: Callable[[list[str]], None] | None = None,
) -> IdleResult:
    """Poll until ``cfg.settle`` consecutive idle samples or ``cfg.max_wait`` elapses."""
    read = read_signals if read is None else read
    sleep = time.sleep if sleep is None else sleep
    clock = time.monotonic if clock is None else clock
    start = clock()
    if cfg.max_wait <= 0:
        return IdleResult(idle=True, waited_s=0.0, samples=0, last={}, reasons=["disabled"])
    streak = samples = 0
    while True:
        signals = read()
        samples += 1
        reasons = busy_reasons(signals, cfg)
        streak = 0 if reasons else streak + 1
        waited = clock() - start
        if streak >= cfg.settle:
            return IdleResult(True, waited, samples, signals, [])
        if waited + cfg.poll > cfg.max_wait:
            # Last reasons; a host that just went idle but has not settled is still busy.
            return IdleResult(False, waited, samples, signals, reasons or ["not idle long enough to settle"])
        if reasons and on_wait is not None:
            on_wait(reasons)
        sleep(cfg.poll)

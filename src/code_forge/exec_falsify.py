# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@hotmail.com>
"""Phase 53a: EXEC-FALSIFY v1 -- native execution falsifier.

Runs the reviewed diff's declared test/build command in the
manifest-declared environment, synchronously with timeout budget.
Evidence is asymmetric per EXEC-03: FAIL_BEFORE strengthens basis,
PASS_AFTER is receipt-level only, TIMEOUT/UNAVAILABLE are explicit
disclosures that never read as clean.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ExecStatus(str, Enum):
    """Execution falsifier outcome."""

    FAIL_BEFORE = "fail_before"
    PASS_AFTER = "pass_after"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ExecEvidence:
    """Frozen record of an execution falsification attempt."""

    status: ExecStatus
    command: str
    exit_code: Optional[int]
    duration_s: float
    stderr_tail: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dictionary."""
        d: dict[str, Any] = {
            "status": self.status.value,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "stderr_tail": self.stderr_tail,
            "reason": self.reason,
        }
        return d


class ExecFalsifier:
    """Execute test/build commands against the reviewed tree.

    Read-only organ: never writes to the reviewed code tree.
    """

    def __init__(
        self,
        manifest: Optional[dict[str, Any] | Any] = None,
        timeout_seconds: int = 120,
        command: Optional[list[str]] = None,
    ) -> None:
        # Accept dict or EnvManifest (duck-type .to_dict())
        if manifest is not None and hasattr(manifest, "to_dict"):
            self._manifest: Optional[dict[str, Any]] = manifest.to_dict()  # type: ignore[union-attr]
        else:
            self._manifest = manifest
        self._timeout = timeout_seconds
        self._explicit_command = command

    def run(self, cwd: Path) -> ExecEvidence:
        """Execute and return evidence.

        Tier gate (EXEC-02): only DECLARED tier executes.
        ABSENT/OBSERVED return UNAVAILABLE without spawning subprocesses.
        """
        t0 = time.monotonic()

        # Tier gate
        if self._manifest is None:
            return ExecEvidence(
                status=ExecStatus.UNAVAILABLE,
                command="",
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stderr_tail="",
                reason="exec falsification requires DECLARED manifest tier",
            )

        tier = self._manifest.get("tier", "")
        if not isinstance(tier, str) or tier.lower() != "declared":
            return ExecEvidence(
                status=ExecStatus.UNAVAILABLE,
                command="",
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stderr_tail="",
                reason="exec falsification requires DECLARED manifest tier",
            )

        # Command resolution
        cmd = self._resolve_command()
        if cmd is None:
            return ExecEvidence(
                status=ExecStatus.UNAVAILABLE,
                command="",
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stderr_tail="",
                reason="no test command configured for runtime",
            )

        cmd_str = " ".join(cmd)

        # Subprocess execution
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return ExecEvidence(
                status=ExecStatus.TIMEOUT,
                command=cmd_str,
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stderr_tail="",
                reason="budget exhausted",
            )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
            return ExecEvidence(
                status=ExecStatus.UNAVAILABLE,
                command=cmd_str,
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stderr_tail="",
                reason="%s: %s" % (type(exc).__name__, exc),
            )

        duration = time.monotonic() - t0
        stderr_tail = (result.stderr or "")[-2000:]

        if result.returncode != 0:
            return ExecEvidence(
                status=ExecStatus.FAIL_BEFORE,
                command=cmd_str,
                exit_code=result.returncode,
                duration_s=duration,
                stderr_tail=stderr_tail,
                reason="",
            )

        return ExecEvidence(
            status=ExecStatus.PASS_AFTER,
            command=cmd_str,
            exit_code=0,
            duration_s=duration,
            stderr_tail=stderr_tail,
            reason="",
        )

    def _resolve_command(self) -> Optional[list[str]]:
        """Resolve command: explicit > gate.yaml test.command > runtime default."""
        if self._explicit_command:
            return list(self._explicit_command)

        # Runtime default for Python
        if self._manifest is not None:
            runtime_name = self._manifest.get("runtime_name", "")
            if isinstance(runtime_name, str) and runtime_name.lower() == "python":
                runtime_bin = self._manifest.get("runtime_bin", "")
                if not runtime_bin:
                    runtime_bin = sys.executable
                return [runtime_bin, "-m", "pytest"]

        return None

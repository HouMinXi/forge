# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@glint.org>
"""Phase 53a: EXEC-FALSIFY v1 -- native execution falsifier.

Runs the reviewed diff's declared test/build command in the
manifest-declared environment, synchronously with timeout budget.
Evidence is asymmetric per EXEC-03: FAIL_BEFORE strengthens basis,
PASS_AFTER is receipt-level only, TIMEOUT/UNAVAILABLE are explicit
disclosures that never read as clean.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
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
    command: list[str] | str
    exit_code: Optional[int]
    duration_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    reason: str = ""
    environment: dict[str, Any] = field(default_factory=dict)

    @property
    def command_argv(self) -> list[str]:
        """Return argv list."""
        if isinstance(self.command, list):
            return list(self.command)
        if isinstance(self.command, str):
            return self.command.split() if self.command else []
        return [str(self.command)]

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dictionary."""
        if isinstance(self.command, list):
            cmd_str = " ".join(self.command)
            cmd_argv = list(self.command)
        elif isinstance(self.command, str):
            cmd_str = self.command
            cmd_argv = self.command.split() if self.command else []
        else:
            cmd_str = str(self.command)
            cmd_argv = [str(self.command)]

        return {
            "status": self.status.value,
            "command": cmd_str,
            "command_argv": cmd_argv,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "reason": self.reason,
            "environment": dict(self.environment),
        }


_SAFE_ENV_VARS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TEMP", "TMP", "PYTHONPATH", "VIRTUAL_ENV", "TERM",
    "SAFE_TOOLCHAIN_VAR",
})

_SENSITIVE_PREFIXES = (
    "ANTHROPIC_", "OPENAI_", "MIMO_", "HERMES_", "CUSTOM_SECRET",
    "FORGE_API_KEY", "DEEPSEEK_", "AWS_", "GITHUB_TOKEN",
)


def _clean_subprocess_env(extra_env: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Build minimal allowlisted environment stripping reviewer secrets."""
    clean: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in _SAFE_ENV_VARS:
            clean[k] = v
        elif k.startswith("LC_") or k.startswith("LANG_"):
            clean[k] = v

    if extra_env:
        for k, v in extra_env.items():
            k_upper = k.upper()
            if not any(k_upper.startswith(p) for p in _SENSITIVE_PREFIXES):
                if "SECRET" not in k_upper and "TOKEN" not in k_upper and "API_KEY" not in k_upper:
                    clean[k] = str(v)

    # Strip any sensitive tokens unconditionally
    for k in list(clean.keys()):
        k_upper = k.upper()
        if any(k_upper.startswith(p) for p in _SENSITIVE_PREFIXES):
            clean.pop(k, None)
        elif "TOKEN" in k_upper or "SECRET" in k_upper:
            clean.pop(k, None)

    return clean


def _read_file_tail(f: Any, limit: int = 2000) -> str:
    """Read last *limit* characters from a binary file."""
    f.seek(0, os.SEEK_END)
    size = f.tell()
    seek_pos = max(0, size - limit * 4)
    f.seek(seek_pos, os.SEEK_SET)
    raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    return text[-limit:]


def _kill_process_tree(p: subprocess.Popen) -> None:
    """Terminate entire process group on timeout or error."""
    try:
        pgid = os.getpgid(p.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        p.wait(timeout=1.0)
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError, OSError):
        pass


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
                command=self._explicit_command or [],
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stdout_tail="",
                stderr_tail="",
                reason="exec falsification requires DECLARED manifest tier",
                environment={},
            )

        tier = self._manifest.get("tier", "")
        if not isinstance(tier, str) or tier.lower() != "declared":
            return ExecEvidence(
                status=ExecStatus.UNAVAILABLE,
                command=self._explicit_command or [],
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stdout_tail="",
                stderr_tail="",
                reason="exec falsification requires DECLARED manifest tier",
                environment={},
            )

        # Environment identity & lockfile verification
        manifest_path = self._manifest.get("manifest_path")
        lockfile_hash = ""
        if manifest_path:
            lock_file = cwd / manifest_path
            if not lock_file.is_file():
                return ExecEvidence(
                    status=ExecStatus.UNAVAILABLE,
                    command=self._explicit_command or [],
                    exit_code=None,
                    duration_s=time.monotonic() - t0,
                    stdout_tail="",
                    stderr_tail="",
                    reason="lockfile not found: %s" % manifest_path,
                    environment={"manifest_path": manifest_path},
                )
            try:
                lock_bytes = lock_file.read_bytes()
                lockfile_hash = "sha256:" + hashlib.sha256(lock_bytes).hexdigest()
            except OSError as exc:
                return ExecEvidence(
                    status=ExecStatus.UNAVAILABLE,
                    command=self._explicit_command or [],
                    exit_code=None,
                    duration_s=time.monotonic() - t0,
                    stdout_tail="",
                    stderr_tail="",
                    reason="failed reading lockfile: %s" % exc,
                    environment={"manifest_path": manifest_path},
                )

        runtime_name = str(self._manifest.get("runtime_name", "")).lower()
        runtime_bin = self._manifest.get("runtime_bin", "")

        # Virtualenv resolution for Python
        if runtime_name == "python" and not runtime_bin and self._explicit_command is None:
            venv_candidates = [
                cwd / ".venv" / "bin" / "python",
                cwd / ".venv" / "bin" / "python3",
                cwd / "venv" / "bin" / "python",
                cwd / "venv" / "bin" / "python3",
            ]
            for candidate in venv_candidates:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    runtime_bin = str(candidate)
                    break
            else:
                return ExecEvidence(
                    status=ExecStatus.UNAVAILABLE,
                    command=[],
                    exit_code=None,
                    duration_s=time.monotonic() - t0,
                    stdout_tail="",
                    stderr_tail="",
                    reason="declared Python virtualenv not found in reviewed workspace",
                    environment={
                        "lockfile_hash": lockfile_hash,
                        "manifest_path": manifest_path or "",
                    },
                )

        # Probe raw version
        raw_version = ""
        probe_target = runtime_bin or (self._explicit_command[0] if self._explicit_command else "")
        if probe_target:
            try:
                probe_res = subprocess.run(
                    [probe_target, "--version"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=5.0,
                    env=_clean_subprocess_env(),
                )
                raw_version = (probe_res.stdout.strip() or probe_res.stderr.strip())
            except Exception:
                pass

        environment: dict[str, Any] = {
            "raw_version": raw_version,
            "lockfile_hash": lockfile_hash,
            "manifest_path": manifest_path or "",
        }

        # Command resolution
        cmd = self._resolve_command(runtime_name, runtime_bin)
        if cmd is None:
            return ExecEvidence(
                status=ExecStatus.UNAVAILABLE,
                command=[],
                exit_code=None,
                duration_s=time.monotonic() - t0,
                stdout_tail="",
                stderr_tail="",
                reason="no test command configured for runtime",
                environment=environment,
            )

        # Isolated execution tree: copy reviewed tree to tempdir to prevent tree mutation
        with tempfile.TemporaryDirectory(prefix="forge-exec-") as tmp_exec_dir:
            try:
                # Copy workspace files excluding .git and .code-forge
                shutil.copytree(
                    str(cwd),
                    tmp_exec_dir,
                    symlinks=True,
                    ignore=shutil.ignore_patterns(".git", ".code-forge"),
                    dirs_exist_ok=True,
                )
            except Exception as exc:
                return ExecEvidence(
                    status=ExecStatus.UNAVAILABLE,
                    command=cmd,
                    exit_code=None,
                    duration_s=time.monotonic() - t0,
                    stdout_tail="",
                    stderr_tail="",
                    reason="failed creating isolated execution workspace: %s" % exc,
                    environment=environment,
                )

            # Adjust cmd if it references a local venv inside cwd
            adjusted_cmd = list(cmd)
            if runtime_bin and str(cwd) in runtime_bin:
                rel = os.path.relpath(runtime_bin, str(cwd))
                target_bin = os.path.join(tmp_exec_dir, rel)
                if os.path.isfile(target_bin):
                    adjusted_cmd[0] = target_bin

            child_env = _clean_subprocess_env()

            with tempfile.TemporaryFile(mode="w+b") as stdout_f, tempfile.TemporaryFile(mode="w+b") as stderr_f:
                try:
                    p = subprocess.Popen(
                        adjusted_cmd,
                        cwd=tmp_exec_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_f,
                        stderr=stderr_f,
                        start_new_session=True,
                        env=child_env,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    return ExecEvidence(
                        status=ExecStatus.UNAVAILABLE,
                        command=cmd,
                        exit_code=None,
                        duration_s=time.monotonic() - t0,
                        stdout_tail="",
                        stderr_tail="",
                        reason="%s: %s" % (type(exc).__name__, exc),
                        environment=environment,
                    )

                try:
                    p.wait(timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(p)
                    stdout_tail = _read_file_tail(stdout_f, 2000)
                    stderr_tail = _read_file_tail(stderr_f, 2000)
                    return ExecEvidence(
                        status=ExecStatus.TIMEOUT,
                        command=cmd,
                        exit_code=None,
                        duration_s=time.monotonic() - t0,
                        stdout_tail=stdout_tail,
                        stderr_tail=stderr_tail,
                        reason="budget exhausted",
                        environment=environment,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    _kill_process_tree(p)
                    return ExecEvidence(
                        status=ExecStatus.UNAVAILABLE,
                        command=cmd,
                        exit_code=None,
                        duration_s=time.monotonic() - t0,
                        stdout_tail="",
                        stderr_tail="",
                        reason="%s: %s" % (type(exc).__name__, exc),
                        environment=environment,
                    )

                duration = time.monotonic() - t0
                stdout_tail = _read_file_tail(stdout_f, 2000)
                stderr_tail = _read_file_tail(stderr_f, 2000)
                ret = p.returncode

                if ret != 0:
                    return ExecEvidence(
                        status=ExecStatus.FAIL_BEFORE,
                        command=cmd,
                        exit_code=ret,
                        duration_s=duration,
                        stdout_tail=stdout_tail,
                        stderr_tail=stderr_tail,
                        reason="",
                        environment=environment,
                    )

                return ExecEvidence(
                    status=ExecStatus.PASS_AFTER,
                    command=cmd,
                    exit_code=0,
                    duration_s=duration,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    reason="",
                    environment=environment,
                )

    def _resolve_command(
        self, runtime_name: str, runtime_bin: str
    ) -> Optional[list[str]]:
        """Resolve command: explicit > gate.yaml test.command > runtime default."""
        if self._explicit_command:
            return list(self._explicit_command)

        # Runtime default for Python
        if runtime_name == "python":
            bin_path = runtime_bin or "python3"
            return [bin_path, "-m", "pytest"]

        return None

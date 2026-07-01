# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""MCP stdio server exposing forge review tools to IDE clients.

Six tools: forge_review, forge_gate_check, forge_init, forge_trust,
forge_resolve_outlet, forge_job_status. Runs as a local subprocess
of the IDE via stdio transport.

Workspace resolution: FORGE_PROJECT_DIR env var (primary), then cwd.
MCP processes typically run with cwd=~ so the env var is the reliable
path to reach nested repos like ~/code/forge.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from code_forge.mcp_jobs import (
    ForgeJobRef,
    ForgeResult,
    cleanup_all,
    exit_to_verdict,
    get_job,
    start_job,
)

# Module-level state populated by lifespan startup.
_backend_names: list[str] = []


def _resolve_project_dir() -> Path:
    """Resolve the forge project directory.

    Priority: FORGE_PROJECT_DIR env var > cwd.
    Validates that .code-forge/gate.yaml exists at the resolved path.
    Raises ToolError with remediation guidance on failure.
    """
    env_dir = os.environ.get("FORGE_PROJECT_DIR")
    if env_dir:
        project = Path(env_dir)
        if not (project / ".code-forge" / "gate.yaml").exists():
            raise ToolError(
                "FORGE_PROJECT_DIR points to %s but no .code-forge/gate.yaml "
                "found. Run 'code-forge init' in that directory." % project
            )
        return project

    cwd = Path.cwd()
    if (cwd / ".code-forge" / "gate.yaml").exists():
        return cwd

    raise ToolError(
        "Cannot determine project directory. Set FORGE_PROJECT_DIR or run "
        "the MCP server from a directory containing .code-forge/gate.yaml."
    )


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Load backend names at startup, clean up subprocesses on shutdown."""
    global _backend_names  # noqa: PLW0603

    try:
        project_dir = _resolve_project_dir()
        gate_yaml_path = project_dir / ".code-forge" / "gate.yaml"
        from code_forge import cli

        _, gate_data = cli._load_gate_backends(gate_yaml_path)
        _backend_names = list(gate_data.get("backends", {}).keys())
    except Exception:
        _backend_names = []

    yield {"backend_names": _backend_names}

    await cleanup_all()


mcp = FastMCP(
    "code-forge-mcp",
    instructions=(
        "Forge code review tools. The server expects cwd = workspace root "
        "(the directory containing .code-forge/). "
        "Use forge_review to review git diffs, "
        "forge_gate_check for pre-commit gating, forge_resolve_outlet to "
        "diagnose backend configuration."
    ),
    lifespan=lifespan,
)


# -- pre-flight helper --


def _check_backend() -> Path:
    """Verify a trusted review backend is configured. Returns project dir.

    Uses _resolve_project_dir() for workspace resolution, then loads
    via _load_gate_backends. Does NOT call resolve_outlet (avoids HTTP
    probe latency).
    """
    project_dir = _resolve_project_dir()
    gate_yaml_path = project_dir / ".code-forge" / "gate.yaml"
    from code_forge import cli
    from code_forge.errors import CliError

    try:
        backend_configs, _ = cli._load_gate_backends(gate_yaml_path)
        if not backend_configs:
            raise ToolError(
                "No trusted review backend configured. "
                "Run 'code-forge trust' then restart the MCP server."
            )
    except (CliError, ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc
    return project_dir


# -- CLI runner helpers --


async def _run_cli_simple(
    *args: str, cwd: Path | None = None,
) -> tuple[str, str, int]:
    """Run a CLI command and return (stdout, stderr, exit_code)."""
    proc = await asyncio.create_subprocess_exec(
        "code-forge",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return (
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
        proc.returncode or 0,
    )


async def _run_cli_budgeted(
    *args: str,
    budget: float = 20.0,
    cwd: Path | None = None,
) -> tuple[str, int, float] | tuple[asyncio.Task[Any], asyncio.subprocess.Process]:
    """Run CLI with a time budget. Returns inline result or (task, proc) on timeout.

    On timeout the inner_task (wrapping proc.communicate()) survives via
    asyncio.shield -- pass it to start_job for background completion.
    """
    proc = await asyncio.create_subprocess_exec(
        "code-forge",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    start = time.monotonic()
    inner_task = asyncio.create_task(proc.communicate())
    try:
        stdout_bytes, _ = await asyncio.wait_for(
            asyncio.shield(inner_task), timeout=budget
        )
        elapsed = time.monotonic() - start
        return (stdout_bytes.decode(errors="replace"), proc.returncode or 0, elapsed)
    except asyncio.TimeoutError:
        return (inner_task, proc)
    except asyncio.CancelledError:
        proc.kill()
        inner_task.cancel()
        raise


# -- result formatting --


def _make_result(stdout: str, exit_code: int, elapsed: float) -> CallToolResult:
    """Build dual-layer CallToolResult for completed review/gate-check."""
    structured = ForgeResult(
        verdict=exit_to_verdict(exit_code),
        exit_code=exit_code,
        findings_count=None,
        duration_s=round(elapsed, 2),
        output=stdout,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=stdout)],
        structuredContent=structured.model_dump(),
    )


def _make_simple_result(stdout: str, exit_code: int) -> CallToolResult:
    """Build CallToolResult for simple CLI commands (init, trust, etc.)."""
    return CallToolResult(
        content=[TextContent(type="text", text=stdout)],
        structuredContent={"exit_code": exit_code, "output": stdout},
    )


def _make_job_ref(job_id: str) -> CallToolResult:
    """Build CallToolResult for a background job reference."""
    ref = ForgeJobRef(
        job_id=job_id, status="running", poll_after_seconds=10, result=None
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    "Review running in background. "
                    "Poll with forge_job_status(job_id='%s')." % job_id
                ),
            )
        ],
        structuredContent=ref.model_dump(),
    )


def _validate_backend(backend: str | None) -> None:
    """Raise ToolError if backend name is not in the loaded list."""
    if backend and _backend_names and backend not in _backend_names:
        raise ToolError(
            "Unknown backend '%s'. Available: %s"
            % (backend, ", ".join(_backend_names))
        )


# -- tool handlers --


@mcp.tool(
    name="forge_review",
    description=(
        "Run the forge review pipeline on the current git diff. "
        "Long-running: returns inline if <20s, otherwise returns job_id for "
        "polling via forge_job_status."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def forge_review(
    backend: str | None = None,
    contract: str | None = None,
    committed: bool = False,
    whole_file: bool = False,
    canary: bool = False,
) -> CallToolResult:
    """Run forge review pipeline."""
    project_dir = _check_backend()
    _validate_backend(backend)

    cli_args: list[str] = ["review"]
    if backend:
        cli_args.extend(["--backend", backend])
    if committed:
        cli_args.append("--committed")
    if whole_file:
        cli_args.append("--whole-file")
    if canary:
        cli_args.append("--canary")

    tmp_path: str | None = None
    if contract:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        tmp.write(contract)
        tmp.close()
        tmp_path = tmp.name
        cli_args.extend(["--contract", tmp_path])

    result = await _run_cli_budgeted(*cli_args, cwd=project_dir)

    if isinstance(result[0], str):
        # Inline completion
        stdout, exit_code, elapsed = result  # type: ignore[misc]
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
        return _make_result(stdout, exit_code, elapsed)
    else:
        # Timeout -- transfer tempfile ownership to job
        inner_task, proc = result  # type: ignore[misc]
        try:
            job_id = start_job(inner_task, proc, tempfile_path=tmp_path)
        except Exception:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
            raise
        return _make_job_ref(job_id)


@mcp.tool(
    name="forge_gate_check",
    description="Run pre-commit gate check on staged changes.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def forge_gate_check(
    baseline: str | None = None,
    backend: str | None = None,
) -> CallToolResult:
    """Run forge gate-check pipeline."""
    project_dir = _check_backend()
    _validate_backend(backend)

    cli_args: list[str] = ["gate-check"]
    if baseline:
        cli_args.extend(["--baseline", baseline])
    if backend:
        cli_args.extend(["--backend", backend])

    result = await _run_cli_budgeted(*cli_args, cwd=project_dir)

    if isinstance(result[0], str):
        stdout, exit_code, elapsed = result  # type: ignore[misc]
        return _make_result(stdout, exit_code, elapsed)
    else:
        inner_task, proc = result  # type: ignore[misc]
        job_id = start_job(inner_task, proc)
        return _make_job_ref(job_id)


@mcp.tool(
    name="forge_init",
    description="Initialize .code-forge/ directory in the current workspace.",
    annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True),
)
async def forge_init(force: bool = False) -> CallToolResult:
    """Initialize forge configuration."""
    cli_args: list[str] = ["init"]
    if force:
        cli_args.append("--force")
    # init uses FORGE_PROJECT_DIR if set, else cwd (which is fine for init)
    env_dir = os.environ.get("FORGE_PROJECT_DIR")
    cwd = Path(env_dir) if env_dir else None
    stdout, _, exit_code = await _run_cli_simple(*cli_args, cwd=cwd)
    return _make_simple_result(stdout, exit_code)


@mcp.tool(
    name="forge_trust",
    description="Trust the gate.yaml backends in the current workspace.",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def forge_trust() -> CallToolResult:
    """Trust forge backends."""
    project_dir = _resolve_project_dir()
    stdout, _, exit_code = await _run_cli_simple("trust", cwd=project_dir)
    return _make_simple_result(stdout, exit_code)


@mcp.tool(
    name="forge_resolve_outlet",
    description=(
        "Diagnose which review backend and outlet forge will use. Read-only."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
async def forge_resolve_outlet() -> CallToolResult:
    """Diagnose backend routing with API key provenance."""
    project_dir = _resolve_project_dir()
    stdout, stderr, exit_code = await _run_cli_simple(
        "resolve-outlet", cwd=project_dir,
    )
    # MCP-06: append API key provenance diagnostic
    diag_lines: list[str] = []
    gate_yaml_path = project_dir / ".code-forge" / "gate.yaml"
    if gate_yaml_path.exists():
        try:
            from code_forge import cli
            _, gate_data = cli._load_gate_backends(gate_yaml_path)
            for bname, bcfg in gate_data.get("backends", {}).items():
                if not isinstance(bcfg, dict):
                    continue
                key_env = bcfg.get("api_key_env")
                if key_env:
                    is_set = key_env in os.environ
                    diag_lines.append(
                        "  %s: API key: %s (%s)"
                        % (bname, key_env, "set" if is_set else "NOT SET")
                    )
        except Exception:
            pass
    if diag_lines:
        stdout += "\nAPI key status:\n" + "\n".join(diag_lines) + "\n"
    return _make_simple_result(stdout, exit_code)


@mcp.tool(
    name="forge_job_status",
    description=(
        "Poll a long-running forge review job. "
        "Returns current status and result when complete."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def forge_job_status(job_id: str) -> CallToolResult:
    """Poll job status."""
    entry = get_job(job_id)
    if entry is None:
        raise ToolError("Unknown job_id: %s" % job_id)

    status = entry["status"]
    forge_result: ForgeResult | None = None

    if status in ("completed", "failed") and entry.get("result"):
        r = entry["result"]
        forge_result = ForgeResult(
            verdict=r.get("verdict", "UNKNOWN(-1)"),
            exit_code=r.get("exit_code", -1),
            findings_count=None,
            duration_s=0.0,
            output=r.get("stdout", ""),
        )

    ref = ForgeJobRef(
        job_id=job_id,
        status=status,
        poll_after_seconds=10 if status == "running" else None,
        result=forge_result,
    )

    if forge_result:
        text = "Job %s %s: %s (exit %d)" % (
            job_id,
            status,
            forge_result.verdict,
            forge_result.exit_code,
        )
    else:
        text = "Job %s: %s" % (job_id, status)

    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=ref.model_dump(),
    )


# -- entry point --


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

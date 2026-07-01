# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""MCP stdio server exposing forge review tools to IDE clients.

Six tools: forge_review, forge_gate_check, forge_init, forge_trust,
forge_resolve_outlet, forge_job_status. Runs as a local subprocess
of the IDE via stdio transport.

Workspace resolution (ADR-0006): the server locates the project root
via FORGE_PROJECT_DIR env var, then by walking up from cwd to find
.code-forge/gate.yaml, then falls back to cwd as-is. The resolved
root is passed as cwd to all CLI subprocesses; cli.py is unchanged.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp.server.fastmcp import Context, FastMCP
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

# -- workspace resolution (ADR-0006) --


def _resolve_workspace() -> Path:
    """Forge workspace root: explicit env, else walk up from cwd.

    Priority: FORGE_PROJECT_DIR > nearest ancestor with
    .code-forge/gate.yaml > cwd as-is (lets _check_backend emit the
    clear error).
    """
    env = os.environ.get("FORGE_PROJECT_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    start = Path.cwd().resolve()
    for d in (start, *start.parents):
        if (d / ".code-forge" / "gate.yaml").is_file():
            return d
    return start


_WORKSPACE: Path = _resolve_workspace()

# Module-level state populated by lifespan startup.
_backend_names: list[str] = []


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Load backend names at startup, clean up subprocesses on shutdown."""
    global _backend_names  # noqa: PLW0603

    from code_forge import cli

    try:
        gate_yaml_path = _WORKSPACE / ".code-forge" / "gate.yaml"
        _, gate_data = cli._load_gate_backends(gate_yaml_path)
        _backend_names = list(gate_data.get("backends", {}).keys())
    except Exception:
        _backend_names = []

    yield {"backend_names": _backend_names}

    await cleanup_all()


mcp = FastMCP(
    "code-forge-mcp",
    instructions=(
        "Forge code review tools. The server auto-detects the project "
        "root by walking up from cwd to find .code-forge/gate.yaml, or "
        "via the FORGE_PROJECT_DIR env var. "
        "Use forge_review to review git diffs, "
        "forge_gate_check for pre-commit gating, forge_resolve_outlet to "
        "diagnose backend configuration."
    ),
    lifespan=lifespan,
)


# -- pre-flight helper --


def _check_backend() -> None:
    """Verify a trusted review backend is configured.

    Checks gate.yaml existence then loads via _load_gate_backends.
    Does NOT call resolve_outlet (avoids HTTP probe latency).
    """
    gate_yaml_path = _WORKSPACE / ".code-forge" / "gate.yaml"
    if not gate_yaml_path.exists():
        raise ToolError(
            "gate.yaml not found at %s. Run 'code-forge init'." % gate_yaml_path
        )
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


# -- CLI runner helpers --


async def _run_cli_simple(*args: str) -> tuple[str, str, int]:
    """Run a CLI command and return (stdout, stderr, exit_code)."""
    proc = await asyncio.create_subprocess_exec(
        "code-forge",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_WORKSPACE),
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
        cwd=str(_WORKSPACE),
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


def _make_simple_result(
    stdout: str, exit_code: int, stderr: str = "",
) -> CallToolResult:
    """Build CallToolResult for simple CLI commands (init, trust, etc.)."""
    text = stdout
    if stderr.strip():
        text = stdout + "\n--- stderr ---\n" + stderr
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={"exit_code": exit_code, "output": text},
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


def _build_review_context(
    cwd: Path, committed: bool, staged: bool = False,
) -> tuple["ResolvedReview", str, str]:
    """Build review context for in-process sampling path.

    Returns (resolved, source_hash, baseline_repr).
    Equivalent to cli.py:1697-1724 but without argparse args object.
    """
    from code_forge.baseline import GitRefBaseline, resolve_baseline, serialize_baseline_spec
    from code_forge.source import compute_source_hash

    if committed:
        baseline_spec = GitRefBaseline("HEAD~1")
        head_spec = GitRefBaseline("HEAD")
    else:
        if staged:
            # INDEX = staged changes only (forge_gate_check path)
            head_spec = GitRefBaseline("INDEX")
        else:
            # WORKING = unstaged working tree changes (forge_review default)
            # cli.py:2387 defaults to WORKING, not INDEX
            head_spec = GitRefBaseline("WORKING")
        baseline_spec = GitRefBaseline("HEAD")

    resolved = resolve_baseline(baseline_spec, head_spec, [], cwd)
    # Note: cli.py:1716-1723 branches on mode_hint (git vs non-git).
    # MCP sampling always uses committed/staged (git context), so git_diff
    # path is correct here. Non-git workspaces would need files= path.
    source_hash = compute_source_hash(git_diff=resolved.git_diff or "")
    baseline_repr = serialize_baseline_spec(baseline_spec)
    return resolved, source_hash, baseline_repr


def _make_inprocess_result(
    verdict: "Verdict", findings_count: int, elapsed: float
) -> CallToolResult:
    """Convert in-process Verdict to CallToolResult.

    Maps Verdict enum to exit code: PASS->0, FAIL->1, ESCALATED->3, else->1.
    """
    from code_forge.state import Verdict
    # Reverse of _EXIT_TO_VERDICT (mcp_jobs.py:26-35)
    exit_map = {
        Verdict.PASS: 0,
        Verdict.FAIL: 1,
        Verdict.ESCALATED: 4,
        Verdict.PENDING: 3,  # BUSY -- review incomplete, not FAIL
        Verdict.DELEGATED: 5,
        Verdict.UNRELIABLE: 7,
    }
    exit_code = exit_map.get(verdict, 1)
    summary = "forge: %s (%d findings, %.1fs)" % (verdict.value, findings_count, elapsed)
    structured = ForgeResult(
        verdict=verdict.value,
        exit_code=exit_code,
        findings_count=findings_count,
        duration_s=round(elapsed, 2),
        output=summary,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structuredContent=structured.model_dump(),
    )


async def _dispatch_sampling(
    session,              # ServerSession
    committed: bool,
    backend_name: str | None = None,
    staged: bool = False,  # True for gate-check (INDEX), False for review (WORKING)
) -> CallToolResult:
    """Run forge review in-process via MCP sampling transport.

    Builds review context, constructs StateMachine with sampling l1_provider,
    runs machine.run() in a worker thread. On truncation, falls back to
    CLI subprocess if a backend is available.
    """
    from code_forge.factories import (
        build_autofixer,
        build_e2e_checker,
        build_falsifier,
        build_l2_runner,
        build_revert_fn,
        build_sampling_l1_provider,
    )
    from code_forge.llm_invoke import LLMInvokeError
    from code_forge.machine import Mode, StateMachine

    resolved, source_hash, baseline_repr = _build_review_context(_WORKSPACE, committed, staged=staged)

    # capture event loop BEFORE dispatching to worker thread
    loop = asyncio.get_running_loop()

    l1_provider = build_sampling_l1_provider(
        session=session,
        loop=loop,
        resolved=resolved,
    )

    # ponytail: Phase 1 limitations for sampling path:
    # - falsifier="stub" (not "auto") to avoid silently calling claude -p
    # - registry={} -- no L0 tools (semgrep, shellcheck) run in sampling mode
    # - source_files=[] from paths=[] -- L0 would see no files anyway
    # Phase 2 wires sampling session into falsifier + populates registry.
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=build_falsifier("stub"),
        autofixer=build_autofixer(resolved),
        revert_fn=build_revert_fn(resolved, _WORKSPACE),
        resolved_review=resolved,
        source_hash=source_hash,
        baseline_spec_repr=baseline_repr,
        cwd=_WORKSPACE,
        registry={},
        l1_provider=l1_provider,
        l2_runner=build_l2_runner(),
        e2e_runner=build_e2e_checker(),
    )

    from code_forge.lock import ForgeLock
    lock_path = _WORKSPACE / ".code-forge" / "code-forge.lock"  # must match cli.py:1653

    # Lock acquisition + machine.run both inside worker thread to avoid
    # blocking the MCP server event loop on lock contention.
    def _run_locked():
        with ForgeLock(lock_path):
            return machine.run()

    t0 = time.monotonic()
    try:
        verdict = await asyncio.to_thread(_run_locked)
    except LLMInvokeError as exc:
        # _backend_names: module-level list populated in lifespan() from gate.yaml backends
        if "truncated" in str(exc) and (backend_name or _backend_names):
            # truncation fallback to CLI backend
            # MUST force --outlet subprocess to prevent infinite loop when
            # gate.yaml has outlet: sampling (subprocess reads gate.yaml too)
            fallback_backend = backend_name or _backend_names[0]
            # Fallback only supports "review" command (gate-check parser
            # doesn't accept --backend/--outlet). If staged (gate-check),
            # raise clear error instead of broken CLI call.
            if staged:
                raise ToolError(
                    "Sampling truncated during gate-check. "
                    "Configure an API backend for gate-check fallback."
                )
            cli_args = ["review", "--no-color", "--backend", fallback_backend,
                        "--outlet", "subprocess"]
            if committed:
                cli_args.append("--committed")
            result = await _run_cli_budgeted(*cli_args)
            if isinstance(result[0], str):
                stdout, exit_code, elapsed = result
                return _make_result(stdout, exit_code, elapsed)
            else:
                inner_task, proc = result
                job_id = start_job(inner_task, proc)
                return _make_job_ref(job_id)
        else:
            raise ToolError("Sampling failed: %s" % exc)

    elapsed = time.monotonic() - t0
    # Findings count is omitted for now (not tracked reliably by StateMachine.run)
    return _make_inprocess_result(verdict, findings_count=0, elapsed=elapsed)


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
    ctx: Context = None,
) -> CallToolResult:
    """Run forge review pipeline."""
    from code_forge.outlet_resolver import load_outlet_from_gate
    
    outlet = os.environ.get("FORGE_OUTLET")
    if not outlet:
        gate_yaml_path = _WORKSPACE / ".code-forge" / "gate.yaml"
        if gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)
            
    if outlet == "sampling":
        if ctx is None or ctx.session.client_params.capabilities.sampling is None:
            raise ToolError("Client does not support sampling capability.")
        return await _dispatch_sampling(
            session=ctx.session,
            committed=committed,
            backend_name=backend,
            staged=False,
        )

    _check_backend()
    _validate_backend(backend)

    cli_args: list[str] = ["review", "--no-color"]
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

    result = await _run_cli_budgeted(*cli_args)

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
    ctx: Context = None,
) -> CallToolResult:
    """Run forge gate-check pipeline."""
    from code_forge.outlet_resolver import load_outlet_from_gate

    outlet = os.environ.get("FORGE_OUTLET")
    if not outlet:
        gate_yaml_path = _WORKSPACE / ".code-forge" / "gate.yaml"
        if gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)

    if outlet == "sampling":
        if ctx is None or ctx.session.client_params.capabilities.sampling is None:
            raise ToolError("Client does not support sampling capability.")
        return await _dispatch_sampling(
            session=ctx.session,
            committed=False,
            backend_name=backend,
            staged=True,
        )

    _check_backend()
    _validate_backend(backend)

    cli_args: list[str] = ["gate-check", "--no-color"]
    if baseline:
        cli_args.extend(["--baseline", baseline])
    if backend:
        cli_args.extend(["--backend", backend])

    result = await _run_cli_budgeted(*cli_args)

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
    stdout, stderr, exit_code = await _run_cli_simple(*cli_args)
    return _make_simple_result(stdout, exit_code, stderr)


@mcp.tool(
    name="forge_trust",
    description="Trust the gate.yaml backends in the current workspace.",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def forge_trust() -> CallToolResult:
    """Trust forge backends."""
    stdout, stderr, exit_code = await _run_cli_simple("trust")
    return _make_simple_result(stdout, exit_code, stderr)


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
    """Diagnose backend routing."""
    stdout, stderr, exit_code = await _run_cli_simple("resolve-outlet")
    return _make_simple_result(stdout, exit_code, stderr)


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

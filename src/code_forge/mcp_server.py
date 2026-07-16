# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""MCP stdio server exposing forge review tools to IDE clients.

Six tools: forge_review, forge_gate_check, forge_init, forge_trust,
forge_resolve_outlet, forge_job_status. Runs as a local subprocess
of the IDE via stdio transport.

Workspace resolution (ADR-0006, ADR-0009): the server locates the
project root via FORGE_PROJECT_DIR env var, then by walking up from
cwd to find .code-forge/gate.yaml (skipping $HOME to prevent stale
markers from acting as walkup magnets), then falls back to cwd as-is.
User-level backend defaults live at ~/.config/code-forge/config.yaml
(XDG) and merge under project-level backends.  The resolved root is
passed as cwd to all CLI subprocesses; cli.py is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from code_forge.mcp_jobs import (
    ForgeJobRef,
    ForgeResult,
    _terminate_and_reap,
    cleanup_all,
    exit_to_verdict,
    get_job,
    snapshot_tempfile_paths,
    start_job,
)
from code_forge.llm_invoke import effective_invoke_timeout_s

log = logging.getLogger(__name__)

# -- signal-driven shutdown for the stdio server --
# The CLI signal handler (llm_invoke._install_signal_handlers) raises
# KeyboardInterrupt on SIGTERM, which the asyncio runner swallows.
# The server installs its own handler via loop.add_signal_handler
# (last-install-wins), giving it a real exit path through cleanup_all.

_shutting_down = False


def _install_pdeathsig() -> None:
    """Ask the kernel to SIGTERM this process when its parent dies.

    Covers the orphan leak where the IDE/session restarts without
    sending SIGTERM or closing the stdio socket.  The delivered
    SIGTERM is then handled by the lifespan signal handler
    or, if that has not been installed yet, by the default SIGTERM
    disposition (process terminates -- no cleanup, but no orphan).

    Linux-only (prctl PR_SET_PDEATHSIG).  Other platforms are
    unguarded today; document the gap rather than fake a fix.

    PID namespace caveat: inside Docker/K8s the parent may
    itself be PID 1.  The startup-race check compares ppid before
    and after prctl rather than hardcoding ppid==1, so it works
    regardless of whether PID 1 is init or a container entrypoint.
    """
    if sys.platform != "linux":
        return
    import ctypes
    import ctypes.util

    original_ppid = os.getppid()

    PR_SET_PDEATHSIG = 1
    # find_library returns None on musl (no ldconfig); fall back to
    # glibc soname then the bare "libc.so" symlink musl provides.
    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        for name in ("libc.so.6", "libc.so"):
            try:
                ctypes.CDLL(name, use_errno=True)
                libc_name = name
                break
            except OSError:
                continue
        if libc_name is None:
            log.warning("PR_SET_PDEATHSIG unavailable: cannot find libc")
            return
    try:
        libc = ctypes.CDLL(libc_name, use_errno=True)
        rc = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        if rc != 0:
            errno = ctypes.get_errno()
            log.warning(
                "prctl(PR_SET_PDEATHSIG) failed: rc=%d errno=%d", rc, errno
            )
    except Exception as exc:
        log.warning("PR_SET_PDEATHSIG unavailable: %s", exc)

    # Startup race: if the parent died between fork and prctl, the
    # signal will never arrive.  Compare ppid before/after rather
    # than hardcoding ==1, so this works inside PID namespaces
    # where the parent may itself be PID 1.
    if os.getppid() != original_ppid:
        log.warning(
            "Parent changed during startup (was %d, now %d), exiting",
            original_ppid, os.getppid(),
        )
        os._exit(1)


def _schedule_shutdown(signum: int, loop: asyncio.AbstractEventLoop) -> None:
    """Schedule graceful shutdown on SIGTERM/SIGINT. Plain def for add_signal_handler."""
    global _shutting_down  # noqa: PLW0603
    if _shutting_down:
        return
    _shutting_down = True
    loop.create_task(_do_shutdown(signum))


async def _do_shutdown(signum: int) -> None:
    """Run cleanup, unlink tempfiles, then hard-exit.

    Tempfile paths are snapshotted BEFORE cleanup_all() because it
    clears _jobs, orphaning entries before _wait_for_job can fire
    its own finally-block unlink.  The snapshot+unlink here is the
    backup path; double-unlink is harmless (OSError caught).
    """
    paths = snapshot_tempfile_paths()
    try:
        await cleanup_all()
    except Exception:
        log.exception("cleanup_all failed during shutdown")
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except OSError:
            pass
        os._exit(128 + signum)


# -- workspace resolution (ADR-0006) --

from code_forge.workspace import SAMPLING_REMEDIATION, resolve_workspace  # noqa: E402


def _resolve_workspace() -> Path:
    """Thin wrapper: delegates to the shared, MCP-free resolver."""
    return resolve_workspace(Path.cwd(), os.environ)


# User-level config shared with cli.py via user_config module.
from code_forge.user_config import load_user_backends, merge_backends  # noqa: E402

if TYPE_CHECKING:
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict


# -- per-session workspace cache (single-slot, one session per stdio) --
_cached_session_ref = None   # session object, identity-compared
_cached_workspace = None     # resolved Path


async def _workspace_for(ctx, project_dir: str = "") -> Path:
    """Resolve workspace from MCP roots, env, or walk-up.

    Priority: project_dir (explicit per-call) > cached > MCP roots
    (prefer root with gate.yaml) > FORGE_PROJECT_DIR > walk-up > cwd.

    project_dir default is "" (not None) for MCP schema compatibility:
    Pydantic's str|None generates anyOf without a top-level "type",
    causing Claude Code's tool inspector to show "unknown". Empty string
    is falsy, so the truthy guard below is branch-neutral with the old
    `is not None` check for all callers.
    """
    global _cached_session_ref, _cached_workspace

    if project_dir:
        return Path(project_dir).expanduser().resolve()

    if ctx is None:
        return _resolve_workspace()

    if _cached_session_ref is ctx.session:
        return _cached_workspace

    # Try MCP roots when the client advertises the capability.
    if ctx.session.client_params.capabilities.roots:
        try:
            result = await ctx.session.list_roots()
        except Exception as exc:
            sys.stderr.write(
                "code-forge: list_roots failed: %s\n" % exc)
            # Do not cache after RPC failure -- let the next call
            # retry instead of pinning a wrong workspace.
            return _resolve_workspace()

        if result.roots:
            from urllib.parse import unquote, urlparse

            candidates = []
            for root in result.roots:
                p = Path(unquote(urlparse(str(root.uri)).path))
                if (p / ".code-forge" / "gate.yaml").is_file():
                    _cached_session_ref = ctx.session
                    _cached_workspace = p
                    return p
                candidates.append(p)
            if candidates:
                _cached_session_ref = ctx.session
                _cached_workspace = candidates[0]
                return candidates[0]

    # No roots capability or empty roots -- cache the static result.
    ws = _resolve_workspace()
    _cached_session_ref = ctx.session
    _cached_workspace = ws
    return ws


def _backend_names_for(workspace: Path) -> list[str]:
    """Merge project + user backend names for a workspace."""
    from code_forge import cli

    user_backends = load_user_backends()
    project_backends: dict[str, dict] = {}
    try:
        gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
        _, gate_data = cli._load_gate_backends(gate_yaml_path)
        project_backends = gate_data.get("backends", {})
        if not isinstance(project_backends, dict):
            project_backends = {}
    except Exception:
        pass
    return list(merge_backends(project_backends, user_backends).keys())


def _job_cap_s(workspace: Path, backend_name: str = "") -> float:
    """Compute the wall-clock cap for a background MCP job.

    Returns effective_invoke_timeout_s(backend) + 600s grace.
    When FORGE_MCP_JOB_TIMEOUT_S is set and positive, it wins.
    Falls back to derived value on junk env.
    """
    # Env override takes priority
    env_raw = os.environ.get("FORGE_MCP_JOB_TIMEOUT_S")
    if env_raw is not None:
        try:
            env_val = int(env_raw)
            if env_val > 0:
                return float(env_val)
            log.warning(
                "FORGE_MCP_JOB_TIMEOUT_S=%r is not positive; "
                "falling back to derived cap",
                env_raw,
            )
        except ValueError:
            log.warning(
                "FORGE_MCP_JOB_TIMEOUT_S=%r is not an int; "
                "falling back to derived cap",
                env_raw,
            )

    # Resolve the BackendConfig to get the effective invoke timeout.
    # Lazy import avoids circular dependency (mcp_server <-> cli).
    # Note: _load_gate_backends does sync file I/O (reading gate.yaml).
    # This is acceptable because _job_cap_s is called only on the rare
    # timeout-cap path, not on every request.
    from code_forge import cli as _cli
    from code_forge.backend import (
        DEFAULT_BACKEND as _DEFAULT,
        load_backend_configs as _load,
        resolve_backend as _resolve,
    )

    try:
        gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
        _, gate_data = _cli._load_gate_backends(gate_yaml_path)
        configs = _load(gate_data)
        backend = _resolve(
            os.environ, configs,
            cli_value=backend_name or None,
        )
    except Exception:
        log.warning(
            "backend resolution failed; falling back to default "
            "CLI backend (timeout_s=%d)",
            effective_invoke_timeout_s(_DEFAULT),
            exc_info=True,
        )
        backend = _DEFAULT

    return float(effective_invoke_timeout_s(backend) + 600)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Load backend names at startup, clean up subprocesses on shutdown.

    Backend merge order: project-level gate.yaml first, then
    user-level defaults append for names not already defined.
    Project backends lead so fallback ([0]) picks a CLI-resolvable
    backend.  Only backend names are tracked here; the actual
    BackendConfig loading happens per-review in _check_backend.
    """
    _install_pdeathsig()

    startup_ws = _resolve_workspace()
    log.info("startup workspace: %s", startup_ws)

    # Install signal handlers that actually terminate the server.
    # Windows event loops raise NotImplementedError here: SIGTERM on
    # Windows is TerminateProcess (no handler can run), and Ctrl+C
    # reaches asyncio.run as KeyboardInterrupt without our help.
    # stdio EOF still exits the lifespan, so cleanup_all() runs on
    # every orderly shutdown; only the kill-without-EOF path loses
    # cleanup, same gap _install_pdeathsig documents for non-Linux.
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(
            signal.SIGTERM,
            lambda: _schedule_shutdown(signal.SIGTERM, loop))
        loop.add_signal_handler(
            signal.SIGINT,
            lambda: _schedule_shutdown(signal.SIGINT, loop))
    except NotImplementedError:
        log.info(
            "loop.add_signal_handler unsupported on this platform; "
            "relying on stdio EOF for shutdown")

    yield {}

    await cleanup_all()


mcp = FastMCP(
    "code-forge-mcp",
    instructions=(
        "Forge code review tools. The server auto-detects the project "
        "root via FORGE_PROJECT_DIR env var, or by walking up from cwd "
        "to find .code-forge/gate.yaml (skipping $HOME). User-level "
        "backend defaults in ~/.config/code-forge/config.yaml merge "
        "under project backends. "
        "Use forge_review to review git diffs, "
        "forge_gate_check for pre-commit gating, forge_resolve_outlet to "
        "diagnose backend configuration."
    ),
    lifespan=lifespan,
)

# Null-coercion fallback: MCP clients may send null for optional string
# params. Pydantic's str type rejects null, but our schema uses str=""
# for display cleanliness. Coerce None -> "" before validation.
#
# TECHNICAL DEBT: uses private mcp._tool_manager.call_tool because SDK
# FastMCP exposes no middleware/call-interceptor hook. Coerces all None
# values (not just str-typed params) -- safe because Pydantic rejects ""
# for bool/int exactly as it rejects None (ValidationError either way;
# no observable behavior change). Mitigations: pin mcp<2 in
# pyproject.toml; test_null_coercion_* tests act as a tripwire if a
# future mcp 1.x renames _tool_manager (import-time crash, suite RED).
# Upstream FR for middleware support would let us drop this entirely.
_original_tc = mcp._tool_manager.call_tool


async def _null_coerce_call_tool(name, arguments, **kw):
    for k, v in list(arguments.items()):
        if v is None:
            arguments[k] = ""
    return await _original_tc(name, arguments, **kw)


mcp._tool_manager.call_tool = _null_coerce_call_tool


# -- pre-flight helper --


def _check_backend(workspace: Path) -> None:
    """Verify a trusted review backend is configured.

    Checks gate.yaml existence then loads via _load_gate_backends.
    Does NOT call resolve_outlet (avoids HTTP probe latency).
    """
    gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
    if not gate_yaml_path.exists():
        raise ToolError(
            "gate.yaml not found at %s. Run 'code-forge init'." % gate_yaml_path
        )
    from code_forge import cli
    from code_forge.errors import CliError

    backend_configs: list = []  # list[BackendConfig] after _load_gate_backends
    try:
        backend_configs, gate_data = cli._load_gate_backends(gate_yaml_path)
        backend_configs = cli._merge_user_into(backend_configs, gate_data)
        if not backend_configs:
            raise ToolError(
                "No review backends configured in %s. Add backends to "
                "user-level config (~/.config/code-forge/config.yaml) "
                "or project gate.yaml, or set 'outlet: sampling' to "
                "review with the IDE's own model. "
                "(workspace: %s -- wrong project? set "
                "FORGE_PROJECT_DIR in the MCP server env)"
                % (gate_yaml_path, workspace)
            )
    except (CliError, ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc

    # Key env check: only block if ZERO backends have valid keys.
    # With user-level backends, a user may configure 5 but only have
    # keys for 2 -- blocking all reviews for missing keys on unused
    # backends is unnecessarily strict.
    available = [
        cfg for cfg in backend_configs
        if not cfg.api_key_env or os.environ.get(cfg.api_key_env)
    ]
    missing_pairs = sorted(
        set(
            (cfg.name, cfg.api_key_env)
            for cfg in backend_configs
            if cfg.api_key_env and not os.environ.get(cfg.api_key_env)
        )
    )
    detail = (", ".join("%s: %s" % (n, k) for n, k in missing_pairs)
              if missing_pairs else "")
    if missing_pairs:
        log.warning("Backends with missing API keys (unavailable): %s",
                    detail)
    if not available:
        raise ToolError(
            "API key env var(s) not set in the MCP server process: "
            "%s. Set them in the MCP server config env block (or the "
            "wrapper script), then restart the MCP server."
            % detail
        )


# -- CLI runner helpers --


async def _run_cli_simple(*args: str, workspace: Path) -> tuple[str, str, int]:
    """Run a CLI command and return (stdout, stderr, exit_code)."""
    proc = await asyncio.create_subprocess_exec(
        "code-forge",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace),
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return (
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
        proc.returncode or 0,
    )



async def _kill_and_reap(
    proc: asyncio.subprocess.Process,
    task: asyncio.Task,
) -> None:
    """Best-effort subprocess cleanup.  Never raises."""
    task.cancel()
    await _terminate_and_reap(proc)


async def _run_cli_budgeted(
    *args: str,
    workspace: Path,
    budget: float = 20.0,
    env: dict[str, str] | None = None,
) -> (
    tuple[str, int, float, str]
    | tuple[asyncio.Task[Any], asyncio.subprocess.Process, str]
):
    """Run CLI with a time budget.

    Args:
        *args: CLI arguments to pass to code-forge.
        workspace: Working directory for the subprocess.
        budget: Maximum wall-clock seconds before timeout.
        env: Optional environment dict for the subprocess. When None,
            the child inherits the server process environment. When
            provided, it completely replaces the child's environment
            (must include PATH and other essentials). Pass a shallow
            copy of os.environ with overrides merged in, e.g.
            ``{**os.environ, "MY_VAR": "1"}``.

    Returns inline 4-tuple or (task, proc, stderr_log_path) on timeout.
    Child stderr is redirected to a tempfile so forge_job_status can
    report real-time progress while a background job runs.

    Raises:
        ValueError: If env is an empty dict (would strip PATH and all
            environment variables, causing the subprocess to fail
            silently).
    """
    if env is not None and not env:
        raise ValueError(
            "env must be None (inherit parent) or a non-empty dict; "
            "an empty dict would strip PATH and all environment "
            "variables from the subprocess"
        )
    stderr_fh = tempfile.NamedTemporaryFile(
        mode="w", prefix="forge-stderr-", suffix=".log", delete=False
    )
    stderr_log_path = stderr_fh.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "code-forge",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_fh,
            cwd=str(workspace),
            env=env,
        )
    except BaseException:
        stderr_fh.close()
        try:
            os.unlink(stderr_log_path)
        except OSError:
            pass
        raise
    stderr_fh.close()  # parent fd closed; child owns the file

    start = time.monotonic()
    inner_task = asyncio.create_task(proc.communicate())
    try:
        stdout_bytes, _stderr_none = await asyncio.wait_for(
            asyncio.shield(inner_task), timeout=budget
        )
        elapsed = time.monotonic() - start
        try:
            stderr_text = Path(stderr_log_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            stderr_text = ""
        try:
            os.unlink(stderr_log_path)
        except OSError:
            pass
        return (
            stdout_bytes.decode(errors="replace"),
            proc.returncode or 0,
            elapsed,
            stderr_text,
        )
    except asyncio.TimeoutError:
        return (inner_task, proc, stderr_log_path)
    except asyncio.CancelledError:
        try:
            os.unlink(stderr_log_path)
        except OSError:
            pass
        await _kill_and_reap(proc, inner_task)
        raise


# -- result formatting --


def _make_result(
    stdout: str, exit_code: int, elapsed: float, stderr: str = "",
) -> CallToolResult:
    """Build dual-layer CallToolResult for completed review/gate-check.

    On non-zero exit, stderr is appended to the output so the caller can
    diagnose the failure.  Previously stderr was discarded, leaving
    ``{output: ""}`` on CLI_ERROR.
    """
    output = stdout
    if exit_code != 0 and stderr.strip():
        output = stdout + "\n--- stderr ---\n" + stderr
    structured = ForgeResult(
        verdict=exit_to_verdict(exit_code),
        exit_code=exit_code,
        findings_count=None,
        duration_s=round(elapsed, 2),
        output=output,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=output)],
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


def _validate_backend(backend: str, workspace: Path) -> None:
    """Raise ToolError if backend name is not in the loaded list."""
    names = _backend_names_for(workspace)
    if backend and names and backend not in names:
        raise ToolError(
            "Unknown backend '%s'. Available: %s"
            % (backend, ", ".join(names))
        )


def _build_review_context(
    cwd: Path, committed: bool, staged: bool = False,
) -> tuple[ResolvedReview, str, str]:
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


_MAX_FINDINGS_IN_RESULT = 20


def _truncate(text: str, limit: int) -> str:
    """Truncate text with ellipsis marker when it exceeds limit.

    When limit < 4, hard-slices without ellipsis (not enough room
    for even one char + "...").  Non-positive limits return the
    empty string.  Callers using small limits lose the truncation
    signal; the sole call site uses limit=200.
    """
    if limit <= 0:
        return ""
    if limit < 4:
        return text[:limit]
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _make_inprocess_result(
    verdict: Verdict, findings_count: int, elapsed: float,
    findings: list[dict] | None = None,
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
        findings=findings,
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
    workspace: Path,
    backend_name: str | None = None,
    staged: bool = False,  # True for gate-check (INDEX), False for review (WORKING)
) -> CallToolResult:
    """Run forge review in-process via MCP sampling transport.

    Builds review context, constructs StateMachine with sampling l1_provider,
    runs machine.run() in a worker thread. On a recoverable sampling
    failure (LLMInvokeError.kind in truncated/empty/stub_model/no_json),
    falls back to CLI subprocess if a backend is available.
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

    resolved, source_hash, baseline_repr = _build_review_context(workspace, committed, staged=staged)

    # capture event loop BEFORE dispatching to worker thread
    loop = asyncio.get_running_loop()

    l1_provider = build_sampling_l1_provider(
        session=session,
        loop=loop,
        resolved=resolved,
    )

    # ponytail: sampling path uses stubs -- stub falsifier (not "auto",
    # which would silently call claude -p), empty registry (no L0 tools),
    # empty source_files. Full wiring deferred until sampling needs it.
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=build_falsifier("stub"),
        autofixer=build_autofixer(resolved),
        revert_fn=build_revert_fn(resolved, workspace),
        resolved_review=resolved,
        source_hash=source_hash,
        baseline_spec_repr=baseline_repr,
        cwd=workspace,
        registry={},
        l1_provider=l1_provider,
        l2_runner=build_l2_runner(),
        e2e_runner=build_e2e_checker(),
    )

    from code_forge.lock import ForgeLock
    lock_path = workspace / ".code-forge" / "code-forge.lock"  # must match cli.py:1653

    # Lock acquisition + machine.run both inside worker thread to avoid
    # blocking the MCP server event loop on lock contention.
    def _run_locked():
        with ForgeLock(lock_path):
            return machine.run()

    t0 = time.monotonic()
    try:
        verdict = await asyncio.to_thread(_run_locked)
    except LLMInvokeError as exc:
        # kind is set by invoke_sampling for the recoverable failure
        # classes; anything else (unknown kind) is not fallback-eligible.
        _can_fallback = exc.kind in (
            "truncated", "empty", "stub_model", "no_json",
        )
        backend_names = _backend_names_for(workspace)
        if _can_fallback and (backend_name or backend_names):
            # sampling failed -- fall back to CLI subprocess backend
            # MUST force --outlet subprocess to prevent infinite loop when
            # gate.yaml has outlet: sampling (subprocess reads gate.yaml too)
            fallback_backend = backend_name or backend_names[0]
            # Fallback only supports "review" command (gate-check parser
            # doesn't accept --backend/--outlet). If staged (gate-check),
            # raise clear error instead of broken CLI call.
            if staged:
                raise ToolError(
                    "Sampling failed during gate-check (%s). "
                    "Run gate-check via the CLI with a configured "
                    "backend instead." % (exc.kind or exc)
                )
            cli_args = ["review", "--no-color", "--backend", fallback_backend,
                        "--outlet", "subprocess"]
            if committed:
                cli_args.append("--committed")
            result = await _run_cli_budgeted(*cli_args, workspace=workspace)
            if isinstance(result[0], str):
                stdout, exit_code, elapsed, stderr = result  # type: ignore[misc]
                return _make_result(stdout, exit_code, elapsed, stderr)
            else:
                inner_task, proc, stderr_path = result  # type: ignore[misc]
                cap = _job_cap_s(workspace, backend_name or "")
                job_id = start_job(inner_task, proc,
                                   stderr_log_path=stderr_path,
                                   max_lifetime_s=cap)
                return _make_job_ref(job_id)
        elif _can_fallback:
            raise ToolError(
                "Sampling failed: %s. Configure an API backend in "
                "gate.yaml for automatic fallback." % exc
            )
        else:
            raise ToolError("Sampling failed: %s" % exc)

    elapsed = time.monotonic() - t0

    # Extract non-dismissed findings from the machine for the MCP result.
    active = machine.active_findings
    compact = [
        {
            "file": f.file,
            "line_range": f.line_range,
            "source": f.source,
            "disposition": f.disposition.value,
            "description": _truncate(f.description or "", 200),
        }
        for f in active[:_MAX_FINDINGS_IN_RESULT]
    ]
    if len(active) > _MAX_FINDINGS_IN_RESULT:
        # Synthetic entry -- disposition is not a Disposition enum member
        # on purpose; consumers should treat source=OVERFLOW as metadata.
        compact.append({
            "file": "",
            "line_range": [],
            "source": "OVERFLOW",
            "disposition": "info",
            "description": "+%d more, see state.json"
                           % (len(active) - _MAX_FINDINGS_IN_RESULT),
        })
    return _make_inprocess_result(
        verdict, findings_count=len(active), elapsed=elapsed,
        findings=compact if compact else None,
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
    backend: str = "",
    contract: str = "",
    committed: bool = False,
    whole_file: bool = False,
    canary: bool = False,
    allow_main: bool = False,
    project_dir: str = "",
    ctx: Context = None,
) -> CallToolResult:
    """Run forge review pipeline."""
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    from code_forge.outlet_resolver import load_outlet_from_gate

    outlet = os.environ.get("FORGE_OUTLET")
    if not outlet:
        gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
        if gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)

    if outlet == "sampling":
        if ctx is None or ctx.session.client_params.capabilities.sampling is None:
            raise ToolError(
                "Client does not support sampling capability. "
                + SAMPLING_REMEDIATION
            )
        return await _dispatch_sampling(
            session=ctx.session,
            committed=committed,
            workspace=workspace,
            backend_name=backend,
            staged=False,
        )

    _check_backend(workspace)
    _validate_backend(backend, workspace)

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

    # Build per-call env when allow_main is requested so we never
    # mutate the server process environment.
    child_env: dict[str, str] | None = (
        {**os.environ, "FORGE_ALLOW_MAIN": "1"} if allow_main else None
    )
    result = await _run_cli_budgeted(
        *cli_args, workspace=workspace, env=child_env
    )

    if isinstance(result[0], str):
        # Inline completion
        stdout, exit_code, elapsed, stderr = result  # type: ignore[misc]
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
        return _make_result(stdout, exit_code, elapsed, stderr)
    else:
        # Timeout -- transfer tempfile ownership to job
        inner_task, proc, stderr_path = result  # type: ignore[misc]
        cap = _job_cap_s(workspace, backend)
        try:
            job_id = start_job(inner_task, proc, tempfile_path=tmp_path,
                               stderr_log_path=stderr_path,
                               max_lifetime_s=cap)
        except Exception:
            for p in (tmp_path, stderr_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            raise
        return _make_job_ref(job_id)


@mcp.tool(
    name="forge_gate_check",
    description="Run pre-commit gate check on staged changes.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def forge_gate_check(
    baseline: str = "",
    backend: str = "",
    project_dir: str = "",
    ctx: Context = None,
) -> CallToolResult:
    """Run forge gate-check pipeline."""
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    from code_forge.outlet_resolver import load_outlet_from_gate

    outlet = os.environ.get("FORGE_OUTLET")
    if not outlet:
        gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
        if gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)

    if outlet == "sampling":
        if ctx is None or ctx.session.client_params.capabilities.sampling is None:
            raise ToolError(
                "Client does not support sampling capability. "
                + SAMPLING_REMEDIATION
            )
        return await _dispatch_sampling(
            session=ctx.session,
            committed=False,
            workspace=workspace,
            backend_name=backend,
            staged=True,
        )

    _check_backend(workspace)
    _validate_backend(backend, workspace)

    cli_args: list[str] = ["gate-check", "--no-color"]
    if baseline:
        cli_args.extend(["--baseline", baseline])
    if backend:
        cli_args.extend(["--backend", backend])

    result = await _run_cli_budgeted(*cli_args, workspace=workspace)

    if isinstance(result[0], str):
        stdout, exit_code, elapsed, stderr = result  # type: ignore[misc]
        return _make_result(stdout, exit_code, elapsed, stderr)
    else:
        inner_task, proc, stderr_path = result  # type: ignore[misc]
        cap = _job_cap_s(workspace, backend)
        job_id = start_job(inner_task, proc,
                           stderr_log_path=stderr_path,
                           max_lifetime_s=cap)
        return _make_job_ref(job_id)


@mcp.tool(
    name="forge_init",
    description="Initialize .code-forge/ directory in the current workspace.",
    annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True),
)
async def forge_init(force: bool = False, project_dir: str = "", ctx: Context = None) -> CallToolResult:
    """Initialize forge configuration.

    Refuses to create project markers at $HOME -- use user-level
    config at ~/.config/code-forge/config.yaml instead.
    """
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    if workspace.resolve() == Path.home().resolve():
        raise ToolError(
            "Refusing to initialize forge at $HOME (%s). "
            "$HOME is a configuration domain, not a project. "
            "cd into a project directory, or set FORGE_PROJECT_DIR, "
            "or write user-level defaults to "
            "~/.config/code-forge/config.yaml."
            % workspace
        )
    cli_args: list[str] = ["init"]
    if force:
        cli_args.append("--force")
    stdout, stderr, exit_code = await _run_cli_simple(
        *cli_args, workspace=workspace)
    return _make_simple_result(stdout, exit_code, stderr)


@mcp.tool(
    name="forge_trust",
    description="Trust the gate.yaml backends in the current workspace.",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def forge_trust(project_dir: str = "", ctx: Context = None) -> CallToolResult:
    """Trust forge backends."""
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    stdout, stderr, exit_code = await _run_cli_simple(
        "trust", workspace=workspace)
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
async def forge_resolve_outlet(project_dir: str = "", ctx: Context = None) -> CallToolResult:
    """Diagnose backend routing.

    Appends the resolved workspace, gate.yaml path, backend names, and
    client capability lines.  When the resolved outlet is "sampling" but
    the client lacks the capability, a MISCONFIG warning is appended so
    the user sees the problem in the diagnostic output (the guards in
    forge_review/forge_gate_check would raise ToolError, but this
    read-only tool surfaces the mismatch without blocking).
    """
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    stdout, stderr, exit_code = await _run_cli_simple(
        "resolve-outlet", workspace=workspace)
    gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
    gate_desc = (
        str(gate_yaml_path)
        if gate_yaml_path.exists()
        else "%s (not found)" % gate_yaml_path
    )
    backend_names = _backend_names_for(workspace)
    context = "workspace: %s\ngate.yaml: %s\nbackends: %s\n" % (
        workspace,
        gate_desc,
        ", ".join(backend_names) if backend_names else "(none)",
    )

    # -- T1: capability diagnostics --
    if ctx is not None:
        caps = ctx.session.client_params.capabilities
        context += "client sampling: %s\n" % (
            "yes" if caps.sampling else "NO"
        )
        context += "client roots:    %s\n" % (
            "yes" if caps.roots else "NO"
        )

        # MISCONFIG: outlet resolved to sampling but client cannot do it.
        # Read outlet the same way forge_review does (env first, gate.yaml
        # second) so the diagnostic condition is identical to the guard.
        from code_forge.outlet_resolver import load_outlet_from_gate

        outlet = os.environ.get("FORGE_OUTLET")
        if not outlet and gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)
        if outlet == "sampling" and caps.sampling is None:
            context += (
                "MISCONFIG: outlet is 'sampling' but client lacks "
                "sampling capability. %s\n" % SAMPLING_REMEDIATION
            )
    else:
        context += "client capabilities: unknown (no MCP session)\n"

    return _make_simple_result(
        stdout.rstrip("\n") + "\n" + context, exit_code, stderr
    )


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
        raise ToolError(
            "Unknown job_id: %s. The server may have restarted since "
            "this job was issued (each instance tracks only its own jobs). "
            "Completed reviews leave receipts under .code-forge/ regardless."
            % job_id
        )

    status = entry["status"]
    forge_result: ForgeResult | None = None

    if status in ("completed", "failed") and entry.get("result"):
        r = entry["result"]
        output = r.get("stdout", "")
        stderr = r.get("stderr", "")
        if r.get("exit_code", 0) != 0 and stderr.strip():
            output = output + "\n--- stderr ---\n" + stderr
        forge_result = ForgeResult(
            verdict=r.get("verdict", "UNKNOWN(-1)"),
            exit_code=r.get("exit_code", -1),
            findings_count=None,
            duration_s=r.get("duration_s", 0.0),
            output=output,
        )

    elapsed_text = ""
    if status == "running":
        elapsed = time.monotonic() - entry["created_at"]
        # Read stderr tail for live progress
        stderr_tail = ""
        log_path = entry.get("stderr_log_path")
        if log_path:
            try:
                sz = os.path.getsize(log_path)
                with open(log_path, "rb") as fh:
                    fh.seek(max(0, sz - 2048))
                    stderr_tail = fh.read().decode("utf-8", errors="replace")
            except OSError:
                pass
        elapsed_text = " (%.0fs)" % elapsed
        if stderr_tail.strip():
            elapsed_text += "\n--- progress ---\n" + stderr_tail.strip()

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
        text = "Job %s: %s%s" % (job_id, status, elapsed_text)

    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=ref.model_dump(),
    )


# -- entry point --


def main() -> None:
    """Run the MCP server on stdio transport."""
    # Prevent CJK/emoji in findings from crashing redirected stdio pipes
    # on Windows (console handles are UTF-16-safe via PEP 528; pipes are
    # not).  Guarded: sys.stdout can be None (pythonw) or a
    # non-TextIOWrapper object without reconfigure.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

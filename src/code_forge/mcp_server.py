# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""MCP stdio server exposing forge review tools to IDE clients.

Seven tools: forge_review, forge_gate_check, forge_init, forge_trust,
forge_resolve_outlet, forge_job_status, forge_server_status. Runs as a local subprocess
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
import hashlib
import json
import logging
import math
import os
import signal
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator

import anyio
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from pydantic import Field, TypeAdapter, ValidationError

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp import server as _fastmcp_server
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.stdio import stdio_server
from mcp.types import (
    INVALID_PARAMS,
    CallToolResult,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    TextContent,
    ToolAnnotations,
)

import code_forge
from code_forge.llm_invoke import effective_invoke_timeout_s
from code_forge.mcp_jobs import (
    ForgeJobRef,
    ForgeResult,
    _read_stderr_tail,
    _terminate_and_reap,
    cleanup_all,
    exit_to_verdict,
    get_job,
    signal_exit_note,
    snapshot_tempfile_paths,
    start_job,
)

log = logging.getLogger(__name__)

_TimeoutSeconds = Annotated[
    float,
    Field(
        strict=True,
        gt=0,
        allow_inf_nan=False,
        description="Total CLI runtime limit in seconds, including background execution; null uses server defaults.",
    ),
]
_timeout_adapter = TypeAdapter(_TimeoutSeconds | None)


def _validate_timeout(timeout_s: float | None) -> float | None:
    """Validate direct calls with the same constraints as the MCP schema."""
    try:
        return _timeout_adapter.validate_python(timeout_s)
    except ValidationError as exc:
        raise ToolError("timeout_s must be a finite positive number or null") from exc


class _TimeoutReceiveStream(ObjectReceiveStream[SessionMessage | Exception]):
    """Validate timeouts before the SDK's JSON dump can erase nonfinite values."""

    def __init__(
        self,
        incoming: ObjectReceiveStream[SessionMessage | Exception],
        outgoing: ObjectSendStream[SessionMessage],
    ) -> None:
        self._incoming = incoming
        self._outgoing = outgoing

    @property
    def extra_attributes(self) -> Mapping[object, Callable[[], Any]]:
        return self._incoming.extra_attributes

    async def receive(self) -> SessionMessage | Exception:
        while True:
            message = await self._incoming.receive()
            if isinstance(message, Exception):
                return message
            request = message.message.root
            if (
                not isinstance(request, JSONRPCRequest)
                or request.method != "tools/call"
                or not isinstance(request.params, dict)
                or request.params.get("name") not in ("forge_review", "forge_gate_check")
                or not isinstance(request.params.get("arguments"), dict)
            ):
                return message
            try:
                _validate_timeout(request.params["arguments"].get("timeout_s"))
            except ToolError as exc:
                error = JSONRPCError(
                    jsonrpc="2.0", id=request.id,
                    error=ErrorData(code=INVALID_PARAMS, message=str(exc)),
                )
                await self._outgoing.send(SessionMessage(message=JSONRPCMessage(error)))
            else:
                return message

    async def aclose(self) -> None:
        await self._incoming.aclose()


class _ForgeFastMCP(FastMCP):
    async def run_stdio_async(self) -> None:
        # Keep the SDK transport, initialization and lifespan; only intercept
        # parsed input before ServerSession's lossy JSON-mode serialization.
        async with stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                _TimeoutReceiveStream(read_stream, write_stream),
                write_stream,
                self._mcp_server.create_initialization_options(),
            )


# -- signal-driven shutdown for the stdio server --
# The CLI signal handler (llm_invoke._install_signal_handlers) raises
# KeyboardInterrupt on SIGTERM, which the asyncio runner swallows.
# The server installs its own handler via loop.add_signal_handler
# (last-install-wins), giving it a real exit path through cleanup_all.

_shutting_down = False
_simple_calls_closing = False
_active_simple_calls: set[asyncio.Task[Any]] = set()
_mcp_cleanup_task: asyncio.Task[None] | None = None


async def _wait_for_shielded_task(task: asyncio.Future[Any]) -> Any:
    """Wait for owned cleanup despite AnyIO or repeated task cancellation."""
    interruption: BaseException | None = None
    current = asyncio.current_task()
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except BaseException as exc:  # noqa: BLE001 -- finish teardown before propagating
                caller_cancelled = current is not None and current.cancelling() > 0
                if not task.done() or (isinstance(exc, asyncio.CancelledError) and caller_cancelled):
                    if interruption is None:
                        interruption = exc
        if interruption is not None:
            try:
                task.result()
            except BaseException as cleanup_error:
                log.error(
                    "owned cleanup failed while caller cancellation was pending",
                    exc_info=(type(cleanup_error), cleanup_error, cleanup_error.__traceback__),
                )
            raise interruption
        return task.result()


async def _cleanup_mcp_processes() -> None:
    """Stop request-owned simple CLIs before cleaning registered background jobs."""
    global _simple_calls_closing  # noqa: PLW0603
    _simple_calls_closing = True
    current = asyncio.current_task()
    active = [task for task in tuple(_active_simple_calls) if task is not current and not task.done()]
    for task in active:
        task.cancel()
    if active:
        results = await asyncio.gather(*active, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                log.error(
                    "simple MCP request failed during shutdown",
                    exc_info=(type(result), result, result.__traceback__),
                )
    await cleanup_all()


async def _await_mcp_cleanup() -> None:
    """Run one shared server cleanup task despite caller cancellation."""
    global _mcp_cleanup_task  # noqa: PLW0603
    if _mcp_cleanup_task is None:
        _mcp_cleanup_task = asyncio.create_task(_cleanup_mcp_processes())
    await _wait_for_shielded_task(_mcp_cleanup_task)


async def _cleanup_after_lifespan_error() -> None:
    """Keep an active request's failure primary if shutdown cleanup also fails."""
    try:
        await _await_mcp_cleanup()
    except BaseException:
        log.exception("MCP cleanup failed while preserving lifespan error")


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

    import code_forge

    if os.getpid() == code_forge.STARTUP_PID:
        original_ppid = code_forge.STARTUP_PPID
    else:
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
            log.warning("prctl(PR_SET_PDEATHSIG) failed: rc=%d errno=%d", rc, errno)
    except OSError as exc:
        log.warning("PR_SET_PDEATHSIG unavailable: %s", exc)

    # Startup race: if the parent died between fork and prctl, the
    # signal will never arrive.  Compare ppid before/after rather
    # than hardcoding ==1, so this works inside PID namespaces
    # where the parent may itself be PID 1.
    if os.getppid() != original_ppid:
        log.warning(
            "Parent changed during startup (was %d, now %d), exiting",
            original_ppid,
            os.getppid(),
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
    """Run shared process cleanup, unlink tempfiles, then hard-exit.

    Tempfile paths are snapshotted BEFORE cleanup_all() because it
    clears _jobs, orphaning entries before _wait_for_job can fire
    its own finally-block unlink.  The snapshot+unlink here is the
    backup path; double-unlink is harmless (OSError caught).
    """
    paths = snapshot_tempfile_paths()
    try:
        await _await_mcp_cleanup()
    except Exception:
        log.exception("MCP process cleanup failed during shutdown")
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

from code_forge.workspace import resolve_workspace  # noqa: E402


def _resolve_workspace() -> Path:
    """Thin wrapper: delegates to the shared, MCP-free resolver."""
    return resolve_workspace(Path.cwd(), os.environ)


# User-level config shared with cli.py via user_config module.
from code_forge.user_config import load_user_backends, merge_backends  # noqa: E402

if TYPE_CHECKING:
    from code_forge.state import Verdict


# -- per-session workspace cache (single-slot, one session per stdio) --
@dataclass
class _WorkspaceCache:
    value: tuple[object, Path] | None = None


_workspace_cache = _WorkspaceCache()


def _root_uri_to_path(uri: str) -> Path:
    """Convert an MCP root URI to a filesystem path.

    POSIX file URIs parse cleanly: file:///src/app.py has a path of
    /src/app.py and Path takes it as is. A Windows one does not.
    file:///C:/Users/x parses to /C:/Users/x -- a slash in front of the
    drive letter -- and Path keeps it, so the result is read as the
    current drive's root followed by a directory literally named "C:".
    No such directory exists, and asking Windows about it raises
    WinError 267 rather than returning False, so the caller's is_file()
    check does not merely miss, it throws.

    The strip is guarded on os.name so POSIX stays byte-for-byte as it
    was, and the shape it looks for -- slash, one letter, colon -- cannot
    match a POSIX absolute path, which has no colon in that position.
    """
    from urllib.parse import unquote, urlparse

    raw = unquote(urlparse(uri).path)
    if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[1].isalpha() and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def _explicit_workspace(project_dir: str) -> Path:
    return Path(project_dir).expanduser().resolve()


def _workspace_from_roots(uris: tuple[str, ...]) -> Path | None:
    candidates = []
    for uri in uris:
        path = _root_uri_to_path(uri)
        if (path / ".code-forge" / "gate.yaml").is_file():
            return path
        candidates.append(path)
    return candidates[0] if candidates else None


async def _workspace_for(ctx, project_dir: str = "") -> Path:
    """Resolve explicit path, cached session, roots, then env/walk-up/cwd.

    Empty project_dir preserves the MCP string schema and falls through.
    Workers only resolve paths; cache publication stays on the event loop.
    """
    if project_dir:
        return await asyncio.to_thread(_explicit_workspace, project_dir)

    if ctx is None:
        return await asyncio.to_thread(_resolve_workspace)

    session = ctx.session
    cached = _workspace_cache.value
    if cached is not None and cached[0] is session:
        return cached[1]

    if session.client_params.capabilities.roots:
        try:
            result = await session.list_roots()
        except (
            McpError,
            BrokenResourceError,
            ClosedResourceError,
            EndOfStream,
            ValidationError,
            RuntimeError,
        ) as exc:
            sys.stderr.write("code-forge: list_roots failed: %s\n" % exc)
            # Retry the RPC on the next call instead of caching fallback.
            return await asyncio.to_thread(_resolve_workspace)

        workspace = await asyncio.to_thread(
            _workspace_from_roots, tuple(str(root.uri) for root in result.roots)
        )
        if workspace is not None:
            _workspace_cache.value = (session, workspace)
            return workspace

    workspace = await asyncio.to_thread(_resolve_workspace)
    _workspace_cache.value = (session, workspace)
    return workspace


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
    except (OSError, cli.CliError) as exc:
        log.warning("project gate.yaml backends unreadable: %s", exc)
    return list(merge_backends(project_backends, user_backends).keys())


def _job_cap_s(workspace: Path | None = None, backend_name: str = "") -> float | None:
    """Explicit wall-clock cap for a background MCP job, or None (#165).

    A LOCAL review runs the mutation baseline every round and may take up to
    FORGE_MAX_TOTAL_ROUNDS rounds, so no derived total fits every review.
    A job is bounded by an inactivity watchdog instead (see _job_idle_s);
    a total cap applies only when FORGE_MCP_JOB_TIMEOUT_S is set and
    positive (or a per-call timeout_s is given). Junk env is ignored.
    """
    env_raw = os.environ.get("FORGE_MCP_JOB_TIMEOUT_S")
    if env_raw is None:
        return None
    try:
        env_val = int(env_raw)
    except ValueError:
        log.warning("FORGE_MCP_JOB_TIMEOUT_S=%r is not an int; no total cap", env_raw)
        return None
    if env_val > 0:
        return float(env_val)
    log.warning("FORGE_MCP_JOB_TIMEOUT_S=%r is not positive; no total cap", env_raw)
    return None


def _job_idle_s(workspace: Path, backend_name: str = "") -> float:
    """Inactivity window for a background MCP job (#165): the longest step
    that may print nothing, plus 600s grace. A step is one LLM invoke
    (effective_invoke_timeout_s(backend)) or one silent test run from the
    trusted gate (see _silent_step_s). Progress lines reset the window, so
    a review of any number of rounds is never cut off while it advances."""
    # Lazy import avoids circular dependency (mcp_server <-> cli).
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
            os.environ,
            configs,
            cli_value=backend_name or None,
        )
    except Exception:
        log.warning(
            "backend resolution failed; falling back to default CLI backend (timeout_s=%d)",
            effective_invoke_timeout_s(_DEFAULT),
            exc_info=True,
        )
        backend = _DEFAULT

    return float(max(effective_invoke_timeout_s(backend), _silent_step_s(workspace)) + 600)


def _trusted_test_block(workspace: Path) -> dict:
    """test.* from the gate the review itself will use (#165): an outside
    FORGE_TEST_CONFIG_ROOT, else the HEAD (review baseline) copy, else an
    untracked in-tree file -- via gate_check.load_trusted_gate_config, not
    the reviewed tree's working copy. {} when none or unreadable."""
    import subprocess

    from code_forge.gate_check import load_trusted_gate_config

    root = os.environ.get("FORGE_TEST_CONFIG_ROOT") or str(workspace)
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=str(workspace),
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
            stdin=subprocess.DEVNULL,
        ).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        head = None
    try:
        test = load_trusted_gate_config(
            config_root=Path(root), source_root=workspace, base_sha=head,
        ).get("test")
    except Exception:  # noqa: BLE001 - only sizes a time window
        return {}
    return test if isinstance(test, dict) else {}


def _silent_step_s(workspace: Path) -> int:
    """Longest single test step that may print no progress (#128/#165):
    one run of test.command (test.timeout_seconds, default 120) or the
    mutant execution (test.mutation_timeout_seconds, default 600), read from the trusted
    gate. 0 when no test command is configured (no baseline)."""
    test = _trusted_test_block(workspace)
    if not test.get("command"):
        return 0
    steps = []
    from .mutation_config import DEFAULT_MUTATION_TIMEOUT

    # #183: an unset mutation timeout runs mutmut with its real default.
    for key, default in (("timeout_seconds", 120), ("mutation_timeout_seconds", DEFAULT_MUTATION_TIMEOUT)):
        value = test.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            value = default
        steps.append(value)
    return max(steps)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Load backend names at startup, clean up subprocesses on shutdown.

    Backend merge order: project-level gate.yaml first, then
    user-level defaults append for names not already defined.
    Project backends lead so fallback ([0]) picks a CLI-resolvable
    backend.  Only backend names are tracked here; the actual
    BackendConfig loading happens per-review in _check_backend.
    """
    global _mcp_cleanup_task, _simple_calls_closing  # noqa: PLW0603
    if not _shutting_down:
        _mcp_cleanup_task = None
        _simple_calls_closing = False
    _install_pdeathsig()

    startup_ws = _resolve_workspace()
    log.info("startup workspace: %s", startup_ws)

    # Install signal handlers that actually terminate the server.
    # Windows event loops raise NotImplementedError here: SIGTERM on
    # Windows is TerminateProcess (no handler can run), and Ctrl+C
    # reaches asyncio.run as KeyboardInterrupt without our help.
    # stdio EOF still exits the lifespan, so server cleanup runs on
    # every orderly shutdown; only the kill-without-EOF path loses
    # cleanup, same gap _install_pdeathsig documents for non-Linux.
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: _schedule_shutdown(signal.SIGTERM, loop))
        loop.add_signal_handler(signal.SIGINT, lambda: _schedule_shutdown(signal.SIGINT, loop))
    except NotImplementedError:
        log.info(
            "loop.add_signal_handler unsupported on this platform; relying on stdio EOF for shutdown"
        )

    try:
        yield {}
    except BaseException:
        await _cleanup_after_lifespan_error()
        raise
    else:
        await _await_mcp_cleanup()


# The SDK defines Settings before FastMCP. Resolve its forward references
# before pydantic-settings inspects environment-backed fields.
_fastmcp_server.Settings.model_rebuild(_types_namespace=vars(_fastmcp_server))

mcp = _ForgeFastMCP(
    "code-forge-mcp",
    instructions=(
        "Forge code review tools. The server auto-detects the project "
        "root via FORGE_PROJECT_DIR env var, or by walking up from cwd "
        "to find .code-forge/gate.yaml (skipping $HOME). User-level "
        "backend defaults in ~/.config/code-forge/config.yaml merge "
        "under project backends. "
        "Use forge_review to review git diffs, "
        "forge_gate_check for pre-commit gating, forge_resolve_outlet to "
        "diagnose backend configuration, and forge_server_status to inspect "
        "this server's source identity even when a source change blocks other tools."
    ),
    lifespan=lifespan,
)

# Null-coercion fallback: MCP clients may send null for optional string
# params. Pydantic's str type rejects null, but our schema uses str=""
# for display cleanliness. Coerce None -> "" before validation.
#
# TECHNICAL DEBT: uses private mcp._tool_manager.call_tool because SDK
# FastMCP exposes no middleware/call-interceptor hook. Coerces all None
# values except the nullable timeout_s on review/gate tools. Pydantic
# rejects "" for bool/int exactly as it rejects None. Mitigations: pin mcp<2 in
# pyproject.toml; test_null_coercion_* tests act as a tripwire if a
# future mcp 1.x renames _tool_manager (import-time crash, suite RED).
# Upstream FR for middleware support would let us drop this entirely.
_original_tc = mcp._tool_manager.call_tool
_package_import_file = Path(code_forge.__file__).absolute()
_loaded_package_dir = _package_import_file.resolve().parent
_server_instance_id = uuid.uuid4().hex
_module_loaded_at = datetime.now(timezone.utc).isoformat()


def _package_dir() -> Path:
    """Directory currently reached through the captured package import path.

    An editable install points this at the git checkout, so a pull changes
    the files under it while this process keeps the old modules loaded.
    """
    return _package_import_file.resolve().parent


def _source_digest() -> str:
    """Digest of every Python file shipped in the package.

    Covers the modules a tool call imports lazily, not just this one, so a
    symbol added in any of them shows up as a drift.
    """
    digest = hashlib.blake2b(digest_size=16)
    root = _package_dir()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


_loaded_digest = _source_digest()


def _refuse_if_source_moved() -> None:
    if _source_digest() == _loaded_digest:
        return
    raise ToolError(
        "code_forge source changed on disk after this server started. "
        "The loaded modules are stale and the next lazy import can raise "
        "ImportError. Reload the MCP server in this session."
    )


async def _null_coerce_call_tool(name, arguments, **kw):
    if name != "forge_server_status":
        _refuse_if_source_moved()
    if name in {"forge_review", "forge_gate_check"}:
        _validate_timeout(arguments.get("timeout_s"))
    for k, v in list(arguments.items()):
        if v is None and not (k == "timeout_s" and name in {"forge_review", "forge_gate_check"}):
            arguments[k] = ""
    return await _original_tc(name, arguments, **kw)


mcp._tool_manager.call_tool = _null_coerce_call_tool


@mcp.tool(
    name="forge_server_status",
    description="Inspect this server's loaded and current source identity without starting work.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
async def forge_server_status() -> CallToolResult:
    """Describe this module instance without importing or executing changed code.

    The loaded digest is a package snapshot taken at module initialization,
    not a Git commit or proof that every module was imported. The instance
    token and timestamp describe that initialization, not OS process birth.
    package_path is the loaded physical root, literal unless
    package_path_is_escaped is true: then
    surrogate characters use backslash escapes for display, and
    package_path_bytes_hex preserves the exact filesystem bytes.
    """
    source_error_type = None
    try:
        current_digest = _source_digest()
    except (OSError, UnicodeEncodeError) as exc:
        current_digest = None
        source_state = "unreadable"
        source_error_type = type(exc).__name__
        reason = "Cannot inspect package source. Restore readable source and reconnect this MCP connection."
    else:
        if current_digest == _loaded_digest:
            source_state = "unchanged"
            reason = "Package source matches this server's module-load snapshot."
        else:
            source_state = "changed"
            reason = "Package source changed. Reconnect this MCP connection after the source update completes."
    package_path = str(_loaded_package_dir)
    display_path = package_path.encode("utf-8", "backslashreplace").decode("utf-8")
    path_is_escaped = display_path != package_path
    status = {
        "package_path": display_path,
        "package_path_is_escaped": path_is_escaped,
        "package_path_bytes_hex": os.fsencode(package_path).hex() if path_is_escaped else None,
        "loaded_digest": _loaded_digest,
        "current_digest": current_digest,
        "source_state": source_state,
        "reconnect_required": source_state != "unchanged",
        "reason": reason,
        "process_id": os.getpid(),
        "server_instance_id": _server_instance_id,
        "module_loaded_at": _module_loaded_at,
        "source_error_type": source_error_type,
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(status, indent=2))],
        structuredContent=status,
    )


# -- pre-flight helper --


def _check_backend(workspace: Path) -> None:
    """Verify a trusted review backend is configured.

    Checks gate.yaml existence then loads via _load_gate_backends.
    Does NOT call resolve_outlet (avoids HTTP probe latency).
    """
    gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
    if not gate_yaml_path.exists():
        raise ToolError("gate.yaml not found at %s. Run 'code-forge init'." % gate_yaml_path)
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
                "or project gate.yaml. "
                "(workspace: %s -- wrong project? set "
                "FORGE_PROJECT_DIR in the MCP server env)" % (gate_yaml_path, workspace)
            )
    except (CliError, ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc

    # Key env check: only block if ZERO backends have valid keys.
    # With user-level backends, a user may configure 5 but only have
    # keys for 2 -- blocking all reviews for missing keys on unused
    # backends is unnecessarily strict.
    available = [
        cfg for cfg in backend_configs if not cfg.api_key_env or os.environ.get(cfg.api_key_env)
    ]
    missing_pairs = sorted(
        set(
            (cfg.name, cfg.api_key_env)
            for cfg in backend_configs
            if cfg.api_key_env and not os.environ.get(cfg.api_key_env)
        )
    )
    detail = ", ".join("%s: %s" % (n, k) for n, k in missing_pairs) if missing_pairs else ""
    if missing_pairs:
        log.warning("Backends with missing API keys (unavailable): %s", detail)
    if not available:
        raise ToolError(
            "API key env var(s) not set in the MCP server process: "
            "%s. Set them in the MCP server config env block (or the "
            "wrapper script), then restart the MCP server." % detail
        )


# -- CLI runner helpers --


async def _run_cli_simple(*args: str, workspace: Path) -> tuple[str, str, int]:
    """Run a CLI command and return (stdout, stderr, exit_code)."""
    if _shutting_down or _simple_calls_closing:
        raise asyncio.CancelledError
    current_task = asyncio.current_task()
    if current_task is not None:
        _active_simple_calls.add(current_task)
    try:
        proc = await asyncio.create_subprocess_exec(
            "code-forge",
            *args,
            stdin=asyncio.subprocess.DEVNULL,  # #111: not the MCP stdio protocol pipe
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            start_new_session=True,
        )
        comm_task = asyncio.create_task(proc.communicate())
        try:
            stdout_bytes, stderr_bytes = await comm_task
        except BaseException:
            # This request owns the process until communicate completes. Do not let
            # cancellation abandon the child or interrupt its shared teardown.
            cleanup = asyncio.create_task(_kill_and_reap(proc, comm_task))
            try:
                await _wait_for_shielded_task(cleanup)
            except BaseException as cleanup_error:
                # Preserve the exception that entered this handler; make a
                # failed cleanup visible without replacing that cause.
                if not (isinstance(cleanup_error, asyncio.CancelledError) and not cleanup.cancelled()):
                    log.exception("failed to terminate and reap simple CLI child")
            if proc.returncode is None:
                log.error("simple CLI child is still live after cleanup attempt")
            raise
        return (
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
            proc.returncode or 0,
        )
    finally:
        if current_task is not None:
            _active_simple_calls.discard(current_task)


async def _kill_and_reap(
    proc: asyncio.subprocess.Process,
    task: asyncio.Task,
) -> None:
    """Best-effort subprocess cleanup.  Never raises."""
    task.cancel()
    await _terminate_and_reap(proc)


def _read_and_unlink_stderr(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        _unlink(path)


async def _run_cli_budgeted(
    *args: str,
    workspace: Path,
    budget: float = 20.0,
    env: dict[str, str] | None = None,
    deadline: float | None = None,
) -> tuple[str, int, float, str] | tuple[asyncio.Task[Any], asyncio.subprocess.Process, str]:
    """Run CLI with a time budget.

    Args:
        *args: CLI arguments to pass to code-forge.
        workspace: Working directory for the subprocess.
        budget: Maximum wall-clock seconds before timeout.
        deadline: Optional absolute monotonic deadline, including spawn time.
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
            stdin=asyncio.subprocess.DEVNULL,  # #111: not the MCP stdio protocol pipe
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_fh,
            cwd=str(workspace),
            env=env,
            start_new_session=True,
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
    if deadline is not None:
        budget = min(budget, max(0.0, deadline - start))
    inner_task = asyncio.create_task(proc.communicate())
    try:
        stdout_bytes, _stderr_none = await asyncio.wait_for(asyncio.shield(inner_task), timeout=budget)
        elapsed = time.monotonic() - start
        stderr_text = await asyncio.to_thread(_read_and_unlink_stderr, stderr_log_path)
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
        cleanup = asyncio.create_task(_kill_and_reap(proc, inner_task))
        try:
            await _wait_for_shielded_task(cleanup)
        except BaseException as cleanup_error:
            if not (isinstance(cleanup_error, asyncio.CancelledError) and not cleanup.cancelled()):
                log.exception("failed to terminate and reap budgeted CLI child")
        if proc.returncode is None:
            log.error("budgeted CLI child is still live after cleanup attempt")
        raise


# -- result formatting --


_MAX_SARIF_REPORT_CHARS = 1024 * 1024
_MAX_SARIF_PROJECTION_DEPTH = 64


def _sarif_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate SARIF key")
        result[key] = value
    return result


def _sarif_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite SARIF number")
    return number


def _sarif_projection_depth_ok(findings: list[dict], audit: list[dict] | None) -> bool:
    """Count dictionary/list levels, including each projected list root.

    Iterators keep traversal space bounded by depth, independent of width.
    """
    stack = [(iter((findings, audit)), 0)]
    while stack:
        children, depth = stack[-1]
        try:
            value = next(children)
        except StopIteration:
            stack.pop()
            continue
        if type(value) is dict:
            values = value.values()
        elif type(value) is list:
            values = value
        else:
            continue
        if depth >= _MAX_SARIF_PROJECTION_DEPTH:
            return False
        stack.append((iter(values), depth + 1))
    return True


_CAPACITY_INCOMPLETE_COUNT_STATUS = (
    "unknown: capacity-incomplete review -- provider capacity cut acquisition short, "
    "so the published results are not a complete count"
)


def _sarif_result_projection(stdout: str) -> tuple[int | None, list[dict] | None, list[dict] | None]:
    """Three-field projection; see ``_sarif_projection_with_status``."""
    return _sarif_projection_with_status(stdout)[:3]


def _sarif_projection_with_status(
    stdout: str,
) -> tuple[int | None, list[dict] | None, list[dict] | None, str | None]:
    """Read presentation from one CLI SARIF document, never review authority.

    The 1 MiB-character cutoff limits optional parsing and structured copies
    made by the MCP serializer. Wide reports can otherwise exhaust memory
    despite shallow nesting. Original output is retained unchanged;
    unsupported reports remain uncounted.
    Each projected list includes at most 64 dictionary/list levels so the
    full MCP response envelope retains ample serialization depth headroom.
    """
    unknown = (None, None, None, None)
    if type(stdout) is not str or len(stdout) > _MAX_SARIF_REPORT_CHARS:
        return unknown
    try:
        document = json.loads(stdout, object_pairs_hook=_sarif_object,
                              parse_float=_sarif_float, parse_constant=_sarif_float)
        if type(document) is not dict or document.get("version") != "2.1.0":
            return unknown
        runs = document.get("runs")
        if type(runs) is not list or len(runs) != 1 or type(runs[0]) is not dict:
            return unknown
        run = runs[0]
        tool = run.get("tool")
        if type(tool) is not dict or type(tool.get("driver")) is not dict:
            return unknown
        if tool["driver"].get("name") != "code-forge":
            return unknown
        findings = run.get("results")
        if type(findings) is not list or any(type(row) is not dict for row in findings):
            return unknown
        properties = run.get("properties", {})
        if type(properties) is not dict:
            return unknown
        audit = properties.get("receiptAudit")
        if "receiptAudit" in properties and (
            type(audit) is not list or any(type(row) is not dict for row in audit)
        ):
            return unknown
        if not _sarif_projection_depth_ok(findings, audit):
            return unknown
        # Escaped unpaired surrogates can parse but cannot reach MCP UTF-8.
        json.dumps((findings, audit), ensure_ascii=False, allow_nan=False).encode("utf-8")
        diagnostics = properties.get("providerDiagnostics")
        if type(diagnostics) is list and any(
            type(row) is dict and row.get("diagnostic_kind") == "capacity-incomplete"
            for row in diagnostics
        ):
            # #221: a capacity-incomplete review did not acquire every pass;
            # its result list is not a count. Unknown, labelled -- never 0.
            return None, findings, audit, _CAPACITY_INCOMPLETE_COUNT_STATUS
        return len(findings), findings, audit, None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return unknown


def _report_classification_from_stdout(stdout: str) -> dict[str, object]:
    """Shared reader: parse additive report_classification from CLI SARIF stdout.

    Uses the IC-01 ``parse_report_classification`` / ``wire_to_json`` helpers so
    inline ``_make_result``, job status, and SDK serialization see the same
    normalized block. Does not rewrite legacy findings_count fields.
    Empty stdout → unavailable/no-report; non-JSON / non-run → rejected path.
    """
    from code_forge.report_presentation import (
        parse_report_classification,
        wire_to_json,
    )

    if type(stdout) is not str or stdout == "":
        return wire_to_json(
            parse_report_classification(None, legacy_report_state="empty")
        )
    try:
        document = json.loads(stdout)
    except (ValueError, TypeError):
        return wire_to_json(
            parse_report_classification(None, legacy_report_state="rejected")
        )
    if type(document) is not dict:
        return wire_to_json(
            parse_report_classification(None, legacy_report_state="rejected")
        )
    runs = document.get("runs")
    if type(runs) is not list or not runs or type(runs[0]) is not dict:
        return wire_to_json(
            parse_report_classification(None, legacy_report_state="rejected")
        )
    return wire_to_json(
        parse_report_classification(runs[0], legacy_report_state="accepted")
    )


def _make_result(
    stdout: str,
    exit_code: int,
    elapsed: float,
    stderr: str = "",
) -> CallToolResult:
    """Build dual-layer CallToolResult for completed review/gate-check.

    On non-zero exit, stderr is appended to the output so the caller can
    diagnose the failure.  Previously stderr was discarded, leaving
    ``{output: ""}`` on CLI_ERROR.
    """
    output = stdout
    if exit_code != 0 and stderr.strip():
        output = stdout + "\n--- stderr ---\n" + stderr
    note = signal_exit_note(exit_code)
    if note:
        output = output + ("\n" if output else "") + note
    findings_count, findings, receipt_audit, count_status = _sarif_projection_with_status(stdout)
    structured = ForgeResult(
        verdict=exit_to_verdict(exit_code),
        exit_code=exit_code,
        findings_count=findings_count,
        findings_count_status=count_status,
        findings=findings,
        receipt_audit=receipt_audit,
        duration_s=round(elapsed, 2),
        output=output,
        report_classification=_report_classification_from_stdout(stdout),
    )
    return CallToolResult(
        content=[TextContent(type="text", text=output)],
        structuredContent=structured.model_dump(),
    )


SAMPLING_REMOVED = (
    "sampling outlet was removed: the Model Context Protocol deprecated "
    "Sampling on 2026-07-28. Configure an API backend in gate.yaml, or "
    "set FORGE_OUTLET=inline."
)


def _make_simple_result(
    stdout: str,
    exit_code: int,
    stderr: str = "",
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
    ref = ForgeJobRef(job_id=job_id, status="running", poll_after_seconds=10, result=None)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=("Review running in background. Poll with forge_job_status(job_id='%s')." % job_id),
            )
        ],
        structuredContent=ref.model_dump(),
    )


def _unlink(path: str | None) -> None:
    """Best-effort unlink; never raises."""
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _dispatch_cli(
    cli_args: list[str],
    workspace: Path,
    cap: float | None,
    contract: str | None = None,
    focus: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float | None = None,
    idle_s: float | None = None,
) -> CallToolResult:
    """Shared CLI-subprocess dispatch with contract+focus tmpfile lifecycle.

    Materializes optional contract and focus to tmpfiles, runs
    _run_cli_budgeted, and routes the result to inline or job
    completion.  Cleanup is automatic on every exit path:
      - raise from _run_cli_budgeted: unlink both tmpfiles, re-raise
      - inline result: unlink both tmpfiles, return _make_result
      - job result: transfer tmpfile ownership to start_job;
        if start_job raises, unlink all three (contract + focus + stderr)
    """
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    contract_tmp: str | None = None
    focus_tmp: str | None = None
    try:
        if contract:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
            contract_tmp = tmp.name
            tmp.write(contract)
            tmp.close()
            cli_args.extend(["--contract", contract_tmp])
        if focus:
            ftmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
            focus_tmp = ftmp.name
            ftmp.write(focus)
            ftmp.close()
            cli_args.extend(["--focus", focus_tmp])
    except BaseException:
        _unlink(contract_tmp)
        _unlink(focus_tmp)
        raise

    try:
        if deadline is None:
            result = await _run_cli_budgeted(*cli_args, workspace=workspace, env=env)
        else:
            result = await _run_cli_budgeted(
                *cli_args, workspace=workspace, env=env, deadline=deadline
            )
    except BaseException:
        _unlink(contract_tmp)
        _unlink(focus_tmp)
        raise

    if isinstance(result[0], str):
        stdout, exit_code, elapsed, stderr = result  # type: ignore[misc]
        _unlink(contract_tmp)
        _unlink(focus_tmp)
        return _make_result(stdout, exit_code, elapsed, stderr)

    # Timeout -- transfer ownership to background job
    inner_task, proc, stderr_path = result  # type: ignore[misc]
    try:
        job_id = start_job(
            inner_task,
            proc,
            tempfile_path=contract_tmp,
            focus_tempfile_path=focus_tmp,
            stderr_log_path=stderr_path,
            max_lifetime_s=cap if deadline is None else max(0.0, deadline - time.monotonic()),
            **({"deadline": deadline} if deadline is not None else {}),
            **({"idle_timeout_s": idle_s} if deadline is None and idle_s is not None else {}),
        )
    except Exception:
        _unlink(contract_tmp)
        _unlink(focus_tmp)
        _unlink(stderr_path)
        raise
    return _make_job_ref(job_id)


def _validate_backend(backend: str, workspace: Path) -> None:
    """Raise ToolError if backend name is not in the loaded list."""
    names = _backend_names_for(workspace)
    if backend and names and backend not in names:
        raise ToolError("Unknown backend '%s'. Available: %s" % (backend, ", ".join(names)))


def _normalize_whole_file(
    whole_file: list[str] | str | bool | None,
    workspace: Path | None = None,
) -> list[str]:
    """Normalize and validate whole_file parameter into relative paths.

    Accepts:
      - None / False / empty: returns []
      - str: single file path -> [path]
      - list[str]: list of file paths -> [path, ...]
      - True: raises ToolError (paths required)
    """
    if whole_file is None or whole_file is False or whole_file == "" or whole_file == []:
        return []
    if whole_file is True:
        raise ToolError(
            "whole_file requires one or more file paths "
            "(e.g. whole_file='src/foo.py' or whole_file=['src/foo.py'])"
        )
    if isinstance(whole_file, str):
        raw_paths = [whole_file]
    elif isinstance(whole_file, (list, tuple)):
        raw_paths = list(whole_file)
    else:
        raise ToolError("Invalid whole_file parameter: expected string or list of strings")

    normalized: list[str] = []
    cwd = workspace or Path.cwd()
    cwd_resolved = cwd.resolve()
    for p in raw_paths:
        if not isinstance(p, str) or not p.strip():
            raise ToolError("--whole-file: path must be a non-empty string")
        pp = Path(p)
        if pp.is_absolute():
            raise ToolError("--whole-file: path must be relative, got: %s" % p)
        resolved_p = (cwd / pp).resolve()
        try:
            resolved_p.relative_to(cwd_resolved)
        except ValueError as exc:
            raise ToolError("--whole-file: path escapes repo root: %s" % p) from exc
        normalized.append(p)
    return normalized


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
    verdict: Verdict,
    findings_count: int,
    elapsed: float,
    findings: list[dict] | None = None,
    receipt_audit: list[dict] | None = None,
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
    if receipt_audit:
        summary += " receipt_audit=%d (metadata only; see state.json)" % len(receipt_audit)
    structured = ForgeResult(
        verdict=verdict.value,
        exit_code=exit_code,
        findings_count=findings_count,
        findings=findings,
        receipt_audit=receipt_audit,
        duration_s=round(elapsed, 2),
        output=summary,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structuredContent=structured.model_dump(),
    )


# -- tool handlers --


@mcp.tool(
    name="forge_review",
    description=(
        "Run the forge review pipeline on the current git diff. "
        "baseline/head name an explicit commit range; leave them empty "
        "(the default) to review the working tree. An empty string means "
        "'not supplied', so it cannot be used to request an empty range. "
        "Long-running: returns inline if <20s, otherwise returns job_id for "
        "polling via forge_job_status."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def forge_review(
    baseline: str = "",
    head: str = "",
    backend: str = "",
    contract: str = "",
    focus: str = "",
    committed: bool = False,
    whole_file: list[str] | str | bool | None = None,
    canary: bool = False,
    allow_main: bool = False,
    project_dir: str = "",
    ctx: Context = None,
    timeout_s: _TimeoutSeconds | None = None,
) -> CallToolResult:
    """Run forge review pipeline."""
    timeout_s = _validate_timeout(timeout_s)
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    whole_files = _normalize_whole_file(whole_file, workspace=workspace)
    if whole_files and committed:
        raise ToolError("whole_file cannot be combined with committed=True")

    from code_forge.outlet_resolver import load_outlet_from_gate

    outlet = os.environ.get("FORGE_OUTLET")
    if not outlet:
        gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
        if gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)

    if outlet == "sampling":
        raise ToolError(SAMPLING_REMOVED)

    _check_backend(workspace)
    _validate_backend(backend, workspace)

    cli_args: list[str] = ["review", "--no-color"]
    if baseline:
        cli_args.extend(["--baseline", baseline])
    if head:
        cli_args.extend(["--head", head])
    if backend:
        cli_args.extend(["--backend", backend])
    if committed:
        cli_args.append("--committed")
    if whole_files:
        cli_args.append("--whole-file")
        cli_args.extend(whole_files)
    if canary:
        cli_args.append("--canary")

    # Build per-call env when allow_main is requested so we never
    # mutate the server process environment.
    child_env: dict[str, str] | None = {**os.environ, "FORGE_ALLOW_MAIN": "1"} if allow_main else None
    cap = _job_cap_s(workspace, backend) if timeout_s is None else timeout_s
    # #165 P3: reading the gate/config can block; keep it off the event loop.
    idle_s = await asyncio.to_thread(_job_idle_s, workspace, backend) if timeout_s is None else None
    return await _dispatch_cli(
        cli_args,
        workspace,
        cap,
        contract=contract or None,
        focus=focus or None,
        env=child_env,
        **({"timeout_s": timeout_s} if timeout_s is not None else {"idle_s": idle_s}),
    )


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
    timeout_s: _TimeoutSeconds | None = None,
) -> CallToolResult:
    """Run forge gate-check pipeline."""
    timeout_s = _validate_timeout(timeout_s)
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    from code_forge.outlet_resolver import load_outlet_from_gate

    outlet = os.environ.get("FORGE_OUTLET")
    if not outlet:
        gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
        if gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)

    if outlet == "sampling":
        raise ToolError(SAMPLING_REMOVED)

    _check_backend(workspace)
    _validate_backend(backend, workspace)

    cli_args: list[str] = ["gate-check", "--no-color"]
    if baseline:
        cli_args.extend(["--baseline", baseline])
    if backend:
        cli_args.extend(["--backend", backend])

    cap = _job_cap_s(workspace, backend) if timeout_s is None else timeout_s
    # #165 P3: reading the gate/config can block; keep it off the event loop.
    idle_s = await asyncio.to_thread(_job_idle_s, workspace, backend) if timeout_s is None else None
    return await _dispatch_cli(
        cli_args, workspace, cap,
        **({"timeout_s": timeout_s} if timeout_s is not None else {"idle_s": idle_s}),
    )


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
            "~/.config/code-forge/config.yaml." % workspace
        )
    cli_args: list[str] = ["init"]
    if force:
        cli_args.append("--force")
    stdout, stderr, exit_code = await _run_cli_simple(*cli_args, workspace=workspace)
    return _make_simple_result(stdout, exit_code, stderr)


@mcp.tool(
    name="forge_trust",
    description="Trust the gate.yaml backends in the current workspace.",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def forge_trust(project_dir: str = "", ctx: Context = None) -> CallToolResult:
    """Trust forge backends."""
    workspace = await _workspace_for(ctx, project_dir=project_dir)
    stdout, stderr, exit_code = await _run_cli_simple("trust", workspace=workspace)
    return _make_simple_result(stdout, exit_code, stderr)


@mcp.tool(
    name="forge_resolve_outlet",
    description=("Diagnose which review backend and outlet forge will use. Read-only."),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
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
    stdout, stderr, exit_code = await _run_cli_simple("resolve-outlet", workspace=workspace)
    gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
    gate_desc = str(gate_yaml_path) if gate_yaml_path.exists() else "%s (not found)" % gate_yaml_path
    backend_names = _backend_names_for(workspace)
    context = "workspace: %s\ngate.yaml: %s\nbackends: %s\n" % (
        workspace,
        gate_desc,
        ", ".join(backend_names) if backend_names else "(none)",
    )

    # -- T1: capability diagnostics --
    outlet = os.environ.get("FORGE_OUTLET")
    if ctx is not None:
        caps = ctx.session.client_params.capabilities
        context += "client sampling: %s\n" % ("yes" if caps.sampling else "NO")
        context += "client roots:    %s\n" % ("yes" if caps.roots else "NO")

        # MISCONFIG: outlet resolved to sampling but client cannot do it.
        # Read outlet the same way forge_review does (env first, gate.yaml
        # second) so the diagnostic condition is identical to the guard.
        from code_forge.outlet_resolver import load_outlet_from_gate

        if not outlet and gate_yaml_path.exists():
            outlet = load_outlet_from_gate(gate_yaml_path)
        if outlet == "sampling":
            context += "REMOVED: %s\n" % SAMPLING_REMOVED
    else:
        context += "client capabilities: unknown (no MCP session)\n"

    return _make_simple_result(stdout.rstrip("\n") + "\n" + context, exit_code, stderr)


@mcp.tool(
    name="forge_job_status",
    description=(
        "Poll a long-running forge review job. Returns current status and result when complete."
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
            "Completed reviews leave receipts under .code-forge/ regardless." % job_id
        )

    status = entry["status"]
    forge_result: ForgeResult | None = None

    if status in ("completed", "failed") and entry.get("result"):
        r = entry["result"]
        output = r.get("stdout", "")
        stderr = r.get("stderr", "")
        if (status == "failed" or r.get("exit_code", 0) != 0) and stderr.strip():
            output = output + "\n--- stderr ---\n" + stderr
        if str(r.get("verdict", "")).startswith("SIGNALED("):
            note = signal_exit_note(r.get("exit_code", 0))
            if note:
                output = output + ("\n" if output else "") + note
        job_stdout = r.get("stdout", "")
        findings_count, findings, receipt_audit, count_status = _sarif_projection_with_status(job_stdout)
        forge_result = ForgeResult(
            verdict=r.get("verdict", "UNKNOWN(-1)"),
            exit_code=r.get("exit_code", -1),
            findings_count=findings_count,
            findings_count_status=count_status,
            findings=findings,
            receipt_audit=receipt_audit,
            duration_s=r.get("duration_s", 0.0),
            output=output,
            report_classification=_report_classification_from_stdout(job_stdout),
        )

    elapsed_text = ""
    if status == "running":
        elapsed = time.monotonic() - entry["created_at"]
        # Snapshot the path so the worker never observes mutable job state.
        stderr_tail = await asyncio.to_thread(
            _read_stderr_tail, {"stderr_log_path": entry.get("stderr_log_path")}
        )
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
        if forge_result.output:
            text += "\n" + forge_result.output
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

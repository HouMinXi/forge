"""Self-check command for forge workspace health.

Aggregates existing public functions (no new diagnostic engine).
Exit 0 = all green, 1 = any FAIL or SKIP.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import yaml

from code_forge.workspace import SAMPLING_REMEDIATION, resolve_workspace


# -- Registry map (D-4) ---------------------------------------------------

@dataclass(frozen=True)
class RegistryEntry:
    name: str
    paths: list[str]
    key: str
    xml: bool = False


REGISTRY_MAP = [
    RegistryEntry(
        name="Claude Code",
        paths=["~/.claude.json", "~/.claude/settings.json"],
        key="mcpServers",
    ),
    RegistryEntry(
        name="Cursor",
        paths=["~/.cursor/mcp.json"],
        key="mcpServers",
    ),
    RegistryEntry(
        name="VS Code",
        paths=[
            "~/.vscode/settings.json",
            "~/.config/Code/User/settings.json",
        ],
        key="servers",
    ),
    RegistryEntry(
        name="Windsurf",
        paths=["~/.codeium/windsurf/mcp_config.json"],
        key="mcpServers",
    ),
    RegistryEntry(
        name="JetBrains",
        paths=["~/.config/JetBrains/*/llm.mcpServers.xml"],
        key="",
        xml=True,
    ),
    RegistryEntry(
        name="JetBrains JSON",
        paths=["~/.config/JetBrains/*/mcp.json"],
        key="mcpServers",
    ),
]


# -- Individual checks -----------------------------------------------------


def _check_workspace(
    cwd: Path, env: Mapping[str, str],
) -> tuple[bool, str, Optional[Path]]:
    try:
        ws = resolve_workspace(cwd, env)
        return (True, str(ws), ws)
    except Exception as exc:
        return (False, str(exc), None)


def _check_gate_yaml(
    workspace: Path,
) -> tuple[bool, str, Optional[dict]]:
    gate_path = workspace / ".code-forge" / "gate.yaml"
    try:
        with open(gate_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return (False, "not found: %s" % gate_path, None)
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        return (False, "gate.yaml: %s" % exc, None)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return (
            False,
            "gate.yaml must be a mapping, got %s" % type(data).__name__,
            None,
        )
    return (True, str(gate_path), data)


def _check_trust(
    gate_yaml_path: Path, gate_data: dict,
) -> tuple[bool, str]:
    from code_forge.trust import trust_status

    status = trust_status(gate_yaml_path, gate_data)
    if status.trusted:
        return (True, "granted")
    return (False, "not granted (run: code-forge trust)")


def _check_backends(
    workspace: Path, gate_data: dict, env: Mapping[str, str],
) -> tuple[list[tuple[bool, str]], list]:
    """Return diagnostic lines AND merged BackendConfig list."""
    from code_forge.backend import load_backend_configs, probe_backend
    from code_forge.user_config import load_user_backends, merge_backends

    try:
        project_raw = gate_data.get("backends") or {}
        user_raw = load_user_backends()
        merged_raw = merge_backends(project_raw, user_raw)
        configs = load_backend_configs({"backends": merged_raw})
    except Exception as exc:
        return ([(False, "backend config error: %s" % exc)], [])

    if not configs:
        return ([(False, "no backends configured")], [])

    diag: list[tuple[bool, str]] = []
    for cfg in configs:
        provenance = "(project)" if cfg.name in project_raw else "(user)"
        # Informational: note the shadow, then probe the project version
        if cfg.name in project_raw and cfg.name in user_raw:
            diag.append(
                (True, "%s (user) SHADOWED by project" % cfg.name))
        try:
            result = probe_backend(cfg, env=env)
            if result.ok:
                diag.append((True, "%s %s" % (cfg.name, provenance)))
            else:
                diag.append((False, "%s %s: %s" % (
                    cfg.name, provenance,
                    result.error or "probe failed")))
        except Exception as exc:
            diag.append(
                (False, "%s %s: %s" % (cfg.name, provenance, exc)))
    return (diag, configs)


def _check_outlet(
    workspace: Path, gate_data: dict, env: Mapping[str, str],
    configs: list,
) -> tuple[bool, str]:
    from code_forge.outlet_resolver import resolve_outlet

    gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
    try:
        outlet = resolve_outlet(
            env, gate_yaml_path, cli_value=None, configs=configs,
            has_explicit_backend=False, reachability_fn=None,
        )
    except Exception as exc:
        return (False, str(exc))
    if outlet == "sampling":
        return (
            False,
            "sampling (cannot verify client capability from CLI; "
            "the MCP-side forge_resolve_outlet can). "
            + SAMPLING_REMEDIATION,
        )
    return (True, outlet)


def _check_handshake() -> tuple[bool, str]:
    async def _async():
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        binary = shutil.which("code-forge-mcp")
        cmd = binary or sys.executable
        args = [] if binary else ["-m", "code_forge.mcp_server"]
        params = StdioServerParameters(command=cmd, args=args)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                result = await asyncio.wait_for(
                    session.initialize(), 15)
                name = (result.serverInfo.name
                        if result.serverInfo else "unknown")
                return (True, name)

    try:
        return asyncio.run(_async())
    except ImportError:
        return (False,
                "mcp not installed -- pip install code-forge[mcp]")
    except asyncio.TimeoutError:
        return (False, "handshake timed out after 15s")
    except Exception as exc:
        return (False, str(exc))


def _check_registries(home: Path) -> list[tuple[str, str]]:
    import glob
    import json

    results: list[tuple[str, str]] = []
    for entry in REGISTRY_MAP:
        found = False
        for pattern in entry.paths:
            expanded = os.path.expanduser(pattern)
            for fpath in glob.glob(expanded):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if entry.xml:
                    if "forge" in content or "code-forge" in content:
                        found = True
                        break
                else:
                    try:
                        data = json.loads(content)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    servers = data.get(entry.key, {})
                    if isinstance(servers, dict):
                        for sname, cfg in servers.items():
                            if "forge" in sname.lower():
                                found = True
                                break
                            cmd = ""
                            if isinstance(cfg, dict):
                                cmd = str(cfg.get("command", ""))
                            if ("code-forge" in cmd
                                    or "forge" in cmd):
                                found = True
                                break
                if found:
                    break
            if found:
                break
        results.append((entry.name, "PRESENT" if found else "ABSENT"))
    return results


# -- Orchestrator ----------------------------------------------------------


def run_doctor(
    cwd: Path, env: Mapping[str, str],
) -> int:
    """Run all checks and print diagnostic output. Returns 0 or 1."""
    has_fail = False

    ok_ws, msg_ws, workspace = _check_workspace(cwd, env)
    _line("workspace", msg_ws, ok_ws)
    if not ok_ws:
        has_fail = True
        for label in ("gate.yaml", "trust", "backend", "outlet"):
            _line(label, "", None)
    else:
        ok_gy, msg_gy, gate_data = _check_gate_yaml(workspace)
        _line("gate.yaml", msg_gy, ok_gy)
        if not ok_gy:
            has_fail = True
            for label in ("trust", "backend", "outlet"):
                _line(label, "", None)
        else:
            gate_path = workspace / ".code-forge" / "gate.yaml"
            ok_t, msg_t = _check_trust(gate_path, gate_data)
            _line("trust", msg_t, ok_t)
            if not ok_t:
                has_fail = True

            diag_lines, configs = _check_backends(
                workspace, gate_data, env)
            for ok_b, msg_b in diag_lines:
                _line("backend", msg_b, ok_b)
                if not ok_b:
                    has_fail = True

            has_explicit_outlet = "outlet" in gate_data
            if configs or has_explicit_outlet:
                ok_o, msg_o = _check_outlet(
                    workspace, gate_data, env, configs)
                _line("outlet", msg_o, ok_o)
                if not ok_o:
                    has_fail = True
            else:
                _line("outlet", "", None)
                has_fail = True

    # Always run (never short-circuited)
    ok_h, msg_h = _check_handshake()
    _line("self-check", msg_h, ok_h)
    if not ok_h:
        has_fail = True

    reg_results = _check_registries(Path(os.path.expanduser("~")))
    print("  registries:")
    for name, status in reg_results:
        print("    %-15s%s" % (name + ":", status))

    print("  ---")
    print("  If a tool is still missing, "
          "run /mcp list in your agent.")

    return 1 if has_fail else 0


def _line(label: str, msg: str, ok: Optional[bool]) -> None:
    """Print one diagnostic line.  ok=None means SKIP."""
    if ok is None:
        tag = "SKIP"
    elif ok:
        tag = "PASS"
    else:
        tag = "FAIL"
    print("  %-13s  %-40s %s" % (label + ":", msg, tag))

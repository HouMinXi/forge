# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <minxi@hou.email>
"""Phase 52: ENV-MANIFEST -- declared environment detection.

Detects project lockfiles (poetry.lock, Pipfile.lock, requirements.txt,
package-lock.json, pnpm-lock.yaml, Cargo.lock, go.mod) to produce an
EnvManifest describing the declared or observed dependency environment.
Falls back to toolchain probes when no lockfile is found, and to ABSENT
when neither is available.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ManifestTier(str, Enum):
    """Classification of environment knowledge quality."""

    DECLARED = "declared"
    OBSERVED = "observed"
    ABSENT = "absent"


@dataclass
class EnvManifest:
    """Declared or observed environment snapshot."""

    tier: ManifestTier
    runtime: str = ""
    runtime_name: str = ""
    runtime_version: str = ""
    runtime_bin: str = ""
    manifest_path: Optional[str] = None
    manifest_format: Optional[str] = None
    dependencies: dict[str, str] = field(default_factory=dict)
    raw_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dictionary."""
        d: dict[str, Any] = {
            "tier": self.tier.value,
            "runtime": self.runtime,
            "dependencies": self.dependencies,
            "raw_summary": self.raw_summary,
        }
        if self.runtime_name:
            d["runtime_name"] = self.runtime_name
        if self.runtime_version:
            d["runtime_version"] = self.runtime_version
        if self.runtime_bin:
            d["runtime_bin"] = self.runtime_bin
        if self.manifest_path:
            d["manifest_path"] = self.manifest_path
        if self.manifest_format:
            d["manifest_format"] = self.manifest_format
        return d

    def to_prompt_block(self) -> str:
        """Render a markdown block suitable for LLM prompt injection."""
        if self.tier == ManifestTier.ABSENT:
            return (
                "## Declared Environment\n"
                "Manifest Tier: absent\n"
                "No declared or observed environment lockfile found."
            )
        lines = [
            "## Declared Environment",
            "Manifest Tier: %s" % self.tier.value,
        ]
        if self.runtime:
            lines.append("Runtime: %s" % self.runtime)
        if self.dependencies:
            lines.append("Dependencies:")
            items = sorted(self.dependencies.items())
            limit = 50
            for pkg, ver in items[:limit]:
                lines.append("- %s: %s" % (pkg, ver))
            if len(items) > limit:
                lines.append("- ... and %d more dependencies" % (len(items) - limit))
        return "\n".join(lines)


# -- Lockfile parsers -------------------------------------------------------


def _parse_poetry_lock(path: Path) -> dict[str, str]:
    """Parse poetry.lock (TOML) -> {package: version}."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    deps: dict[str, str] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name", "")
        version = pkg.get("version", "")
        if name:
            deps[name] = version
    return deps


def _parse_pipfile_lock(path: Path) -> dict[str, str]:
    """Parse Pipfile.lock (JSON) -> {package: version}."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    deps: dict[str, str] = {}
    for section in ("default", "develop"):
        for pkg, meta in data.get(section, {}).items():
            ver = meta.get("version", "")
            deps[pkg] = ver.lstrip("=") if ver else ""
    return deps


def _parse_requirements_txt(path: Path) -> dict[str, str]:
    """Parse requirements.txt -> {package: version_spec}."""
    deps: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            if sep in line:
                pkg, ver = line.split(sep, 1)
                deps[pkg.strip()] = sep + ver.strip()
                break
        else:
            deps[line] = "*"
    return deps


def _parse_package_lock_json(path: Path) -> dict[str, str]:
    """Parse package-lock.json -> {package: version}."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    deps: dict[str, str] = {}
    for pkg_path, meta in data.get("packages", {}).items():
        if not pkg_path:
            continue
        name = pkg_path.rsplit("node_modules/", 1)[-1]
        deps[name] = meta.get("version", "")
    if not deps:
        for pkg, meta in data.get("dependencies", {}).items():
            deps[pkg] = meta.get("version", "")
    return deps


def _parse_pnpm_lock_yaml(path: Path) -> dict[str, str]:
    """Parse pnpm-lock.yaml -> {package: version}."""
    deps: dict[str, str] = {}
    in_packages = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.rstrip()
        if stripped == "packages:":
            in_packages = True
            continue
        if in_packages:
            if stripped and not stripped.startswith(" "):
                in_packages = False
                continue
            entry = stripped.strip()
            head = entry.split(":", 1)[0].strip("'\"")
            if head.startswith("/"):
                head = head[1:]
            if "(" in head:
                head = head.split("(", 1)[0]
            at_idx = head.rfind("@")
            if at_idx > 0:
                name = head[:at_idx]
                version = head[at_idx + 1 :]
                deps[name] = version
    return deps


def _parse_cargo_lock(path: Path) -> dict[str, str]:
    """Parse Cargo.lock (TOML) -> {package: version}."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    deps: dict[str, str] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name", "")
        version = pkg.get("version", "")
        if name:
            deps[name] = version
    return deps


def _parse_go_mod(path: Path) -> tuple[str, dict[str, str]]:
    """Parse go.mod -> (go_version, {module: version})."""
    runtime = ""
    deps: dict[str, str] = {}
    in_require = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("go ") and not runtime:
            parts = line.split(None, 1)
            runtime = "go " + parts[1] if len(parts) > 1 else ""
            continue
        if line.startswith("require ("):
            in_require = True
            continue
        if in_require:
            if line == ")":
                in_require = False
                continue
            parts = line.split()
            if len(parts) >= 2:
                deps[parts[0]] = parts[1]
            elif len(parts) == 1:
                deps[parts[0]] = ""
        elif line.startswith("require "):
            parts = line.split()
            if len(parts) >= 3:
                deps[parts[1]] = parts[2]
            elif len(parts) == 2:
                deps[parts[1]] = ""
    return runtime, deps


# -- Lockfile detection order -----------------------------------------------

_LOCKFILE_DETECTORS: list[tuple[str, str, Any]] = [
    ("poetry.lock", "poetry", _parse_poetry_lock),
    ("Pipfile.lock", "pipfile", _parse_pipfile_lock),
    ("requirements.txt", "requirements", _parse_requirements_txt),
    ("package-lock.json", "npm", _parse_package_lock_json),
    ("pnpm-lock.yaml", "pnpm", _parse_pnpm_lock_yaml),
    ("Cargo.lock", "cargo", _parse_cargo_lock),
    ("go.mod", "go", _parse_go_mod),
]

_PYTHON_PROBES = ["python3", "python"]
_NODE_PROBES = ["node"]
_GO_PROBES = ["go"]
_RUST_PROBES = ["rustc"]


def _probe_toolchain() -> tuple[str, str, str, str, dict[str, str]]:
    """Probe installed toolchains via subprocess.

    Returns (runtime, runtime_name, runtime_version, runtime_bin, deps).
    """
    for cmd in _PYTHON_PROBES:
        try:
            r = subprocess.run(
                [cmd, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.0,
            )
            if r.returncode == 0 and r.stdout.strip():
                out = r.stdout.strip()
                ver = out.split()[1] if len(out.split()) > 1 else out
                return (out.lower(), "python", ver, cmd, {})
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            continue

    for cmd in _NODE_PROBES:
        try:
            r = subprocess.run(
                [cmd, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.0,
            )
            if r.returncode == 0 and r.stdout.strip():
                out = r.stdout.strip()
                ver = out.lstrip("v")
                return ("node %s" % out, "node", ver, cmd, {})
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            continue

    for cmd in _GO_PROBES:
        try:
            r = subprocess.run(
                [cmd, "version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.0,
            )
            if r.returncode == 0 and r.stdout.strip():
                out = r.stdout.strip()
                parts = out.split()
                ver = parts[2].lstrip("go") if len(parts) >= 3 else out
                return (out, "go", ver, cmd, {})
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            continue

    for cmd in _RUST_PROBES:
        try:
            r = subprocess.run(
                [cmd, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.0,
            )
            if r.returncode == 0 and r.stdout.strip():
                out = r.stdout.strip()
                parts = out.split()
                ver = parts[1] if len(parts) >= 2 else out
                return (out, "rust", ver, cmd, {})
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            continue

    return ("", "", "", "", {})


# -- Public API -------------------------------------------------------------


def extract_manifest(cwd: Path) -> EnvManifest:
    """Detect and parse an environment manifest from *cwd*.

    Tries lockfiles in priority order (DECLARED tier), then falls back
    to toolchain probes (OBSERVED tier), then returns ABSENT.
    """
    for filename, fmt, parser in _LOCKFILE_DETECTORS:
        lockfile = cwd / filename
        if not lockfile.is_file():
            continue
        try:
            runtime = ""
            runtime_name = ""
            runtime_version = ""
            runtime_bin = ""
            if filename == "go.mod":
                runtime, deps = parser(lockfile)
                runtime_name = "go"
                if runtime.startswith("go "):
                    runtime_version = runtime.split()[1]
                    runtime_bin = "go"
            else:
                deps = parser(lockfile)
                if fmt in ("poetry", "pipfile", "requirements"):
                    runtime_name = "python"
                elif fmt in ("npm", "pnpm"):
                    runtime_name = "node"
                elif fmt == "cargo":
                    runtime_name = "rust"

            return EnvManifest(
                tier=ManifestTier.DECLARED,
                runtime=runtime,
                runtime_name=runtime_name,
                runtime_version=runtime_version,
                runtime_bin=runtime_bin,
                manifest_path=filename,
                manifest_format=fmt,
                dependencies=deps,
                raw_summary="%s (%d deps)" % (filename, len(deps)),
            )
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError, OSError, Exception):
            continue

    # Fallback: toolchain probes
    try:
        runtime, r_name, r_ver, r_bin, deps = _probe_toolchain()
        if runtime:
            return EnvManifest(
                tier=ManifestTier.OBSERVED,
                runtime=runtime,
                runtime_name=r_name,
                runtime_version=r_ver,
                runtime_bin=r_bin,
                dependencies=deps,
                raw_summary="observed: %s" % runtime,
            )
    except Exception:
        pass

    return EnvManifest(tier=ManifestTier.ABSENT)

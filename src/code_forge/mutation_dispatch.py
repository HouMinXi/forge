"""Pick a mutation adapter from a changed-file path.

Review used to keep only ``.py`` and hand the rest to mutmut. The
engines already exist; this map is the missing wire. A suffix with no
entry means the gate skips that file and says so, instead of claiming
the whole run is a Python-only MVP.
"""

from __future__ import annotations

# Suffixes are lowercase, no leading dot. Adapter ids match the registry
# in mutation_engines.adapters, plus ps-mutant (PSMutant + Pester).
_BY_SUFFIX: dict[str, str] = {
    "py": "python-mutmut",
    "js": "js-stryker",
    "mjs": "js-stryker",
    "cjs": "js-stryker",
    "jsx": "js-stryker",
    "ts": "js-stryker",
    "tsx": "js-stryker",
    "mts": "js-stryker",
    "cts": "js-stryker",
    "go": "go-gremlins",
    "rs": "rust-cargo-mutants",
    "ps1": "ps-mutant",
    "psm1": "ps-mutant",
    "psd1": "ps-mutant",
    "c": "c-mull",
    "cc": "c-mull",
    "cpp": "c-mull",
    "cxx": "c-mull",
    "h": "c-mull",
    "hh": "c-mull",
    "hpp": "c-mull",
    "hxx": "c-mull",
}


def adapter_for_path(path: str) -> str | None:
    """Return the adapter id that owns this path, or None."""
    name = path.rsplit("/", 1)[-1].lower()
    if "." not in name:
        return None
    suffix = name.rsplit(".", 1)[-1]
    return _BY_SUFFIX.get(suffix)


def group_by_adapter(paths: list[str]) -> dict[str, list[str]]:
    """Bucket paths by adapter. Paths with no adapter are under \"\"."""
    grouped: dict[str, list[str]] = {}
    for path in paths:
        key = adapter_for_path(path) or ""
        grouped.setdefault(key, []).append(path)
    return grouped


def dispatch_label(paths: list[str]) -> str:
    """One line naming which adapters the diff actually selected."""
    grouped = group_by_adapter(paths)
    owned = sorted(key for key in grouped if key)
    skipped = len(grouped.get("", ()))
    if not owned and skipped:
        return "no registered mutation adapter for %d file(s)" % skipped
    parts = ["%s (%d)" % (key, len(grouped[key])) for key in owned]
    if skipped:
        parts.append("unmapped (%d)" % skipped)
    return "mutation adapters: " + ", ".join(parts)


def adapters_for_files(paths: list[str]) -> tuple:
    """Registry objects for the languages in this diff, in first-seen order.

    Unmapped paths are dropped. The caller probes each object; this
    function does not run a tool.
    """
    from code_forge.mutation_engines.adapters import get_adapter

    chosen = []
    seen: set[str] = set()
    for path in paths:
        adapter_id = adapter_for_path(path)
        if not adapter_id or adapter_id in seen:
            continue
        seen.add(adapter_id)
        chosen.append(get_adapter(adapter_id))
    return tuple(chosen)


def review_gate_summary(paths: list[str]) -> str:
    """Comma-separated adapter ids the review gate selected, first-seen order."""
    return ", ".join(adapter.id for adapter in adapters_for_files(paths))


# Binary the review gate looks up before it will claim an adapter ran.
# python-mutmut stays on the existing mutmut path and is not listed here.
_TOOL = {
    "js-stryker": "stryker",
    "go-gremlins": "gremlins",
    "rust-cargo-mutants": "cargo-mutants",
    "ps-mutant": "pwsh",
    "c-mull": "mull-runner-22",
}


class ToolRow:
    """Whether the binary for one selected adapter is on PATH."""

    def __init__(self, adapter_id: str, tool: str, present: bool) -> None:
        self.adapter_id = adapter_id
        self.tool = tool
        self.present = present


def tool_status(paths: list[str]) -> tuple[ToolRow, ...]:
    """Look up each non-Python adapter's binary. Does not run it.

    A missing binary is reported as present=False. Callers turn that
    into a named skip. This function never returns a mutation score.
    """
    import shutil

    rows = []
    for adapter in adapters_for_files(paths):
        tool = _TOOL.get(adapter.id)
        if tool is None:
            continue
        rows.append(ToolRow(adapter.id, tool, shutil.which(tool) is not None))
    return tuple(rows)


def other_adapter_note(paths: list[str], root=None) -> str:
    """One line for a diff mutmut will not run.

    Names the adapters, whether each tool binary is on PATH, and the
    probe state. When root is given, also records invoke for adapters
    whose probe is available. Does not claim a mutation score.
    """
    tools = ", ".join(
        "%s %s" % (row.tool, "present" if row.present else "missing") for row in tool_status(paths)
    )
    text = "mutation adapters: %s; %s; %s; python mutmut not applicable" % (
        review_gate_summary(paths),
        tools,
        probe_note(paths),
    )
    if root is None:
        return text
    scanned = "scanned %s" % root
    return text + "; " + run_note(paths, root) + "; " + scanned


def _probe_context():
    """Smallest context probe will accept. The review gate has no sandbox."""
    from code_forge.mutation_engines.adapters.base import ExecutionContext

    return ExecutionContext(
        run_id="review-gate",
        config_digest="review",
        execution_policy_digest="review",
        toolchain_fingerprint="review",
        cgroup_root="",
        state_root=".",
        approved_python="/usr/bin/python3",
        memory_mb=256,
        pids=32,
        workspace_mb=64,
        process_headroom_mb=64,
    )


def _probe_target(adapter_id: str):
    from code_forge.mutation_engines.reconcile import undeclared_target

    base = undeclared_target()
    return type(base)(
        id=base.id,
        adapter=adapter_id,
        root=base.root,
        sources=base.sources,
        tests=base.tests,
        inputs=base.inputs,
        oracle=base.oracle,
        command=base.command,
        execution_profile=base.execution_profile,
        environment=base.environment,
        budget=base.budget,
    )


def probe_note(paths: list[str]) -> str:
    """Call probe on every selected adapter and report the state.

    Does not call run, so it never invents a mutation score.
    """
    import subprocess

    parts = []
    context = _probe_context()
    for adapter in adapters_for_files(paths):
        if adapter.id == "python-mutmut":
            continue
        version = _tool_version(adapter.id, subprocess.run)
        if version is None:
            report = adapter.probe(_probe_target(adapter.id), context)
            parts.append("%s %s" % (adapter.id, report.state.value))
            continue
        expected = _EXPECTED_VERSION.get(adapter.id)
        state = "available" if version == expected else "unsupported_version"
        parts.append("%s %s" % (adapter.id, state))
    return "mutation probe: " + ", ".join(parts)


def invoke_tool(argv: list[str], reason: str):
    """Run one tool and return an empty score. The reason says why."""
    import subprocess
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class InvokeResult:
        outcomes: tuple
        reason: str

    subprocess.run(argv, check=False, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return InvokeResult((), reason)


def run_note(paths: list[str], root) -> str:
    """Invoke adapters whose probe is available. Never invents a score."""
    import subprocess

    parts = []
    context = _probe_context()
    for adapter in adapters_for_files(paths):
        if not hasattr(adapter, "invoke"):
            continue
        version = _tool_version(adapter.id, subprocess.run)
        if version is None:
            report = adapter.probe(_probe_target(adapter.id), context)
            ready = report.state.value == "available"
        else:
            ready = version == _EXPECTED_VERSION.get(adapter.id)
        if not ready:
            continue
        result = adapter.invoke(root)
        parts.append("%s %s" % (adapter.id, result.reason))
    return "mutation run: " + ", ".join(parts)


_VERSION_ARGV = {
    "go-gremlins": ("gremlins", "--version"),
    "rust-cargo-mutants": ("cargo", "mutants", "--version"),
    "js-stryker": ("stryker", "--version"),
}

_EXPECTED_VERSION = {
    "go-gremlins": "0.6.0",
    "rust-cargo-mutants": "27.1.0",
    "js-stryker": "10.0.0",
}


def _tool_version(adapter_id: str, run) -> str | None:
    """Read a version from the tool's own output. None when this adapter
    has no PATH probe yet, so the caller falls back to adapter.probe.
    """
    argv = _VERSION_ARGV.get(adapter_id)
    if argv is None:
        return None
    import shutil

    binary = shutil.which(argv[0])
    if binary is None:
        return None
    out = run([binary, *argv[1:]], capture_output=True, text=True, encoding="utf-8", timeout=30)
    text = (out.stdout or "") + (out.stderr or "")
    words = text.split()
    for token in words:
        if token[:1].isdigit():
            return token
    return words[-1] if words else "unreadable"

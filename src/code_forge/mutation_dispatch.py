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

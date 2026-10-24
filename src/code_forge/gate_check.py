# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""gate-check subcommand: test-based commit gate.

Parses .code-forge/gate.yaml, runs the configured test command, translates
exit codes, and blocks on new failures vs a baseline.

run_gate_check returns ONLY 0 or 1, NEVER 2 (EXIT_CLI_ERROR).
If it returned 2, the pre-commit hook's exit-code translation would
treat 2 as "allow+warn", causing FAIL-OPEN on config errors.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import IO, Mapping, Optional

import yaml

from .exit_codes import EXIT_FAIL, EXIT_PASS
from ._gate_pytest import (
    child_sigint_options,
    compare_failed_nodes,
    prepare_pytest_capture,
    read_pytest_evidence,
    run_captured_pytest,
)
from .mutation_config import validate_mutation_timeout


# Known test runners for command safety validation
KNOWN_RUNNERS = {
    "python3",
    "python",
    "pytest",
    "cargo",
    "go",
    "make",
    "npm",
    "npx",
    "node",
}

# Shell metacharacters that must not appear in command args
SHELL_METACHARACTERS = set("|;&$><`")

# pytest prints this banner only when a run was actually interrupted.
# A failed collection also exits 2 but never produces it, and merely
# naming the exception in a traceback must not count.
_INTERRUPT_BANNER = re.compile(r"!{3,}\s*KeyboardInterrupt\s*!{3,}")


def load_gate_config(
    config_path: str | Path,
    fs_open=open,
) -> dict:
    """Load and validate gate.yaml config.

    Args:
        config_path: path to gate.yaml
        fs_open: file open callable (injected for testing)

    Returns:
        dict with validated test config

    Raises:
        FileNotFoundError: if config_path does not exist
        ValueError: if YAML is malformed or required fields missing
    """
    try:
        with fs_open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise
    except yaml.YAMLError as e:
        raise ValueError("Invalid YAML in gate.yaml: %s" % e) from e

    if not isinstance(data, dict) or "test" not in data:
        raise ValueError(
            "gate.yaml needs an active 'test' section for the commit gate. "
            "Add:\n"
            "test:\n"
            "  command: [pytest, -q]   # change to your test runner\n"
            "  timeout_seconds: 900"
        )

    test = data["test"]
    if not isinstance(test, dict):
        raise ValueError("'test' section must be a mapping")  # noqa: TRY004  # callers and tests catch ValueError for config shape

    # Validate required fields
    if "command" not in test:
        raise ValueError("'test.command' is required")
    if not isinstance(test["command"], list):
        raise ValueError("'test.command' must be a list")  # noqa: TRY004  # callers and tests catch ValueError for config shape
    if not test["command"]:
        raise ValueError("'test.command' cannot be empty")
    for arg in test["command"]:
        if not isinstance(arg, str) or isinstance(arg, bool):
            raise ValueError("'test.command' elements must be strings")  # noqa: TRY004  # callers and tests catch ValueError for config shape

    # Optional fields with defaults
    if "env" in test and not isinstance(test.get("env"), dict):
        raise ValueError("'test.env' must be a mapping if present")

    if "timeout_seconds" in test:
        if not isinstance(test["timeout_seconds"], int):
            raise ValueError("'test.timeout_seconds' must be an integer")
        if test["timeout_seconds"] <= 0:
            raise ValueError("'test.timeout_seconds' must be positive")

    if "cwd" in test and not isinstance(test["cwd"], str):
        raise ValueError("'test.cwd' must be a string if present")

    if "source_patterns" in test:
        if not isinstance(test["source_patterns"], list):
            raise ValueError("'test.source_patterns' must be a list if present")

    if "also_copy" in test:
        entries = test["also_copy"]
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise ValueError("'test.also_copy' must be a list of strings")

    if "mutation_skip_globs" in test:
        entries = test["mutation_skip_globs"]
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise ValueError("'test.mutation_skip_globs' must be a list of strings")

    if "mutation_include_globs" in test:
        entries = test["mutation_include_globs"]
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise ValueError("'test.mutation_include_globs' must be a list of strings")

    if "mutation_max_children" in test:
        children = test["mutation_max_children"]
        if not isinstance(children, int) or isinstance(children, bool) or children < 1:
            raise ValueError("'test.mutation_max_children' must be a positive integer")

    if "mutation_memory_limit_mb" in test:
        mem_mb = test["mutation_memory_limit_mb"]
        if not isinstance(mem_mb, int) or isinstance(mem_mb, bool) or mem_mb < 1:
            raise ValueError("'test.mutation_memory_limit_mb' must be a positive integer (MiB)")

    if "mutation_timeout_seconds" in test:
        validate_mutation_timeout(test["mutation_timeout_seconds"], "test.mutation_timeout_seconds")

    # Validate optional non_ascii field (top-level)
    if "non_ascii" in data:
        if data["non_ascii"] not in ("ai-smell", "strict"):
            raise ValueError("non_ascii must be 'ai-smell' or 'strict', got: %r" % data["non_ascii"])

    # Validate optional presubmit section
    if "presubmit" in data:
        if not isinstance(data["presubmit"], list):
            raise ValueError(
                "gate.yaml 'presubmit' must be a list, got: %s" % type(data["presubmit"]).__name__
            )
        for idx, entry in enumerate(data["presubmit"]):
            try:
                validate_presubmit_entry(entry)
            except ValueError as e:
                raise ValueError("presubmit[%d]: %s" % (idx, e)) from e

    # Validate optional graph_triage section
    if "graph_triage" in data:
        validate_graph_triage(data["graph_triage"])

    # Validate optional daemon_state section
    if "daemon_state" in data:
        validate_daemon_state(data["daemon_state"])

    # Validate optional siblings section (cross-repo review)
    if "siblings" in data:
        validate_siblings(
            data["siblings"],
            gate_yaml_dir=Path(str(config_path)).parent,
        )

    # Validate optional canary section (reviewer laziness check)
    if "canary" in data:
        validate_canary_config(data["canary"])

    # Validate optional retry section (HTTP-level retry for API backends)
    if "retry" in data:
        validate_retry_config(data["retry"])

    # Validate optional ledger section (eval-on-duty kill-switch)
    if "ledger" in data:
        validate_ledger_config(data["ledger"])

    # Validate optional pinned_paths (44-03 Task 2: path-based suppression)
    if "pinned_paths" in data:
        validate_pinned_paths(data["pinned_paths"])

    # Validate optional style_downgrade (44-03 Task 2: style downgrade config)
    if "style_downgrade" in data:
        validate_style_downgrade_config(data["style_downgrade"])

    # Validate optional exec_falsify block (Phase 53a EXEC-FALSIFY v1)
    if "exec_falsify" in data:
        validate_exec_falsify_config(data["exec_falsify"])

    return data


def config_root_is_outside_review(config_root, source_root) -> bool:
    """True when the gate config root and the reviewed tree are disjoint."""
    import os

    config = Path(os.path.abspath(config_root))
    source = Path(os.path.abspath(source_root))
    return config != source and config not in source.parents and source not in config.parents


class UntrustedGateConfigError(ValueError):
    """The in-tree gate.yaml cannot be trusted for review-time ``test.*`` reads."""


class OutsideConfigRootRefused(UntrustedGateConfigError):
    """An explicit outside test config root failed a trust check (#157).

    Unlike the in-tree refusals (an untracked gate in CI, a change-added
    gate), the operator configured this root, so CI treats a refusal as a
    failure (nothing measured) rather than a skipped phase."""


_GATE_PATHS = (".code-forge", ".code-forge/gate.yaml")
_LINK_MODES = {"120000": "a symlink", "160000": "a submodule"}


def _git_out(source_root, *args) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(source_root), capture_output=True, text=True,
            encoding="utf-8", errors="surrogateescape", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UntrustedGateConfigError(f"cannot inspect .code-forge/gate.yaml with git: {exc}") from exc


def _git_ok(source_root, *args) -> subprocess.CompletedProcess:
    """_git_out that fails closed (#144): a listing git could not produce is
    not an empty listing, so any non-zero exit makes the gate untrusted."""
    result = _git_out(source_root, *args)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise UntrustedGateConfigError(
            f"git {' '.join(a for a in args if a != '--')} failed (exit {result.returncode})"
            + (f": {detail[-1]}" if detail else "")
            + "; cannot verify .code-forge/gate.yaml, refusing it"
        )
    return result


def _gate_entries(listing: str) -> dict[str, str]:
    """{path: mode} for the gate path components in ls-files -s / ls-tree output."""
    entries = {}
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        if path in _GATE_PATHS and meta.split():
            entries[path] = meta.split()[0]
    return entries


def _refuse_linked_gate(source_root, base_sha: str | None) -> bool:
    """Refuse a symlink or submodule at .code-forge or .code-forge/gate.yaml
    (index, baseline tree or filesystem), and a gate path whose realpath
    leaves the checkout or lands elsewhere. Returns whether the gate file is
    tracked in the index as a regular file."""
    root = Path(source_root)
    index = _git_ok(root, "ls-files", "-s", "--", *_GATE_PATHS)
    listings = [("index", _gate_entries(index.stdout))]
    if base_sha:
        tree = _git_ok(root, "ls-tree", base_sha, "--", *_GATE_PATHS)
        listings.append(("review baseline", _gate_entries(tree.stdout)))
    for where, entries in listings:
        for path, mode in entries.items():
            if mode in _LINK_MODES:
                raise UntrustedGateConfigError(
                    f"{path} is {_LINK_MODES[mode]} in the {where}; review-time test config is "
                    "only read from a regular .code-forge/gate.yaml"
                )
    for rel in _GATE_PATHS:
        if os.path.islink(root / rel):
            raise UntrustedGateConfigError(
                f"{rel} is a symlink in the checkout; review-time test config is only read "
                "from a regular .code-forge/gate.yaml"
            )
    real = os.path.relpath(os.path.realpath(root / ".code-forge" / "gate.yaml"), os.path.realpath(root))
    if Path(real).as_posix() != ".code-forge/gate.yaml":
        raise UntrustedGateConfigError(
            f".code-forge/gate.yaml resolves to {real}, outside its own path in the checkout"
        )
    index_entries = listings[0][1]
    return index_entries.get(".code-forge/gate.yaml", "").startswith("100")


def _read_gate_nofollow(root, *, refuse_hardlinks: bool = False) -> str:
    """Read ``<root>/.code-forge/gate.yaml`` without following a symlink at
    either component: ``.code-forge`` is opened dir-relative with O_NOFOLLOW
    and gate.yaml likewise, then fstat'd as a regular file. That closes the
    check-then-open race of an islink/realpath test followed by open().

    ``refuse_hardlinks`` (an outside config root): refuse an opened file with
    ``st_nlink > 1``. A hardlink of a checkout file -- tracked, untracked,
    ignored, in a nested repo or a submodule (#145, #147) -- always has a
    second link, so no listing or walk of the checkout is needed (#149).
    Symlinks and realpath catch the other lead-backs; directories cannot be
    hardlinked."""
    import errno
    import stat

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    fds = []
    try:
        try:
            fds.append(os.open(str(root), os.O_RDONLY | directory))
            fds.append(os.open(".code-forge", os.O_RDONLY | directory | nofollow, dir_fd=fds[-1]))
            fds.append(os.open("gate.yaml", os.O_RDONLY | nofollow, dir_fd=fds[-1]))
        except OSError as exc:
            if getattr(exc, "errno", None) in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
                raise UntrustedGateConfigError(
                    f"{root}/.code-forge/gate.yaml: .code-forge or gate.yaml is a symlink or not a "
                    "regular path; review-time test config is only read from a regular file"
                ) from exc
            raise
        info = os.fstat(fds[-1])
        if not stat.S_ISREG(info.st_mode):
            raise UntrustedGateConfigError(f"{root}/.code-forge/gate.yaml is not a regular file")
        if refuse_hardlinks and info.st_nlink > 1:
            raise UntrustedGateConfigError(
                f"{root}/.code-forge/gate.yaml has {info.st_nlink} hard links; an outside test "
                "config root must hold its own single-link gate.yaml (a hardlink may be a file "
                "in the reviewed checkout)"
            )
        with os.fdopen(fds.pop(), "r", encoding="utf-8") as handle:
            return handle.read()
    finally:
        for fd in fds:
            os.close(fd)


def _parse_validated(label, text: str) -> dict:
    import io

    return load_gate_config(label, fs_open=lambda *_a, **_k: io.StringIO(text))


def _parse_raw(label, text: str) -> dict:
    """Top-level gate.yaml mapping without the ``test`` section requirement
    (#141: review-time keys such as ``ledger`` or ``rulepacks_blocking``)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {label}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping, got {type(data).__name__}")  # noqa: TRY004
    return data


def _load_gate_nofollow(root, *, refuse_hardlinks: bool = False, parse=_parse_validated) -> dict:
    text = _read_gate_nofollow(root, refuse_hardlinks=refuse_hardlinks)
    return parse(Path(root) / ".code-forge" / "gate.yaml", text)


def _refuse_checkout_alias(root: Path, source_root) -> None:
    """#153: refuse a config root that is the checkout under another path.
    A bind mount is neither a symlink nor a hardlink, so realpath and nlink
    miss it. Compare (st_dev, st_ino) of the checkout root and its
    .code-forge with the config root, every parent of it, and its
    .code-forge.

    Residual (needs mount control to set up, out of scope): some *other*
    checkout subdirectory bind-mounted onto the outside root; detecting that
    would need a walk of the checkout."""
    # #155: resolve source_root (a symlinked source_root would otherwise record
    # the link, not the checkout), then lstat its entries (#157). The config
    # side follows: an outside root's .code-forge symlink is refused earlier.
    checkout = Path(source_root).resolve()
    checkout_ids = set()
    for rel in ("", ".code-forge"):
        try:
            # #157: lstat the resolved checkout's own entries. Following a
            # PR-committed .code-forge symlink would record its target (e.g.
            # the operator's outside config) and refuse a legitimate root.
            info = os.stat(checkout / rel, follow_symlinks=False)
        except OSError:
            continue
        checkout_ids.add((info.st_dev, info.st_ino))
    resolved = Path(root).resolve()
    for directory in (resolved, *resolved.parents):
        try:
            info = os.stat(directory)
        except OSError:
            continue
        if (info.st_dev, info.st_ino) in checkout_ids:
            raise UntrustedGateConfigError(
                f"test config root {root} is the reviewed checkout under another path ({directory} "
                "is the same directory, e.g. a bind mount alias)"
            )
    try:
        info = os.stat(resolved / ".code-forge")
    except OSError:
        return
    if (info.st_dev, info.st_ino) in checkout_ids:
        raise UntrustedGateConfigError(
            f"test config root {root}: its .code-forge is the reviewed checkout's (bind mount alias)"
        )


def _load_outside_gate(config_root, source_root, parse=_parse_validated) -> dict:
    """An explicit config root outside the checkout: refuse a symlinked
    .code-forge/gate.yaml or one whose real path lands inside the checkout,
    refuse a gate.yaml with more than one hard link, and read with O_NOFOLLOW.

    Directories cannot be hardlinked on Linux (link(2) returns EPERM), so a
    .code-forge directory hardlink is not a case. No git call and no walk of
    the checkout: the nlink check covers every hardlink location."""
    root = Path(config_root)
    for rel in (".code-forge", ".code-forge/gate.yaml"):
        if os.path.islink(root / rel):
            raise UntrustedGateConfigError(
                f"{root / rel} is a symlink; an outside test config root must hold a regular "
                ".code-forge/gate.yaml"
            )
    real = Path(os.path.realpath(root / ".code-forge" / "gate.yaml"))
    checkout = Path(os.path.realpath(source_root))
    if real == checkout or checkout in real.parents:
        raise UntrustedGateConfigError(
            f"test config root {root} resolves into the reviewed checkout ({real})"
        )
    _refuse_checkout_alias(root, source_root)
    return _load_gate_nofollow(root, refuse_hardlinks=True, parse=parse)


def load_trusted_gate_data(
    *, config_root, source_root, base_sha: str | None, allow_untracked: bool = True,
) -> dict:
    """#141: the raw top-level gate.yaml mapping from the same trusted source
    as :func:`load_trusted_gate_config` (outside root, review baseline, or a
    LOCAL untracked file), without demanding a ``test`` section. Raises the
    same errors; callers of review-time switches treat FileNotFoundError and
    UntrustedGateConfigError as "no trusted gate.yaml" (built-in defaults)."""
    return load_trusted_gate_config(
        config_root=config_root, source_root=source_root, base_sha=base_sha,
        allow_untracked=allow_untracked, _parse=_parse_raw,
    )


def load_trusted_gate_config(
    *, config_root, source_root, base_sha: str | None, allow_untracked: bool = True,
    _parse=_parse_validated,
) -> dict:
    """gate.yaml for review-time ``test.*`` reads and mutation globs, from a
    source the reviewed change cannot edit (#140).

    - An explicit config root outside the reviewed tree: its gate.yaml.
    - Otherwise (the CLI wiring: config root == reviewed tree): the review
      baseline revision's ``.code-forge/gate.yaml`` via ``git show``. A change's
      own edits apply only once on the baseline.
    - Not at the baseline and not tracked by git at all: the checked-out file,
      but only when ``allow_untracked`` (LOCAL). CI refuses it: CI must supply
      the gate from the baseline or an explicit outside config root.

    A symlink or submodule at ``.code-forge`` or ``.code-forge/gate.yaml`` (in
    the index, the baseline tree or the checkout), or a gate path whose
    realpath goes elsewhere, raises UntrustedGateConfigError (a ValueError).
    A tracked gate.yaml with no baseline copy (a first-ever gate.yaml added by
    this change) raises FileNotFoundError; an invalid trusted file raises
    ValueError. Callers already treat these as a missing/invalid config, so
    nothing from the reviewed tree runs.
    """
    if config_root_is_outside_review(config_root, source_root):
        try:
            return _load_outside_gate(config_root, source_root, parse=_parse)
        except OutsideConfigRootRefused:
            raise
        except UntrustedGateConfigError as exc:
            raise OutsideConfigRootRefused(str(exc)) from exc
    working = Path(source_root) / ".code-forge" / "gate.yaml"
    in_git = any((d / ".git").exists() for d in (Path(source_root).resolve(), *Path(source_root).resolve().parents))
    if not in_git:
        if not allow_untracked:
            raise UntrustedGateConfigError(_CI_UNTRACKED)
        # Not a git checkout: nothing is under review control, no git to ask.
        if _parse is _parse_validated:
            return load_gate_config(working)
        with open(working, encoding="utf-8") as handle:
            return _parse(working, handle.read())
    tracked = _refuse_linked_gate(source_root, base_sha)
    if base_sha:
        shown = _git_out(source_root, "show", f"{base_sha}:./.code-forge/gate.yaml")
        if shown.returncode == 0:
            return _parse("<baseline>:.code-forge/gate.yaml", shown.stdout)
    if tracked:
        raise FileNotFoundError(
            f"no .code-forge/gate.yaml at review baseline {(base_sha or 'unknown')[:12]}; a gate.yaml "
            "added by the reviewed change takes effect once it is on the baseline"
        )
    if not allow_untracked:
        raise UntrustedGateConfigError(_CI_UNTRACKED)
    # LOCAL: a gate.yaml git does not track (gitignored or never added) is
    # the user's own setup, not part of the reviewed diff (#143 tracks the
    # WORKING-tree agent risk).
    return _load_gate_nofollow(source_root, parse=_parse)


_CI_UNTRACKED = (
    "CI: .code-forge/gate.yaml is not at the review baseline, and CI does not trust an "
    "untracked in-tree gate.yaml (anything in the checkout may come from the change under "
    "review). Commit gate.yaml so it is on the baseline, or pass --test-config-root DIR "
    "(FORGE_TEST_CONFIG_ROOT) pointing outside the checkout"
)


def validate_exec_falsify_config(section: object) -> None:
    """Validate exec_falsify section of gate.yaml (Phase 53a).

    Schema:
        exec_falsify:
          timeout_seconds: int  # default 120, bounded [10, 1800]

    Raises:
        ValueError: on non-mapping section, non-integer or
            out-of-bounds timeout_seconds, or unknown keys.
    """
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'exec_falsify' must be a mapping, got: %s" % type(section).__name__
        )
    for key in section:
        if key != "timeout_seconds":
            raise ValueError(
                "gate.yaml 'exec_falsify' unknown key: %r (only timeout_seconds is supported)" % key
            )
    if "timeout_seconds" in section:
        ts = section["timeout_seconds"]
        if not isinstance(ts, int) or isinstance(ts, bool):
            raise ValueError("exec_falsify.timeout_seconds must be an integer, got: %r" % (ts,))
        if not (10 <= ts <= 1800):
            raise ValueError("exec_falsify.timeout_seconds must be within [10, 1800], got: %d" % ts)


def validate_pinned_paths(section: object) -> None:
    """Validate pinned_paths section of gate.yaml (44-03 Task 2).

    Schema:
        list[str] of glob patterns. Default: [].

    Raises:
        ValueError: if not a list of strings.
    """
    if not isinstance(section, list):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'pinned_paths' must be a list, got: %s" % type(section).__name__
        )
    for idx, item in enumerate(section):
        if not isinstance(item, str):
            raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
                "gate.yaml 'pinned_paths[%d]' must be a string, got: %s" % (idx, type(item).__name__)
            )


def validate_style_downgrade_config(section: object) -> None:
    """Validate style_downgrade section of gate.yaml (44-03 Task 2).

    Schema:
        pass_names: list[str]  (OPTIONAL, default [])
        keywords:   list[str]  (OPTIONAL, default [])

    Raises:
        ValueError: if known fields have wrong types.
    """
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'style_downgrade' must be a mapping, got: %s" % type(section).__name__
        )
    for key in ("pass_names", "keywords"):
        val = section.get(key, [])
        if not isinstance(val, list):
            raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
                "gate.yaml 'style_downgrade.%s' must be a list, got: %s" % (key, type(val).__name__)
            )
        for idx, item in enumerate(val):
            if not isinstance(item, str):
                raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
                    "gate.yaml 'style_downgrade.%s[%d]' must be a string, got: %s"
                    % (key, idx, type(item).__name__)
                )


def validate_ledger_config(ledger_cfg: object) -> None:
    """Validate optional ledger configuration section in gate.yaml (D-19)."""
    if not isinstance(ledger_cfg, dict):
        raise ValueError("gate.yaml 'ledger' section must be a mapping")  # noqa: TRY004  # callers and tests catch ValueError for config shape
    if "enabled" in ledger_cfg and not isinstance(ledger_cfg["enabled"], bool):
        raise ValueError("gate.yaml 'ledger.enabled' must be a boolean")


def validate_graph_triage(section: dict) -> None:
    """Validate the graph_triage section of gate.yaml.

    Schema:
        enabled:  bool   -- OPTIONAL. Explicit enable/disable.
        db_path:  str    -- OPTIONAL. Path to graph.db override.
        Unknown keys are allowed (forward-compatible).

    Args:
        section: dict from gate.yaml graph_triage key.

    Raises:
        ValueError: if known fields have wrong types.
    """
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'graph_triage' must be a mapping, got: %s" % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError(
                "gate.yaml 'graph_triage.enabled' must be a bool, got: %r" % section["enabled"]
            )
    if "db_path" in section:
        if not isinstance(section["db_path"], str):
            raise ValueError(
                "gate.yaml 'graph_triage.db_path' must be a string, got: %r" % section["db_path"]
            )


def validate_daemon_state(section: object) -> None:
    """Validate the daemon_state section of gate.yaml.

    Schema:
        enabled:         bool         -- OPTIONAL. Explicit enable/disable.
        subsystems:      list[str]    -- OPTIONAL. State domains to focus on.
        patterns:        list[str]    -- OPTIONAL. Extra keyword patterns.
        conflicts:       list[dict]   -- OPTIONAL. Static conflict triplets.
            Each triplet requires: subsystem (str), mutates (str),
            interferes_with (str).
        conflicts_file:  str          -- OPTIONAL. Path to external conflicts YAML.

    Args:
        section: value from gate.yaml daemon_state key.

    Raises:
        ValueError: if known fields have wrong types or required sub-fields missing.
    """
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'daemon_state' must be a mapping, got: %s" % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError(
                "gate.yaml 'daemon_state.enabled' must be a bool, got: %r" % section["enabled"]
            )
    if "subsystems" in section:
        if not isinstance(section["subsystems"], list):
            raise ValueError(
                "gate.yaml 'daemon_state.subsystems' must be a list, got: %s"
                % type(section["subsystems"]).__name__
            )
        for s in section["subsystems"]:
            if not isinstance(s, str):
                raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
                    "'daemon_state.subsystems' elements must be strings"
                )
    if "patterns" in section:
        if not isinstance(section["patterns"], list):
            raise ValueError(
                "gate.yaml 'daemon_state.patterns' must be a list, got: %s"
                % type(section["patterns"]).__name__
            )
    if "conflicts" in section:
        if not isinstance(section["conflicts"], list):
            raise ValueError(
                "gate.yaml 'daemon_state.conflicts' must be a list, got: %s"
                % type(section["conflicts"]).__name__
            )
        for idx, c in enumerate(section["conflicts"]):
            if not isinstance(c, dict):
                raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
                    "daemon_state.conflicts[%d] must be a mapping" % idx
                )
            for req_key in ("subsystem", "mutates", "interferes_with"):
                if req_key not in c:
                    raise ValueError("daemon_state.conflicts[%d] missing required '%s'" % (idx, req_key))
                if not isinstance(c[req_key], str):
                    raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
                        "daemon_state.conflicts[%d].%s must be a string" % (idx, req_key)
                    )
    if "conflicts_file" in section:
        if not isinstance(section["conflicts_file"], str):
            raise ValueError(
                "gate.yaml 'daemon_state.conflicts_file' must be a string, "
                "got: %r" % section["conflicts_file"]
            )


def validate_canary_config(section: object) -> None:
    """Validate the canary section of gate.yaml.

    Schema:
        enabled:          bool          -- OPTIONAL. Activate the canary check.
        n:                int (3..5)    -- OPTIONAL. Number of canary mutations.
        threshold_ratio:  float >0..1.0 -- OPTIONAL. Minimum catch ratio.
        Unknown keys are allowed (forward-compatible).

    Args:
        section: value from gate.yaml canary key.

    Raises:
        ValueError: if known fields have wrong types or values out of range.
    """
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'canary' must be a mapping, got: %s" % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError("gate.yaml 'canary.enabled' must be a bool, got: %r" % section["enabled"])
    if "n" in section:
        if not isinstance(section["n"], int) or isinstance(section["n"], bool):
            raise ValueError("gate.yaml 'canary.n' must be an int, got: %r" % section["n"])
        if section["n"] < 3 or section["n"] > 5:
            raise ValueError("gate.yaml 'canary.n' must be in range 3..5, got: %d" % section["n"])
    if "threshold_ratio" in section:
        val = section["threshold_ratio"]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError("gate.yaml 'canary.threshold_ratio' must be a number, got: %r" % val)
        if val <= 0.0 or val > 1.0:
            raise ValueError(
                "gate.yaml 'canary.threshold_ratio' must be > 0.0 and "
                "<= 1.0 (must be > 0.0..1.0), got: %s" % val
            )


def validate_retry_config(section: object) -> None:
    """Validate the retry section of gate.yaml.

    Schema:
        max_attempts:    int (1..10)          -- OPTIONAL. Max API call attempts.
        initial_delay_s: number (0.1..30)     -- OPTIONAL. Initial retry delay.
        retry_timeout:   bool                 -- OPTIONAL. Retry socket timeouts.
        l1_pass_stagger_s: number (0..120)    -- OPTIONAL. Delay between L1 pass starts.
        Unknown keys are allowed (forward-compatible).

    Args:
        section: value from gate.yaml retry key.

    Raises:
        ValueError: if known fields have wrong types or values out of range.
    """
    if not isinstance(section, dict):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "gate.yaml 'retry' must be a mapping, got: %s" % type(section).__name__
        )
    if "max_attempts" in section:
        val = section["max_attempts"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError("gate.yaml 'retry.max_attempts' must be an int, got: %r" % val)
        if val < 1 or val > 10:
            raise ValueError("gate.yaml 'retry.max_attempts' must be in range 1..10, got: %d" % val)
    if "initial_delay_s" in section:
        val = section["initial_delay_s"]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError("gate.yaml 'retry.initial_delay_s' must be a number, got: %r" % val)
        if val < 0.1 or val > 30:
            raise ValueError("gate.yaml 'retry.initial_delay_s' must be in range 0.1..30, got: %s" % val)
    if "retry_timeout" in section:
        val = section["retry_timeout"]
        if not isinstance(val, bool):
            raise ValueError("gate.yaml 'retry.retry_timeout' must be a bool, got: %r" % val)
    if "l1_pass_stagger_s" in section:
        val = section["l1_pass_stagger_s"]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError("gate.yaml 'retry.l1_pass_stagger_s' must be a number, got: %r" % val)
        if val < 0 or val > 120:
            raise ValueError(
                "gate.yaml 'retry.l1_pass_stagger_s' must be in range 0..120, got: %s" % val
            )


_REF_COMPONENT_RE = re.compile(r"[A-Za-z0-9._/@-]+\Z")


def _validate_ref_part(part_name: str, part_val: str, context: str) -> None:
    """Validate a single git ref component for character safety.

    Rejects leading dash (option injection), leading dot (ambiguous ref),
    and any character outside the git-safe set [A-Za-z0-9._/@-].

    Args:
        part_name: "baseline" or "head" (for error messages).
        part_val: the ref string to validate.
        context: error message prefix (e.g. "siblings[0]:" or "ref_spec").

    Raises:
        ValueError: on invalid characters or leading dash/dot.
    """
    if part_val.startswith("-") or part_val.startswith("."):
        raise ValueError("%s ref %s must not start with '-' or '.'" % (context, part_name))
    if not _REF_COMPONENT_RE.match(part_val):
        raise ValueError("%s ref %s contains invalid characters" % (context, part_name))


def validate_siblings(
    siblings: object,
    gate_yaml_dir: Path,
    primary_language: str | None = None,
) -> None:
    """Validate the siblings section of gate.yaml.

    Checks structural integrity (types, required fields), ref format,
    remote URL rejection (v1 local-only), path traversal via symlink
    guard, label character constraints, reserved-label rejection, and
    label uniqueness after defaulting.

    Args:
        siblings: value from gate.yaml siblings key (expected list).
        gate_yaml_dir: directory containing gate.yaml (used to resolve
            relative repo paths; gate_root = gate_yaml_dir.parent).
        primary_language: if provided, reject siblings whose detected
            language differs (same-stack constraint). Pass None to skip.

    Raises:
        ValueError: on any validation failure.
    """
    if not isinstance(siblings, list):
        raise ValueError("siblings: must be a list")  # noqa: TRY004  # callers and tests catch ValueError for config shape

    from .conventions_resolver import _symlink_guard_passes

    gate_root = gate_yaml_dir.parent
    seen_labels: set[str] = set()

    for idx, entry in enumerate(siblings):
        if not isinstance(entry, dict):
            raise ValueError("siblings[%d]: must be a mapping" % idx)  # noqa: TRY004  # callers and tests catch ValueError for config shape

        # Required fields (must be non-empty strings)
        repo_val = entry.get("repo")
        if not isinstance(repo_val, str) or not repo_val:
            raise ValueError("siblings[%d]: 'repo' is required" % idx)
        ref_val = entry.get("ref")
        if not isinstance(ref_val, str) or not ref_val:
            raise ValueError("siblings[%d]: 'ref' is required" % idx)

        ref = ref_val
        # ref must contain exactly one ".." separator (not "...")
        if ".." not in ref or "..." in ref:
            raise ValueError("siblings[%d]: ref must be 'baseline..head', got %r" % (idx, ref))
        base_part, head_part = ref.split("..", 1)
        if not base_part or not head_part:
            raise ValueError("siblings[%d]: ref must be 'baseline..head', got %r" % (idx, ref))
        _validate_ref_part("baseline", base_part, "siblings[%d]:" % idx)
        _validate_ref_part("head", head_part, "siblings[%d]:" % idx)

        repo_str = repo_val

        # Reject remote URLs (v1 supports local paths only)
        if repo_str.startswith("https://") or repo_str.startswith("git@"):
            raise ValueError("siblings[%d]: remote URLs not supported in v1; use a local path" % idx)

        # Symlink guard: resolve relative to project root
        raw_path = Path(repo_str)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (gate_root / repo_str).resolve()
        if not _symlink_guard_passes(resolved, gate_root):
            raise ValueError("siblings[%d]: repo path traverses outside project" % idx)

        # Label: explicit or defaulted from repo basename
        label = entry.get("label") or os.path.basename(repo_str.rstrip("/"))

        # Label character validation
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
            raise ValueError("siblings[%d]: label must be alphanumeric/hyphen/underscore only" % idx)

        # Reserved label
        if label == "primary":
            raise ValueError("siblings: label 'primary' is reserved")

        # Uniqueness
        if label in seen_labels:
            raise ValueError("siblings: duplicate label '%s'" % label)
        seen_labels.add(label)

        # Same-stack language check (skipped when primary_language is None)
        if primary_language is not None:
            from .detect import detect_toolchain

            result = detect_toolchain(resolved)
            if result.language != primary_language:
                raise ValueError(
                    "siblings[%d]: sibling detected as '%s' but "
                    "primary is '%s'; same-stack only for v1" % (idx, result.language, primary_language)
                )


def fnmatch_to_grep(glob: str) -> str:
    """Convert a shell glob pattern to a POSIX ERE pattern for grep -E.

    Uses a custom converter -- does NOT use fnmatch.translate() which
    produces Python-only constructs incompatible with grep -E.

    Conversion rules:
        *  -> .*
        ?  -> .
        .  -> \\.
        all other chars pass through unchanged

    The result is anchored: ^<converted>$

    Args:
        glob: shell glob pattern (e.g. "*.py", "test_*.py")

    Returns:
        POSIX ERE string suitable for grep -E (e.g. "^.*\\.py$")
    """
    result = []
    for ch in glob:
        if ch == "*":
            result.append(".*")
        elif ch == "?":
            result.append(".")
        elif ch == ".":
            result.append("\\.")
        else:
            result.append(ch)
    return "^" + "".join(result) + "$"


def validate_presubmit_command(command: list[str]) -> None:
    """Validate a presubmit linter command for shell injection safety.

    Checks each element for SHELL_METACHARACTERS. Does NOT check
    KNOWN_RUNNERS -- presubmit commands are user-supplied external
    tools and must not be restricted to the test-runner allowlist.

    Args:
        command: presubmit command list

    Raises:
        ValueError: if any element contains a shell metacharacter
    """
    if not isinstance(command, list):
        raise ValueError("presubmit command must be a list")  # noqa: TRY004  # callers and tests catch ValueError for config shape
    if not command:
        raise ValueError("presubmit command cannot be empty")
    for arg in command:
        if not isinstance(arg, str):
            raise ValueError("presubmit command elements must be strings")  # noqa: TRY004  # callers and tests catch ValueError for config shape
        for char in SHELL_METACHARACTERS:
            if char in arg:
                raise ValueError("Shell metacharacter %r not allowed in presubmit command" % char)
        if "%" in arg:
            raise ValueError(
                "Percent sign not allowed in presubmit command elements"
                " (breaks hook-generation string formatting)"
            )


def validate_presubmit_entry(entry: dict) -> None:
    """Validate a single presubmit entry from gate.yaml.

    Schema:
        command:      list[str] -- REQUIRED. Linter command.
                      Each element checked against SHELL_METACHARACTERS.
        applies_to:   str       -- REQUIRED. Glob pattern.
                      Must NOT contain single-quote or double-quote.
        on:           str       -- REQUIRED. One of "diff" or "patch".
                      "message" is rejected (not yet implemented).
        when_exists:  str       -- OPTIONAL. Activation path.
                      Must NOT contain single-quote or double-quote.

    Stores "applies_to_grep" in entry (POSIX ERE form of applies_to)
    for direct shell use.

    Args:
        entry: dict representing one presubmit list element

    Raises:
        ValueError: if required fields are missing or values are invalid
    """
    if not isinstance(entry, dict):
        raise ValueError("each presubmit entry must be a mapping")  # noqa: TRY004  # callers and tests catch ValueError for config shape

    # command: required, list, no shell metacharacters
    if "command" not in entry:
        raise ValueError("presubmit entry missing required field 'command'")
    if not isinstance(entry["command"], list):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "presubmit entry 'command' must be a list, got: %s" % type(entry["command"]).__name__
        )
    validate_presubmit_command(entry["command"])

    # applies_to: required, string, no quotes (breaks generated shell quoting)
    if "applies_to" not in entry:
        raise ValueError("presubmit entry missing required field 'applies_to'")
    if not isinstance(entry["applies_to"], str):
        raise ValueError(  # noqa: TRY004  # callers and tests catch ValueError for config shape
            "presubmit entry 'applies_to' must be a string"
        )
    if "'" in entry["applies_to"]:
        raise ValueError("presubmit entry 'applies_to' must not contain single-quote")
    if '"' in entry["applies_to"]:
        raise ValueError("presubmit entry 'applies_to' must not contain double-quote")
    if "%" in entry["applies_to"]:
        raise ValueError(
            "presubmit entry 'applies_to' must not contain percent sign"
            " (breaks hook-generation string formatting)"
        )
    # Store the grep-compatible ERE for direct shell use
    entry["applies_to_grep"] = fnmatch_to_grep(entry["applies_to"])

    # on: required, "diff" or "patch" only.
    # YAML 1.1 coerces bare `on:` to boolean True; normalize it to the string
    # "on" so users can write `on: diff` without quoting the key.
    on_key = "on" if "on" in entry else (True if True in entry else None)
    if on_key is None:
        raise ValueError("presubmit entry missing required field 'on'")
    on_value = entry[on_key]
    # Normalize: store as string key for callers
    if on_key is True:
        entry["on"] = on_value
        del entry[True]
    if on_value == "message":
        raise ValueError(
            "presubmit entry 'on: message' not yet supported -- would "
            "silently never execute; use diff or patch"
        )
    if on_value not in ("diff", "patch"):
        raise ValueError("presubmit entry 'on' must be 'diff' or 'patch', got: %r" % on_value)

    # when_exists: optional, string, no quotes
    if "when_exists" in entry:
        we = entry["when_exists"]
        if not isinstance(we, str):
            raise ValueError("presubmit entry 'when_exists' must be a string")
        if "'" in we:
            raise ValueError("presubmit entry 'when_exists' must not contain single-quote")
        if '"' in we:
            raise ValueError("presubmit entry 'when_exists' must not contain double-quote")
        if "%" in we:
            raise ValueError(
                "presubmit entry 'when_exists' must not contain percent sign"
                " (breaks hook-generation string formatting)"
            )


def validate_command_safety(command: list[str]) -> None:
    """Validate test command for safety.

    Args:
        command: test command list

    Raises:
        ValueError: if command is unsafe
    """
    if not command:
        raise ValueError("command cannot be empty")
    if not isinstance(command, list):
        raise ValueError("command must be a list")  # noqa: TRY004  # callers and tests catch ValueError for config shape

    # First element must be a known runner
    if command[0] not in KNOWN_RUNNERS:
        raise ValueError(
            "Unknown test runner: %s (expected one of: %s)"
            % (command[0], ", ".join(sorted(KNOWN_RUNNERS)))
        )

    # No element may contain shell metacharacters
    for arg in command:
        if not isinstance(arg, str) or isinstance(arg, bool):
            raise ValueError("command elements must be strings")  # noqa: TRY004  # callers and tests catch ValueError for config shape
        for char in SHELL_METACHARACTERS:
            if char in arg:
                raise ValueError("Shell metacharacter %r not allowed in command args" % char)


def is_ci_mode(env: Mapping[str, str]) -> bool:
    """Detect if running in CI mode.

    Args:
        env: environment variables (os.environ or test fixture)

    Returns:
        True if in CI mode, False otherwise
    """
    # FORGE_MODE=ci (case-insensitive)
    forge_mode = env.get("FORGE_MODE", "").strip().lower()
    if forge_mode == "ci":
        return True

    # Platform CI vars (any non-empty value means CI)
    ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILD_URL"]
    for var in ci_vars:
        if env.get(var, "").strip():
            return True

    return False


def match_source_patterns(
    staged_files: list[str],
    patterns: list[str],
) -> bool:
    """Check if any staged file matches any pattern.

    Args:
        staged_files: list of file paths from git diff --cached
        patterns: list of glob patterns (e.g. ["*.py", "*.sh"])

    Returns:
        True if any file matches any pattern (run tests),
        False if no matches (skip tests).

    Special cases:
        - Empty staged_files -> False (no source changes, skip tests)
        - Empty patterns list + non-empty files -> True (always run tests)
    """
    if not staged_files:
        return False  # No files staged, skip tests
    if not patterns:
        return True  # No filter, always run

    for file_path in staged_files:
        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
    return False


def load_test_baseline(
    baseline_path: str | Path,
    fs_open=open,
) -> dict | None:
    """Load test baseline from JSON.

    Args:
        baseline_path: path to test_baseline.json
        fs_open: file open callable (injected for testing)

    Returns:
        dict with baseline data, or None if file does not exist

    Raises:
        ValueError: if JSON is malformed or missing schema_version
    """
    try:
        with fs_open(baseline_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        raise ValueError("Invalid JSON in baseline: %s" % e) from e

    if not isinstance(data, dict):
        raise ValueError("Baseline must be a JSON object")  # noqa: TRY004  # callers and tests catch ValueError for config shape
    if "schema_version" not in data:
        raise ValueError("Baseline missing 'schema_version' field")

    return data


def compute_baseline_delta(
    test_output: str,
    baseline: dict | None,
) -> tuple[bool, list[str]]:
    """Compute NEW failures vs baseline.

    Args:
        test_output: stdout from pytest -q (or other test runner)
        baseline: loaded baseline dict, or None

    Returns:
        (should_block: bool, new_failure_names: list[str])

    Logic:
        - No baseline -> (False, []) -- bootstrap, allow
        - Test not in baseline + fails -> NEW failure -> BLOCK
        - Test in baseline as "passed" + now fails -> NEW failure -> BLOCK
        - Test in baseline as "failed" + still fails -> known, not new
        - Test not in baseline + passes -> not a failure, allow
    """
    if baseline is None:
        return (False, [])  # No baseline, allow (bootstrap)

    # Preserve the legacy diagnostic helper. Public waivers use the receipt.
    failed_nodes = []
    for line in test_output.split("\n"):
        if line.startswith("FAILED "):
            test_name = line.split()[1] if len(line.split()) > 1 else ""
            if not test_name:
                continue

            failed_nodes.append(test_name)

    return compare_failed_nodes(failed_nodes, baseline)


def translate_exit_code(test_returncode: int) -> int:
    """Translate test exit code to hook exit code.

    Args:
        test_returncode: exit code from test subprocess

    Returns:
        0 (allow) or 1 (BLOCK) for the pre-commit hook

    Mapping:
        0 -> 0 (allow)
        1 -> 1 (BLOCK - real test failure)
        2, 3 -> 0 (allow - pytest interrupt/internal error)
        4 -> 1 (BLOCK - usage error, misconfigured command)
        5 -> 1 (BLOCK - no tests collected, toothless gate)
        negative -> 1 (BLOCK - killed by signal)
        timeout or >5 -> 1 (BLOCK)
    """
    if test_returncode < 0:
        print(
            "forge: test runner killed by signal %d (e.g. segfault)" % abs(test_returncode),
            file=sys.stderr,
        )
        return 1  # Block -- process was killed
    if test_returncode == 0:
        return 0  # Pass
    if test_returncode == 1:
        return 1  # Real failure
    if test_returncode in (2, 3):
        return 0  # Interrupt/internal error, warn but allow
    if test_returncode in (4, 5):
        return 1  # Usage error / no tests collected
    # timeout or unknown (>5)
    return 1  # Block


def run_gate_check(
    args=None,
    env=None,
    cwd=None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    """Main gate-check entry point.

    Args:
        args: parsed argparse Namespace; reads args.quiet if present
        env: environment variables (os.environ if None)
        cwd: working directory (Path.cwd() if None)
        stdout: output stream (sys.stdout if None)
        stderr: error stream (sys.stderr if None)

    Returns:
        EXIT_PASS (0) or EXIT_FAIL (1)

    CRITICAL: NEVER returns EXIT_CLI_ERROR (2). Config/parse errors
    return EXIT_FAIL (1) to enforce FAIL-OPEN guard.
    """
    if env is None:
        env = os.environ
    if cwd is None:
        cwd = Path.cwd()
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr

    quiet = getattr(args, "quiet", False)

    def warn(msg):
        if not quiet:
            print(msg, file=stderr)

    # FAIL-OPEN guard: catch config/parse errors -> BLOCK (exit 1)
    try:
        config_path = cwd / ".code-forge" / "gate.yaml"
        config = load_gate_config(config_path)
        from .kernel_context import validate_kernel_context
        from .trust import is_trusted_kernel_context
        from .workspace import resolve_workspace

        kernel_cfg = validate_kernel_context(config.get("kernel_context", {}))
        if kernel_cfg.enabled and not is_trusted_kernel_context(
            config_path,
            resolve_workspace(cwd, env),
            kernel_cfg,
        ):
            warn("kernel-context: file access is not authorized; run code-forge trust")
        test_config = config["test"]

        # Validate command safety
        validate_command_safety(test_config["command"])

        # Load baseline (OK if None)
        baseline_path = cwd / ".code-forge" / "test_baseline.json"
        baseline = load_test_baseline(baseline_path)

    except (FileNotFoundError, ValueError) as e:
        print("forge: error: %s" % e, file=stderr)
        return EXIT_FAIL  # BLOCK on config error (FAIL-OPEN guard)

    # Check FORGE_SKIP_TESTS (only in local mode, ignored in CI)
    if env.get("FORGE_SKIP_TESTS") == "1":
        if is_ci_mode(env):
            warn("forge: CI mode: FORGE_SKIP_TESTS ignored")
        else:
            warn("forge: FORGE_SKIP_TESTS=1, skipping tests")
            return EXIT_PASS  # Allow

    # Get staged files via git diff --cached --name-only
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            cwd=str(cwd),
            timeout=5,
        )
        if result.returncode != 0:
            print("forge: error: git diff --cached failed: %s" % result.stderr.strip(), file=stderr)
            return EXIT_FAIL  # BLOCK on git error

        staged_files = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except subprocess.TimeoutExpired:
        print("forge: error: git diff --cached timed out", file=stderr)
        return EXIT_FAIL
    except FileNotFoundError:
        print("forge: error: git not found on PATH", file=stderr)
        return EXIT_FAIL

    # Filter on source_patterns
    source_patterns = test_config.get("source_patterns", [])
    if not match_source_patterns(staged_files, source_patterns):
        warn("forge: no source files staged, skipping tests")
        return EXIT_PASS  # Allow

    # Run test command
    command = test_config["command"]
    test_env = {**env, **test_config.get("env", {})}
    timeout = test_config.get("timeout_seconds", 120)
    test_cwd = cwd / test_config.get("cwd", ".")

    # Put the repository's own source root first on the import path. An
    # editable install of the same package (common in linked worktrees)
    # otherwise shadows the tree being checked, and the gate silently
    # reports on code that is not staged.
    repo_src = cwd / "src"
    if repo_src.is_dir():
        existing = test_env.get("PYTHONPATH", "")
        parts = [str(repo_src)]
        if existing:
            parts.append(existing)
        test_env["PYTHONPATH"] = os.pathsep.join(parts)

    evidence = None
    capture = prepare_pytest_capture(
        command,
        test_cwd=test_cwd,
        test_env=test_env,
        reporter_path=Path(__file__).resolve().with_name("_gate_pytest.py"),
    )
    if capture is not None and repo_src.is_dir():
        capture.source_root = str(repo_src)
    try:
        if capture is not None:
            test_result = run_captured_pytest(
                command,
                capture=capture,
                test_cwd=test_cwd,
                test_env=test_env,
                timeout_seconds=timeout,
            )
            if test_result.returncode == 1:
                evidence = read_pytest_evidence(
                    capture,
                    child_pid=test_result.pid,
                    returncode=test_result.returncode,
                )
        else:
            test_result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=test_env,
                cwd=str(test_cwd),
                timeout=timeout,
                check=False,
                **child_sigint_options(),  # #273
            )
        test_returncode = test_result.returncode
        test_stdout = test_result.stdout
    except subprocess.TimeoutExpired:
        print("forge: error: tests timed out after %d seconds" % timeout, file=stderr)
        return EXIT_FAIL  # BLOCK on timeout
    except FileNotFoundError:
        print("forge: error: test runner not found: %s" % command[0], file=stderr)
        return EXIT_FAIL
    except (OSError, ValueError) as exc:
        print("forge: error: test invocation failed: %s" % exc, file=stderr)
        return EXIT_FAIL
    finally:
        if capture is not None and not capture.close():
            warn("forge: warning: capture cleanup failed: " + capture.cleanup_error)
            if evidence is not None:
                evidence.valid = False
                evidence.reason = "capture cleanup failed: " + capture.cleanup_error

    # Translate exit code
    translated = translate_exit_code(test_returncode)

    # Surface test stderr so users see diagnostics (exit 4/5 especially)
    if test_result.stderr and test_result.stderr.strip():
        print(test_result.stderr.rstrip(), file=stderr)

    # Special handling for exit 2-3 (warn but allow)
    if test_returncode == 2:
        # pytest reuses exit code 2 for a failed collection, where no
        # test ever ran. Waving that through would pass the gate on a
        # suite that could not even be imported, so the benefit of the
        # doubt goes the other way: allow only when the run really was
        # interrupted, and block otherwise.
        combined = f"{test_stdout or ''}\n{test_result.stderr or ''}"
        if not _INTERRUPT_BANNER.search(combined):
            print(
                "forge: error: tests exited with code 2 without an interrupt; blocking commit",
                file=stderr,
            )
            return EXIT_FAIL
        warn("forge: warning: tests exited with code 2 (keyboard interrupt); allowing commit")
    elif test_returncode == 3:
        warn("forge: warning: tests exited with code 3 (internal error); allowing commit")

    # Baseline delta applies ONLY to real test failures (exit 1).
    # Exit 4 (usage error), exit 5 (no tests collected), and timeout BLOCK
    # directly -- vacuous delta would otherwise downgrade them to PASS.
    if translated == EXIT_FAIL and test_returncode == 1:
        if evidence is None or not evidence.valid:
            reason = (
                evidence.reason if evidence is not None else "unsupported command or capture unavailable"
            )
            print("forge: insufficient structured pytest evidence: %s" % reason, file=stderr)
            return EXIT_FAIL
        should_block, new_failures = compare_failed_nodes(evidence.failed_nodes, baseline)
        if not should_block:
            if baseline is None:
                if env.get("FORGE_ALLOW_NO_BASELINE") == "1":
                    warn("forge: warning: no baseline; allowing (opt-in)")
                else:
                    print(
                        "forge: no baseline established; blocking commit. "
                        "Run pytest and save test_baseline.json, or set "
                        "FORGE_ALLOW_NO_BASELINE=1 to allow.",
                        file=stderr,
                    )
                    return EXIT_FAIL
            else:
                warn("forge: all failures are known (in baseline); allowing commit")
            return EXIT_PASS  # Downgrade to allow
        else:
            # NEW failures detected
            print("forge: NEW test failures detected (not in baseline):", file=stderr)
            for test_name in new_failures:
                print("  - %s" % test_name, file=stderr)
            return EXIT_FAIL  # BLOCK

    return translated

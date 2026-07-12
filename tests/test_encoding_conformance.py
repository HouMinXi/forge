# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Conformance test: encoding and liveness probe invariants.

This test greps the codebase for violations of the encoding class
(D3) and the _pid_alive extraction (D2).  It keeps the class closed:
any new text=True without encoding= or encoding-less text-mode IO
will be caught here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "code_forge"

# Deliberate exceptions: (filename, line substring, reason).  An entry
# here must correspond to a line that LOOKS like text-mode IO but is
# not (e.g. code samples embedded in string literals).
_BARE_OPEN_ALLOWLIST = [
    ("canary_gen.py", "f = open(path)",
     "code sample inside a string literal, not real IO"),
    ("canary_gen.py", "with open(path) as f",
     "code sample inside a string literal, not real IO"),
]


def _collect_py_files() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


class TestTextTrueEncoding:
    """Every subprocess call with text=True must have encoding=."""

    def test_no_text_true_without_encoding(self):
        """text=True without encoding= is a D3 violation."""
        violations = []
        for fpath in _collect_py_files():
            content = fpath.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "text=True" in line and "encoding=" not in line:
                    rel = fpath.relative_to(SRC_DIR.parent.parent)
                    violations.append(f"{rel}:{i}: {stripped[:80]}")
        if violations:
            pytest.fail(
                "text=True without encoding= found:\n"
                + "\n".join(violations)
            )


class TestTextModeFileIO:
    """Every text-mode file open must have encoding=."""

    def test_no_encodingless_read_text(self):
        violations = []
        for fpath in _collect_py_files():
            content = fpath.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ".read_text()" in line and "encoding" not in line:
                    rel = fpath.relative_to(SRC_DIR.parent.parent)
                    violations.append(f"{rel}:{i}: {stripped[:80]}")
        if violations:
            pytest.fail(
                ".read_text() without encoding= found:\n"
                + "\n".join(violations)
            )

    def test_no_encodingless_write_text(self):
        """write_text calls must include encoding= somewhere in the call."""
        violations = []
        for fpath in _collect_py_files():
            content = fpath.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ".write_text(" not in line:
                    continue
                # Multi-line call: grab from .write_text( to matching )
                depth = 0
                call_text = ""
                for scan_line in content.splitlines()[i - 1:]:
                    call_text += scan_line + "\n"
                    depth += scan_line.count("(") - scan_line.count(")")
                    if depth <= 0:
                        break
                if "encoding=" not in call_text:
                    rel = fpath.relative_to(SRC_DIR.parent.parent)
                    violations.append(f"{rel}:{i}: {stripped[:80]}")
        if violations:
            pytest.fail(
                ".write_text() without encoding= found:\n"
                + "\n".join(violations)
            )

    def test_no_encodingless_fdopen(self):
        violations = []
        for fpath in _collect_py_files():
            content = fpath.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "os.fdopen(" in line and '"w"' in line and "encoding" not in line:
                    rel = fpath.relative_to(SRC_DIR.parent.parent)
                    violations.append(f"{rel}:{i}: {stripped[:80]}")
        if violations:
            pytest.fail(
                'os.fdopen(..., "w") without encoding= found:\n'
                + "\n".join(violations)
            )

    def test_no_bare_text_mode_open(self):
        """`with open(` / `= open(` without a binary mode and without
        encoding= is a D3 violation: the read/write decodes with the
        locale codec on Windows (GBK on Chinese-locale boxes) and
        crashes on UTF-8 content.  Multi-line calls are scanned to the
        matching close paren.  Deliberate exceptions live in
        _BARE_OPEN_ALLOWLIST with a reason.
        """
        violations = []
        for fpath in _collect_py_files():
            content = fpath.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "with open(" not in line and "= open(" not in line:
                    continue
                if any(
                    fpath.name == fn and sub in line
                    for fn, sub, _reason in _BARE_OPEN_ALLOWLIST
                ):
                    continue
                # Capture the full call (multi-line aware).
                depth = 0
                call_text = ""
                for scan_line in lines[i - 1:]:
                    call_text += scan_line + "\n"
                    depth += scan_line.count("(") - scan_line.count(")")
                    if depth <= 0:
                        break
                if "encoding=" in call_text:
                    continue
                if (
                    '"rb"' in call_text
                    or '"wb"' in call_text
                    or '"ab"' in call_text
                ):
                    continue
                rel = fpath.relative_to(SRC_DIR.parent.parent)
                violations.append(f"{rel}:{i}: {stripped[:80]}")
        if violations:
            pytest.fail(
                "bare text-mode open() without encoding= found:\n"
                + "\n".join(violations)
            )


class TestPidAliveExtraction:
    """os.kill(pid, 0) must only appear inside lock._pid_alive."""

    def test_no_os_kill_probe_outside_pid_alive(self):
        violations = []
        for fpath in _collect_py_files():
            content = fpath.read_text(encoding="utf-8")
            in_pid_alive = False
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if "def _pid_alive" in line:
                    in_pid_alive = True
                elif stripped.startswith("def ") and in_pid_alive:
                    in_pid_alive = False
                if "os.kill(" in line and ", 0)" in line and not in_pid_alive:
                    if "comment" not in stripped.lower():
                        rel = fpath.relative_to(SRC_DIR.parent.parent)
                        violations.append(f"{rel}:{i}: {stripped[:80]}")
        if violations:
            pytest.fail(
                "os.kill(pid, 0) outside _pid_alive found:\n"
                + "\n".join(violations)
            )

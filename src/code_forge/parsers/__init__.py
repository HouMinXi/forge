# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parser subsystem -- tool output to Finding conversion.

Exports PARSER_DISPATCH (format -> parser function) and parse_output()
for dispatch by output_format string.
"""

from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.shellcheck import parse_shellcheck
from code_forge.parsers.ruff import parse_ruff
from code_forge.parsers.semgrep import parse_semgrep
from code_forge.parsers.clippy import parse_clippy
from code_forge.parsers.checkpatch import parse_checkpatch
from code_forge.parsers.non_ascii import parse_non_ascii
from code_forge.parsers._sarif import _parse_sarif

# 5 keys map to 6 tools: ruff and semgrep both use output_format="sarif"
# in tools.yaml, dispatching to the shared _parse_sarif function.
# The tool_name parameter distinguishes them in the Finding objects.
PARSER_DISPATCH: dict = {
    "shellcheck_json": parse_shellcheck,
    "sarif": _parse_sarif,
    "clippy_json": parse_clippy,
    "checkpatch_emacs": parse_checkpatch,
    "grep_line": parse_non_ascii,
}


def parse_output(
    output: str,
    output_format: str,
    tool_name: str,
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Dispatch to the correct parser by output_format.

    Raises KeyError on unknown format (registry validation happens
    at dispatch time per Mimo F-01).
    """
    parser_fn = PARSER_DISPATCH[output_format]
    return parser_fn(output, tool_name, exit_code)


__all__ = [
    "Finding",
    "ToolError",
    "PARSER_DISPATCH",
    "parse_output",
    "parse_shellcheck",
    "parse_ruff",
    "parse_semgrep",
    "parse_clippy",
    "parse_checkpatch",
    "parse_non_ascii",
]

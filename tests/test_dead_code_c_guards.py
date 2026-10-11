# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Literal C guard regressions through real SQLite and public caller discovery.

These paths do not require code-review-graph or a tree-sitter parser. Compound
guards and trailing comments are deliberately left unevaluated and fail open.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from code_forge import dead_code
from code_forge.cross_repo_impact import find_cross_repo_callers
from code_forge.dead_code import LiveCaller, _is_dead_call_site, _live_callers


def _assert_callers(
    tmp_path: Path,
    suffix: str,
    source: str,
    callers: list[tuple[str, int, bool]],
) -> None:
    """Assert exact public and shared-filter results using a real graph.db."""
    source_path = tmp_path / f"consumer{suffix}"
    source_path.write_bytes(source.encode("utf-8"))
    db_path = tmp_path / "graph.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
            "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER)"
        )
        conn.execute("CREATE TABLE edges (kind TEXT, source_qualified TEXT, target_qualified TEXT)")
        conn.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (i, "function", name, f"consumer::{name}", str(source_path), line, line)
                for i, (name, line, _) in enumerate(callers, 1)
            ],
        )
        conn.executemany(
            "INSERT INTO edges VALUES (?, ?, ?)",
            [("CALLS", f"consumer::{name}", "target_fn") for name, _, _ in callers]
            + [("IMPORTS_FROM", "consumer::target_fn", "target_mod::target_fn")],
        )
        conn.commit()

        expected = {
            f"consumer::{name}": {
                "symbol": "target_fn",
                "caller_qualified": f"consumer::{name}",
                "caller_file": str(source_path),
                "caller_line": line,
            }
            for name, line, keep in callers
            if keep
        }
        actual = find_cross_repo_callers(str(db_path), [{"name": "target_fn", "module": "target_mod"}])
        assert {caller["caller_qualified"]: caller for caller in actual} == expected
        assert len(actual) == len(expected)
        assert set(_live_callers(conn.cursor(), "target_fn", "target_mod")) == {
            LiveCaller(f"consumer::{name}", str(source_path), line)
            for name, line, keep in callers
            if keep
        }

    for _, line, keep in callers:
        assert _is_dead_call_site(str(source_path), line) is (not keep)


@pytest.mark.parametrize("suffix", [".c", ".h"])
@pytest.mark.parametrize(
    ("guard", "keep"),
    [
        pytest.param("#if 0", False, id="literal-zero"),
        pytest.param("#if 1", True, id="literal-one"),
        pytest.param("#if 0 || 1", True, id="zero-or-one"),
        pytest.param("#if 0 == 0", True, id="zero-equals-zero"),
        pytest.param("#if 0 + 1", True, id="zero-plus-one"),
        pytest.param("#if 0 && 1", True, id="unevaluated-false-expression"),
        pytest.param("#if 0 /* disabled */", True, id="unevaluated-comment"),
        pytest.param("#if 0 \\\n || 1", True, id="continued-expression"),
        pytest.param("#if 0\\\n || 1", True, id="adjacent-continuation"),
    ],
)
def test_public_callers_only_drop_literal_zero(
    tmp_path: Path, suffix: str, guard: str, keep: bool
) -> None:
    source = f"{guard}\nvoid caller(void) {{ target_fn(); }}\n#endif\n"
    _assert_callers(tmp_path, suffix, source, [("caller", guard.count("\n") + 2, keep)])


@pytest.mark.parametrize("suffix", [".c", ".h"])
@pytest.mark.parametrize("condition", ["0", "0 || 1"])
def test_tabs_and_crlf(tmp_path: Path, suffix: str, condition: str) -> None:
    source = f"\t#\tif\t{condition} \t\r\nvoid caller(void) {{ target_fn(); }}\r\n#endif\r\n"
    _assert_callers(tmp_path, suffix, source, [("caller", 2, condition != "0")])


@pytest.mark.parametrize("suffix", [".c", ".h"])
@pytest.mark.parametrize("guards", [("0", "0 || 1"), ("0 || 1", "0")])
def test_guard_order_preserves_first_and_later_callers(
    tmp_path: Path, suffix: str, guards: tuple[str, str]
) -> None:
    lines = ["void first(void) { target_fn(); }"]
    callers = [("first", 1, True)]
    for index, guard in enumerate(guards):
        name = f"guarded_{index}"
        lines.extend([f"#if {guard}", f"void {name}(void) {{ target_fn(); }}", "#endif"])
        callers.append((name, len(lines) - 1, guard != "0"))
    lines.append("void last(void) { target_fn(); }")
    callers.append(("last", len(lines), True))
    _assert_callers(tmp_path, suffix, "\n".join(lines), callers)


@pytest.mark.parametrize("suffix", [".c", ".h"])
@pytest.mark.parametrize("nested_guard", ["0", "1", "0 || 1"])
@pytest.mark.parametrize("alternative", ["#else", "#elif 1"])
def test_nested_and_alternative_branch_conservatism(
    tmp_path: Path, suffix: str, nested_guard: str, alternative: str
) -> None:
    source = (
        f"#if 0\n#if {nested_guard}\n"
        "void nested(void) { target_fn(); }\n#endif\n"
        "void outer(void) { target_fn(); }\n"
        f"{alternative}\n"
        "void alternate(void) { target_fn(); }\n#endif\n"
        "void after(void) { target_fn(); }\n"
    )
    # The existing upward scan treats nonliteral inner guards as live even
    # within a disabled outer block; this change does not expand its analysis.
    _assert_callers(
        tmp_path,
        suffix,
        source,
        [
            ("nested", 3, nested_guard != "0"),
            ("outer", 5, False),
            ("alternate", 7, True),
            ("after", 9, True),
        ],
    )


@pytest.mark.parametrize("suffix", [".c", ".h"])
def test_current_directive_line_is_not_scanned(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"boundary{suffix}"
    source.write_text("#if 0\nvoid caller(void) { target_fn(); }\n#endif\n")
    assert _is_dead_call_site(str(source), 1) is False
    assert _is_dead_call_site(str(source), 2) is True


@pytest.mark.parametrize("line", ["#if 0", "#if 0\n", " \t# \tif\t0 \t\r\n"])
def test_literal_guard_matches_one_complete_physical_line(line: str) -> None:
    assert dead_code._IF0_RE.match(line) is not None


@pytest.mark.parametrize(
    "line",
    [
        "#if 0\n || 1\n",
        "#\nif 0\n",
        "#if\n0\n",
        "\v#if 0\n",
        "\f#if 0\n",
        "#if 0\v\n",
    ],
)
def test_literal_guard_does_not_consume_nonhorizontal_whitespace(line: str) -> None:
    assert dead_code._IF0_RE.match(line) is None


@pytest.mark.parametrize("suffix", [".py", ".cpp", ".rs"])
def test_c_guard_does_not_change_other_language_dispatch(tmp_path: Path, suffix: str) -> None:
    source = "#if 0\ndef caller(): target_fn()\n#endif\n"
    _assert_callers(tmp_path, suffix, source, [("caller", 2, True)])


def test_python_false_guard_retains_existing_parser_behavior(tmp_path: Path) -> None:
    _assert_callers(
        tmp_path,
        ".py",
        "if False:\n    def caller(): target_fn()\n",
        [("caller", 2, dead_code._PYTHON_PARSER is None)],
    )

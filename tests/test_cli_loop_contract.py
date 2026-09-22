# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Contract tests for CLI loop behavior and file text windowing."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from code_forge.cli import _assemble_post_image, _run_hold_loop, _window_file_text
from code_forge.llm_invoke import Usage
from code_forge.state import (
    Disposition,
    Mode,
    State,
    StateFinding,
    Verdict,
    save_state,
)


def _hunk(start: int, end: int) -> dict:
    return {
        "start": start,
        "end": end,
        "added_lines": [],
        "is_deletion_only": False,
    }


def test_window_file_text_non_adjacent_windows_full_output() -> None:
    """Non-adjacent windows must emit complete formatted lines and omission markers."""
    lines = [f"line{i}" for i in range(1, 31)]
    text = "\n".join(lines)
    # Hunk 1: 5..6 with context 2 -> [3, 8]
    # Hunk 2: 20..21 with context 2 -> [18, 23]
    hunks = [_hunk(5, 6), _hunk(20, 21)]
    out, windowed = _window_file_text(text, hunks, context_lines=2)
    assert windowed is True

    expected_elements = [
        "... [2 lines omitted]",
        "3: line3",
        "4: line4",
        "5: line5",
        "6: line6",
        "7: line7",
        "8: line8",
        "... [9 lines omitted]",
        "18: line18",
        "19: line19",
        "20: line20",
        "21: line21",
        "22: line22",
        "23: line23",
        "... [7 lines omitted]",
    ]
    expected_text = "\n".join(expected_elements)
    assert out == expected_text


def test_window_file_text_empty_input() -> None:
    """Empty text or empty hunks must be handled without error."""
    # Empty text with empty hunks
    out_empty, windowed_empty = _window_file_text("", [], context_lines=3)
    assert out_empty == ""
    assert windowed_empty is False

    # Empty text with non-empty hunks
    out_empty_hunks, windowed_empty_hunks = _window_file_text(
        "", [_hunk(1, 2)], context_lines=3
    )
    assert out_empty_hunks == ""
    assert windowed_empty_hunks is False

    # Non-empty text with empty hunks
    text = "hello\nworld"
    out_no_hunks, windowed_no_hunks = _window_file_text(text, [], context_lines=3)
    assert out_no_hunks == text
    assert windowed_no_hunks is False

    # Out-of-bounds hunks where lo > hi (all hunks beyond text lines)
    out_oob, windowed_oob = _window_file_text(
        text, [_hunk(100, 105)], context_lines=0
    )
    assert out_oob == text
    assert windowed_oob is False


def test_window_file_text_overlapping_windows() -> None:
    """Overlapping and adjacent windows must merge without duplicating lines."""
    lines = [f"item_{i}" for i in range(1, 21)]
    text = "\n".join(lines)
    # Hunk 1: 5..7, context 2 -> [3, 9]
    # Hunk 2: 8..10, context 2 -> [6, 12]
    # Merged -> [3, 12]
    hunks = [_hunk(5, 7), _hunk(8, 10)]
    out, windowed = _window_file_text(text, hunks, context_lines=2)
    assert windowed is True

    out_lines = out.splitlines()
    assert out_lines[0] == "... [2 lines omitted]"
    assert out_lines[-1] == "... [8 lines omitted]"

    content_lines = out_lines[1:-1]
    expected_content = [f"{n}: item_{n}" for n in range(3, 13)]
    assert content_lines == expected_content
    # Confirm no duplicates in emitted numbered lines
    assert len(content_lines) == len(set(content_lines))


def test_window_file_text_real_temp_file_path(tmp_path: Path) -> None:
    """Windowing must correctly format content read from a real file on disk."""
    file_name = "service.py"
    target_file = tmp_path / file_name
    src_lines = [f"def step_{i}(): pass" for i in range(1, 41)]
    target_file.write_text("\n".join(src_lines), encoding="utf-8")

    real_content = target_file.read_text(encoding="utf-8")
    hunks = [_hunk(15, 17)]
    out, windowed = _window_file_text(real_content, hunks, context_lines=1)
    assert windowed is True
    assert "14: def step_14(): pass" in out
    assert "15: def step_15(): pass" in out
    assert "16: def step_16(): pass" in out
    assert "17: def step_17(): pass" in out
    assert "18: def step_18(): pass" in out
    assert "... [13 lines omitted]" in out
    assert "... [22 lines omitted]" in out

    # Exercise _assemble_post_image using the real temp directory path
    diff_text = (
        f"diff --git a/{file_name} b/{file_name}\n"
        f"--- a/{file_name}\n"
        f"+++ b/{file_name}\n"
        "@@ -15,2 +15,2 @@\n"
        " def step_14(): pass\n"
        "-def step_15(): pass\n"
        "+def step_15(): return True\n"
    )
    post_image, digest = _assemble_post_image(
        tmp_path, diff_text, context_lines=1
    )
    assert f"## File: {file_name} (around the changes)" in post_image
    assert "15: def step_15(): pass" in post_image
    assert isinstance(digest, str) and len(digest) > 0


def test_run_hold_loop_iteration_contract(tmp_path: Path) -> None:
    """_run_hold_loop must execute bounded cycles and handle transitions."""
    state_path = tmp_path / ".code-forge" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    initial_state = State(
        mode=Mode.LOCAL,
        source_hash="hash123",
        baseline_spec_repr="git:HEAD",
        findings=[
            StateFinding(
                id="find-1",
                fingerprint="fp-1",
                source="L0",
                disposition=Disposition.UNCERTAIN,
                description="desc",
                file="test.py",
                line_range=[1, 2],
            )
        ],
        verdict=Verdict.PENDING,
    )
    save_state(initial_state, state_path)

    results = iter([Verdict.PENDING, Verdict.PASS])

    def mock_run(self_sm: object) -> Verdict:
        return next(results)

    with patch(
        "code_forge.cli.StateMachine.run", mock_run
    ), patch(
        "code_forge.cli.run_hold_ui", return_value=None
    ):
        verdict = _run_hold_loop(
            mode=Mode.LOCAL,
            falsifier=MagicMock(),
            autofixer=MagicMock(),
            revert_fn=MagicMock(),
            resolved=MagicMock(),
            source_hash="hash123",
            baseline_repr="git:HEAD",
            cwd=tmp_path,
            registry={},
            max_rounds=10,
            max_fix_attempts=3,
            state_path=state_path,
            l1_provider=lambda: ([], [], Usage(), 0.0),
            input_fn=lambda _: "c",
            output_fn=lambda _: None,
        )

    assert verdict == Verdict.PASS

# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Advisory summaries and ordinary LOCAL subprocess report publication."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from .assertion_outcome import AssertionOutcome


_REPORT_NAME = "local-assertion-report.json"


def format_assertion_summary(metadata: dict[str, object]) -> str:
    """Render fixed fields from validated AssertionOutcome.to_metadata output."""
    line = f"code-forge: test_assertion={metadata['status']}"
    if metadata["finding_count"] is not None:
        line += f" assertion_findings={metadata['finding_count']}"
    if "stage" in metadata:
        line += f" stage={metadata['stage']} kind={metadata['kind']}"
    return line


def clear_local_assertion_report(state_path: Path) -> None:
    """Invalidate the previous report only after the review lock is acquired."""
    try:
        (state_path.parent / _REPORT_NAME).unlink(missing_ok=True)
    except OSError:
        print(
            "code-forge: warning: cannot clear local assertion report; "
            "the previous report is not this run's result",
            file=sys.stderr,
        )


def emit_local_assertion_report(state_path: Path, outcome: AssertionOutcome | None) -> None:
    """Publish validated advice without consulting or changing primary state."""
    metadata = AssertionOutcome.to_metadata(outcome) if outcome is not None else None
    payload = {"testAssertion": metadata} if metadata is not None else {}
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
    if metadata is not None:
        print(format_assertion_summary(metadata), file=sys.stderr)

    destination = state_path.parent / _REPORT_NAME
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{_REPORT_NAME}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
        os.replace(temporary, destination)
    except OSError:
        print("code-forge: warning: cannot write local assertion report", file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Best effort only: cleanup must not mask an earlier failure.
                pass

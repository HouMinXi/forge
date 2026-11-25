"""Write complete local reports only after validation and scoring."""

import json
import os
import tempfile
from contextlib import suppress


def build_report(rows):
    def scorecard(task):
        cases = [row for row in rows if row["task"] == task]
        return {
            "attempted": len(cases),
            "completed": sum(r["status"] == "completed" for r in cases),
            "failed": sum(r["status"] != "completed" for r in cases),
            "scorable_runs": sum(r["hits"] is not None for r in cases),
            "known_root_hits": sum(r["hits"] or 0 for r in cases)
            if any(r["hits"] is not None for r in cases)
            else None,
            "false_blockers": sum(r["false_blockers"] for r in cases),
            "pending_findings": sum(r["pending_findings"] for r in cases),
            "usage_known": sum(r["usage"] for r in cases if r["usage"] is not None)
            if any(r["usage"] is not None for r in cases)
            else None,
            "usage_unknown_runs": sum(r["usage"] is None for r in cases),
            "cases": cases,
        }

    return {
        "schema": 1,
        "first_review": scorecard("first_review"),
        "revision_followup": scorecard("revision_followup"),
    }


def publish_local(path, report):
    # A new destination avoids confusing a previous successful output with a
    # refused run. A same-directory link publishes a complete file atomically.
    raw = (json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".offline-replay-", delete=False
        ) as output:
            temp = output.name
            output.write(raw)
            output.flush()
        os.link(temp, path)
    finally:
        if temp is not None:
            # Removing the scratch name must not undo publication or mask a link failure.
            with suppress(OSError):
                os.unlink(temp)

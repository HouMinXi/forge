"""Project runner step outcomes into a closed installed-wheel public summary.

No raw evidence is read, no harness is imported, and no qualification is rerun.
The unchanged harness's exit status remains the qualification authority.
"""

import json
import sys

OUTCOMES = {"success", "failure", "cancelled", "skipped"}
JOB_STATUSES = {"success", "failure", "cancelled"}


def summary(setup, qualification, job):
    result = {
        "schema": "forge-public-installed-wheel-summary-v1",
        "mode": "installed-wheel",
        "scope": "PARTIAL_RUNTIME_ONLY",
        "status": "UNKNOWN",
        "reason": "unavailable",
        "raw_evidence": "NOT_PUBLISHED",
    }
    if (
        type(setup) is not str
        or type(qualification) is not str
        or type(job) is not str
        or setup not in OUTCOMES
        or qualification not in OUTCOMES
        or job not in JOB_STATUSES
    ):
        return result
    if "failure" in (setup, qualification, job) or "cancelled" in (setup, qualification, job):
        result.update(status="STOP", reason="execution_failed")
    elif setup == qualification == job == "success":
        result.update(status="PASS", reason="controls_complete")
    else:
        result.update(reason="not_completed")
    return result


def main():
    result = summary(*sys.argv[1:]) if len(sys.argv) == 4 else summary(None, None, None)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())

"""Static workflow controls only, not hosted runner admission evidence."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text())


def test_opt_in_read_only_non_gating():
    doc = workflow()
    assert set(doc["on"]) == {"workflow_dispatch"}
    assert doc["on"]["workflow_dispatch"]["inputs"]["run_capture"]["default"] is False
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["capture"]
    assert job["if"] == "${{ inputs.run_capture }}"
    checkout = job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["ref"] == "${{ github.sha }}"


def test_only_validated_summary_upload():
    steps = workflow()["jobs"]["capture"]["steps"]
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"].splitlines() == [
        "${{ runner.temp }}/forge-cli-capture-public/published/" + name
        for name in ("capability.json", "baseline.json", "diagnostic.json")
    ]
    assert "steps.publication.outcome == 'success'" in uploads[0]["if"]
    run = next(s["run"] for s in steps if s.get("id") == "publication")
    assert "scripts/cli_capture_publication.py" in run
    assert '"$RUNNER_TEMP/forge-cli-capture-public/published"' in run


def test_original_codes_and_independent_modes():
    steps = workflow()["jobs"]["capture"]["steps"]
    for mode in ("baseline", "diagnostic"):
        step = next(s for s in steps if s.get("id") == mode)
        assert step["continue-on-error"] is True
        assert "|| code=$?" in step["run"]
        assert '--shell-exit "$code"' in step["run"]
        assert 'exit "$code"' in step["run"]
    assert "!cancelled()" in next(s for s in steps if s.get("id") == "diagnostic")["if"]
    gate = steps[-1]["run"]
    assert 'exit "$code"' in gate
    assert '"$BASELINE_EXIT" == 0 && "$DIAGNOSTIC_EXIT" == 0' in gate
    assert '"$BASELINE_SUMMARY" == 0 && "$DIAGNOSTIC_SUMMARY" == 0' in gate

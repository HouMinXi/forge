"""Reverse only the exact stock-dispatch routing delta for older CI snapshots.

This is test-only. Independent routing/mutation tests read the raw workflow;
production conditions and commands are never imported from this helper.
"""

import copy

LEGACY_GROUP = (
    "${{ github.workflow }}-${{ github.event.pull_request.head.repo.full_name || github.repository }}"
    "-${{ github.head_ref || github.ref_name }}"
)
ORDINARY = (
    "github.event_name != 'workflow_dispatch' || "
    "(!inputs.stock_runner_pilot && inputs.expected_source_sha == '')"
)


def without_stock_dispatch(workflow):
    value = copy.deepcopy(workflow)
    inputs = value["on"]["workflow_dispatch"]["inputs"]
    assert inputs["stock_runner_pilot"]["default"] is False
    assert inputs["stock_runner_pilot"]["required"] is False
    assert inputs["expected_source_sha"]["required"] is False
    assert value["on"]["workflow_dispatch"] == {
        "inputs": {
            "stock_runner_pilot": {
                "description": "Run only the stock Ubuntu 26.04 / Python 3.14 qualification.",
                "required": False,
                "default": False,
                "type": "boolean",
            },
            "expected_source_sha": {
                "description": "Reviewed exact commit SHA, required only for the stock pilot.",
                "required": False,
                "default": "",
                "type": "string",
            },
        }
    }
    value["on"]["workflow_dispatch"] = None
    assert value["concurrency"]["cancel-in-progress"] is True
    assert value["concurrency"] == {
        "group": "${{ github.event_name == 'workflow_dispatch' && "
        "(inputs.stock_runner_pilot || inputs.expected_source_sha != '') && 'stock-pilot-' || '' }}"
        + LEGACY_GROUP,
        "cancel-in-progress": True,
    }
    value["concurrency"]["group"] = LEGACY_GROUP
    jobs = value["jobs"]
    assert type(jobs["reject-stock-inputs"]["timeout-minutes"]) is int
    assert jobs.pop("reject-stock-inputs") == {
        "if": "${{ github.event_name == 'workflow_dispatch' && !inputs.stock_runner_pilot && inputs.expected_source_sha != '' }}",
        "runs-on": "ubuntu-24.04",
        "timeout-minutes": 1,
        "steps": [
            {
                "name": "Reject a source SHA without the stock pilot",
                "run": "printf '%s\\n' 'expected_source_sha requires stock_runner_pilot=true' >&2\nexit 1\n",
            }
        ],
    }
    assert jobs.pop("stock-runner-qualification") == {
        "if": "${{ github.event_name == 'workflow_dispatch' && inputs.stock_runner_pilot }}",
        "uses": "./.github/workflows/linux-stock-qualification.yml",
        "with": {"expected_source_sha": "${{ inputs.expected_source_sha }}"},
    }
    assert set(jobs) == {"plan", "process-contracts", "service-free", "linux-tests"}
    for name, job in jobs.items():
        if name == "linux-tests":
            assert job["if"] == "${{ always() && (" + ORDINARY + ") }}"
            job["if"] = "${{ always() }}"
        else:
            assert job.pop("if") == "${{ " + ORDINARY + " }}"
    return value

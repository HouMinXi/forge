# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The published SARIF log must validate against the official 2.1.0 schema.

Host diagnostics (invoke-fail, receipt, mutation infra) have no source file.
They used to name a sentinel such as ``<llm-invoke>`` as an artifact URI and
point at line 0. Neither is valid SARIF, so those results now carry no
``locations`` at all.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from code_forge.disposition import Disposition
from code_forge.sarif import build_sarif_log
from code_forge.state import State, StateFinding, Verdict

SCHEMA = json.loads(
    (Path(__file__).parent / "fixtures" / "sarif" / "sarif-schema-2.1.0.json").read_text(
        encoding="utf-8"
    )
)


def _finding(fingerprint, file, line_range, source="INFRA", description="d"):
    return StateFinding(
        id="f-" + fingerprint,
        fingerprint=fingerprint,
        source=source,
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=line_range,
        description=description,
    )


def _log(findings):
    state = State(verdict=Verdict.FAIL, findings=findings)
    return build_sarif_log(state, {"ruff": "0.5.0"}, "2.9.0")


def _results(log):
    return {r["ruleId"]: r for r in log["runs"][0]["results"]}


SENTINELS = [
    "<llm-invoke>",
    "<spawn>",
    "<receipt-evidence>",
    "<schema-validation>",
    "<exec-falsify>",
    "",
]


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_log_with_host_diagnostic_validates_against_schema(sentinel):
    log = _log(
        [
            _finding("real", "src/a.py", [3, 4], source="L0"),
            _finding("diag", sentinel, [0, 0]),
        ]
    )

    errors = [e.message for e in jsonschema.Draft4Validator(SCHEMA).iter_errors(log)]

    assert errors == []


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_host_diagnostic_has_no_location_but_keeps_rule_and_message(sentinel):
    result = _results(_log([_finding("diag", sentinel, [0, 0], description="backend 429")]))["diag"]

    assert "locations" not in result
    assert result["ruleId"] == "diag"
    assert result["message"] == {"text": "backend 429"}
    assert result["level"] == "error"


def test_real_file_finding_keeps_its_location():
    result = _results(_log([_finding("real", "src/a.py", [3, 4], source="L0")]))["real"]

    assert result["locations"] == [
        {
            "physicalLocation": {
                "artifactLocation": {"uri": "src/a.py"},
                "region": {"startLine": 3, "endLine": 4},
            }
        }
    ]


def test_no_artifact_uri_contains_angle_brackets():
    log = _log([_finding("diag-%d" % i, s, [0, 0]) for i, s in enumerate(SENTINELS)])

    assert "<" not in json.dumps([r.get("locations", []) for r in log["runs"][0]["results"]])

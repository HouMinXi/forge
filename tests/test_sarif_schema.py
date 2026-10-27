# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The published SARIF log must validate against the official 2.1.0 schema.

Host diagnostics (invoke-fail, receipt, mutation infra) have no source file.
They used to name a sentinel such as ``<llm-invoke>`` as an artifact URI and
point at line 0. Neither is valid SARIF. GitHub code scanning also rejects the
whole upload when a result has no location ("expected at least one location"),
so every result keeps one: host diagnostics are anchored to the tracked
``.code-forge/gate.yaml`` at line 1.
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


HOST_DIAGNOSTIC_LOCATION = {
    "physicalLocation": {
        "artifactLocation": {"uri": ".code-forge/gate.yaml"},
        "region": {"startLine": 1, "endLine": 1},
    }
}


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_host_diagnostic_is_anchored_to_gate_yaml_and_keeps_rule_and_message(sentinel):
    result = _results(_log([_finding("diag", sentinel, [0, 0], description="backend 429")]))["diag"]

    assert result["locations"] == [HOST_DIAGNOSTIC_LOCATION]
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


def test_coverage_finding_with_zero_lines_validates_and_keeps_its_file():
    # coverage.py emits a real file with line_range [0, 0]; SARIF needs lines >= 1.
    log = _log([_finding("coverage:src/a.py", "src/a.py", [0, 0], source="COVERAGE")])

    errors = [e.message for e in jsonschema.Draft4Validator(SCHEMA).iter_errors(log)]

    assert errors == []
    assert _results(log)["coverage:src/a.py"]["locations"] == [
        {
            "physicalLocation": {
                "artifactLocation": {"uri": "src/a.py"},
                "region": {"startLine": 1, "endLine": 1},
            }
        }
    ]


@pytest.mark.parametrize(
    ("line_range", "expected"),
    [
        ([0, 0], (1, 1)),
        ([0, 7], (1, 7)),
        ([-3, -1], (1, 1)),
        ([0], (1, 1)),
        ([], (1, 1)),
        ([5, 0], (5, 5)),
        ([5, -2], (5, 5)),
        ([7, 3], (7, 7)),
        ([3, 3], (3, 3)),
        ([4, 9], (4, 9)),
        ([6], (6, 6)),
    ],
)
def test_region_lines_are_at_least_one_and_never_inverted(line_range, expected):
    result = _results(_log([_finding("real", "src/a.py", line_range, source="L0")]))["real"]
    region = result["locations"][0]["physicalLocation"]["region"]

    assert (region["startLine"], region["endLine"]) == expected


HALF_SENTINELS = ["<x", "x>", "a<b", "<>", "src/<gen>/a.py", " "]


@pytest.mark.parametrize("file", SENTINELS + HALF_SENTINELS)
def test_non_uri_file_values_use_the_anchor_and_are_marked_synthetic(file):
    # A half sentinel ("<x", "x>") is not a valid URI either; it must never
    # be emitted unchanged.
    log = _log([_finding("diag", file, [0, 0])])
    result = _results(log)["diag"]

    assert result["locations"] == [HOST_DIAGNOSTIC_LOCATION]
    assert result["properties"]["syntheticLocation"] is True
    assert [e.message for e in jsonschema.Draft4Validator(SCHEMA).iter_errors(log)] == []


@pytest.mark.parametrize(
    ("file", "line_range", "source"),
    [("src/a.py", [3, 4], "L0"), ("src/a.py", [0, 0], "COVERAGE"), ("a b.py", [2, 2], "L0")],
)
def test_real_file_results_are_not_marked_synthetic(file, line_range, source):
    result = _results(_log([_finding("real", file, line_range, source=source)]))["real"]

    assert "syntheticLocation" not in result["properties"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == file


def test_every_result_has_at_least_one_location():
    # GitHub code scanning rejects the upload for a result without a location.
    findings = [_finding("diag-%d" % i, s, [0, 0]) for i, s in enumerate(SENTINELS)]
    findings.append(_finding("real", "src/a.py", [3, 4], source="L0"))
    findings.append(_finding("coverage:src/b.py", "src/b.py", [0, 0], source="COVERAGE"))
    log = _log(findings)

    assert len(log["runs"][0]["results"]) == len(findings)
    assert all(len(r["locations"]) >= 1 for r in log["runs"][0]["results"])
    assert [e.message for e in jsonschema.Draft4Validator(SCHEMA).iter_errors(log)] == []


def test_no_artifact_uri_contains_angle_brackets_or_is_empty():
    log = _log([_finding("diag-%d" % i, s, [0, 0]) for i, s in enumerate(SENTINELS)])

    uris = [
        loc["physicalLocation"]["artifactLocation"]["uri"]
        for r in log["runs"][0]["results"]
        for loc in r["locations"]
    ]
    assert uris and all(u and "<" not in u and ">" not in u for u in uris)

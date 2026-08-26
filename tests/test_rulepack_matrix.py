# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Tests for RulepackMatrix serialization (Task 5).

Covers schema conformance (schema_version 1), atomic persistence to
.code-forge/rulepack-matrix.json, and the stderr summary line format.
"""
from __future__ import annotations

import json

from code_forge.rulepack import RulepackMatrix, RulepackRunner


def _sample_matrix() -> RulepackMatrix:
    return RulepackMatrix(
        packs=[
            {
                "name": "testpack",
                "version": "1.0",
                "source": "https://example.com/testpack",
                "rules": [
                    {"id": "rule-a", "status": "VIOLATION", "findings": 2},
                    {"id": "rule-b", "status": "CLEAN", "findings": 0},
                    {
                        "id": "ghost-rule",
                        "status": "NOT_RUN",
                        "reason": "rule missing from rules.yaml",
                        "findings": 0,
                    },
                ],
            }
        ]
    )


class TestToDictSchema:
    def test_schema_version_and_packs(self):
        d = _sample_matrix().to_dict()
        assert d["schema_version"] == 1
        assert isinstance(d["packs"], list)
        assert len(d["packs"]) == 1

    def test_rule_status_shape(self):
        d = _sample_matrix().to_dict()
        rule = d["packs"][0]["rules"][0]
        assert set(rule) == {"id", "status", "findings"}
        assert rule["status"] == "VIOLATION"

    def test_not_run_rule_carries_reason(self):
        d = _sample_matrix().to_dict()
        ghost = d["packs"][0]["rules"][2]
        assert ghost["status"] == "NOT_RUN"
        assert ghost["reason"] == "rule missing from rules.yaml"

    def test_empty_matrix(self):
        d = RulepackMatrix().to_dict()
        assert d["schema_version"] == 1
        assert d["packs"] == []


class TestAtomicPersistence:
    def test_write_then_readback(self, tmp_path):
        m = _sample_matrix()
        m.write(tmp_path)
        out = tmp_path / ".code-forge" / "rulepack-matrix.json"
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["packs"][0]["name"] == "testpack"

    def test_no_leftover_tmp_file(self, tmp_path):
        m = _sample_matrix()
        m.write(tmp_path)
        tmp = tmp_path / ".code-forge" / "rulepack-matrix.json.tmp"
        assert not tmp.exists()
        assert (tmp_path / ".code-forge" / "rulepack-matrix.json").exists()

    def test_overwrite_is_atomic(self, tmp_path):
        m1 = _sample_matrix()
        m1.write(tmp_path)
        m2 = RulepackMatrix(packs=[])
        m2.write(tmp_path)
        data = json.loads(
            (tmp_path / ".code-forge" / "rulepack-matrix.json")
            .read_text(encoding="utf-8")
        )
        assert data["packs"] == []


class TestStderrSummary:
    def test_summary_counts(self, capsys):
        # Build a matrix with all four states represented.
        runner = RulepackRunner()
        runner.matrix = _sample_matrix()
        runner._print_summary()
        err = capsys.readouterr().err
        assert "rulepack:" in err
        # 1 violation, 1 clean, 0 n/a, 1 not_run, 3 rules total.
        assert "3 rules" in err
        assert "1 violation" in err
        assert "1 clean" in err
        assert "0 n/a" in err
        assert "1 not_run" in err

    def test_no_matrix_prints_nothing(self, capsys):
        runner = RulepackRunner()
        runner.matrix = None
        runner._print_summary()
        assert capsys.readouterr().err == ""

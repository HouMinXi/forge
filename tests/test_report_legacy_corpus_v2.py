# SPDX-License-Identifier: Apache-2.0
"""Frozen complete output from the real pinned legacy CLI, not v2 helpers."""

import io
import json
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from code_forge import cli, mcp_server, report_presentation
from code_forge.report_baseline_v2 import RenderingInputs, SnapshotLimits, build_owned_report_pair
from code_forge.report_reader_v2 import ReaderLimits, read_report_view
from code_forge.state import load_state

CORPUS = json.loads((Path(__file__).parent / "fixtures/report_v2/legacy_cli_388e43b.json").read_text())


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["name"])
def test_real_cli_frozen_bytes_factory_and_old_readers(tmp_path, monkeypatch, case):
    path = tmp_path / "state.json"
    path.write_text(case["state_json"])
    before = path.read_bytes()
    source = load_state(path)
    assert source is not None
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    def unavailable(_):
        raise OSError("fixture locator unavailable")

    monkeypatch.setattr(cli, "_infra_state_identity", unavailable)
    refusal = (
        patch.object(report_presentation, "_block_fits", return_value=False)
        if case["force_classification_refusal"]
        else nullcontext()
    )
    hook = []
    with refusal:
        cli._emit_ci_output(
            path, {}, post_emit_hook=lambda: hook.append((out.getvalue(), err.getvalue()))
        )
        pair = build_owned_report_pair(
            source,
            RenderingInputs(CORPUS["forge_version"], {}),
            limits=SnapshotLimits(10000, 1000000, 100),
        )
    assert out.getvalue() == pair.baseline.stdout == case["stdout"]
    assert err.getvalue() == pair.baseline.stderr == case["stderr"]
    assert hook == [(case["stdout"], case["stderr"])]
    assert path.read_bytes() == before
    assert list(mcp_server._sarif_projection_with_status(case["stdout"])) == case["legacy_projection"]
    old = mcp_server._report_classification_from_stdout(case["stdout"])
    assert old == case["legacy_classification"]
    selected = read_report_view(
        case["stdout"].encode("utf-8"), requested_version="v1", limits=ReaderLimits(4194304, 80, 10000)
    )
    assert selected.envelope_condition == "accepted"
    if case["force_classification_refusal"]:
        assert selected.selected_version is None
    else:
        assert selected.selected_version == "v1"
        assert selected.block == old

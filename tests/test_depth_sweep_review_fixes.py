# SPDX-License-Identifier: Apache-2.0
"""Review round-1 findings on feat/depth-sweep (2026-09-05).

Three CONFIRMED findings, each with a test that is red on the pre-fix
tree and green after:

1. analyse_arms.py filtered wall_s by truthiness, so a measured 0.0 was
   dropped from the mean and printed as "n/a".
2. pool.py applied env_overrides to os.environ and never restored it, on
   both the serial path and inside _worker. In the CLI each arm is its
   own process so nothing leaked; a programmatic caller running two arms
   in one process inherited the first arm's knobs.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent


def _load_analyse_arms():
    spec = importlib.util.spec_from_file_location(
        "analyse_arms", _ROOT / "scripts" / "analyse_arms.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestWallZeroIsAMeasurement:
    def test_zero_wall_counts_toward_mean(self):
        mod = _load_analyse_arms()
        rows = [
            {"entry_id": "a-bug", "verdict": "HOLD", "wall_s": 0.0,
             "depth": 1, "engine": "real"},
            {"entry_id": "b-bug", "verdict": "HOLD", "wall_s": 4.0,
             "depth": 1, "engine": "real"},
        ]
        s = mod.summarise(rows, "x.jsonl")
        # Two measurements, mean 2.0. Truthiness filtering gave 4.0.
        assert s["wall_mean"] == 2.0

    def test_zero_mean_prints_as_number_not_na(self, capsys):
        mod = _load_analyse_arms()
        rows = [{"entry_id": "a-bug", "verdict": "HOLD", "wall_s": 0.0,
                 "depth": 1, "engine": "real"}]
        s = mod.summarise(rows, "x.jsonl")
        mod.print_arm(s, Path("x.jsonl"))
        out = capsys.readouterr().out
        assert "wall/entry: 0 s" in out
        assert "n/a s" not in out


class TestEnvOverridesRestored:
    def test_serial_path_restores_environ(self, tmp_path, monkeypatch):
        from code_forge.eval import pool as pool_mod
        from code_forge.eval.corpus import CorpusEntry
        monkeypatch.delenv("FORGE_CLEAN_ROUND_THRESHOLD", raising=False)
        entry = CorpusEntry(
            name="e", diff_file="d.diff", expected_verdict="PASS",
            axis_tags=[])
        with patch.object(pool_mod, "replay_entry", return_value=None):
            pool_mod.run_pool(
                [entry], corpus_dir=tmp_path, backend_name="b", runs=1,
                backend_config=None, jobs=1,
                env_overrides={"FORGE_CLEAN_ROUND_THRESHOLD": "3"})
        assert "FORGE_CLEAN_ROUND_THRESHOLD" not in os.environ

    def test_serial_path_restores_prior_value(self, tmp_path, monkeypatch):
        from code_forge.eval import pool as pool_mod
        from code_forge.eval.corpus import CorpusEntry
        monkeypatch.setenv("FORGE_CLEAN_ROUND_THRESHOLD", "1")
        entry = CorpusEntry(
            name="e", diff_file="d.diff", expected_verdict="PASS",
            axis_tags=[])
        with patch.object(pool_mod, "replay_entry", return_value=None):
            pool_mod.run_pool(
                [entry], corpus_dir=tmp_path, backend_name="b", runs=1,
                backend_config=None, jobs=1,
                env_overrides={"FORGE_CLEAN_ROUND_THRESHOLD": "3"})
        assert os.environ["FORGE_CLEAN_ROUND_THRESHOLD"] == "1"

    def test_worker_restores_environ(self, tmp_path, monkeypatch):
        from code_forge.eval import pool as pool_mod
        from code_forge.eval.corpus import CorpusEntry
        monkeypatch.delenv("FORGE_CLEAN_ROUND_THRESHOLD", raising=False)
        entry = CorpusEntry(
            name="e", diff_file="d.diff", expected_verdict="PASS",
            axis_tags=[])
        with patch.object(pool_mod, "replay_entry", return_value=None):
            pool_mod._worker(
                entry, str(tmp_path), "b", 1, None,
                env_overrides={"FORGE_CLEAN_ROUND_THRESHOLD": "3"})
        assert "FORGE_CLEAN_ROUND_THRESHOLD" not in os.environ

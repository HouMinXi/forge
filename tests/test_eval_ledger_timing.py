"""The resume ledger has to reach disk before the run finishes.

58-2 built an append-only ledger so a killed corpus run resumes instead of
restarting. 58-3 launched a 150-entry arm with it and, three entries in, the
ledger file did not exist: rows were written from run_pool's return value,
and run_pool only returns once every entry is done. An arm killed at entry
149 would have resumed from zero after five hours of review spend.

The tests below check the property that matters -- rows are on disk while
the run is still going -- rather than that a particular function is called.
"""
import json



class _StopAfter(Exception):
    """Abort a pool run once enough entries have landed."""


def _entry(name):
    from code_forge.eval.runner import CorpusEntry

    return CorpusEntry(
        name=name,
        diff_file="unused.diff",
        expected_verdict="HOLD",
        axis_tags=[],
    )


def _result_for(entry, verdict="HOLD"):
    from code_forge.eval.runner import EvalResult

    return EvalResult(
        entry=entry,
        actual_verdict=verdict,
        runs=1,
        caught_count=1,
        skipped_reason="",
    )


class TestLedgerLandsDuringTheRun:
    """Rows must be on disk before the last entry finishes."""

    def test_rows_exist_while_entries_are_still_running(
        self, tmp_path, monkeypatch
    ):
        # The regression, stated as the operator sees it: kill the run after
        # two of five entries and the ledger has two rows. With rows written
        # from run_pool's return value the file did not exist at all.
        from code_forge.eval import pool as pool_mod

        ledger = tmp_path / "arm.jsonl"
        entries = [_entry("e%d" % i) for i in range(5)]
        seen = {"n": 0}

        def _replay(entry, **kwargs):
            seen["n"] += 1
            if seen["n"] > 2:
                raise _StopAfter("killed after two entries")
            return _result_for(entry)

        recorded = []

        def _progress(done, total, name, wall_s, pool_entry=None):
            # Mirrors the shape cli._progress uses to write rows.
            if pool_entry is not None and pool_entry.result is not None:
                from code_forge.eval.ledger_jsonl import (
                    ResumeKey, append_record, make_record,
                )

                key = ResumeKey(
                    entry_id=name, depth=1, engine="real", backend="b",
                )
                append_record(ledger, make_record(
                    key, pool_entry.result.actual_verdict, runs=1,
                    caught=1, wall_s=wall_s, skipped_reason="",
                ))
                recorded.append(name)

        monkeypatch.setattr(pool_mod, "replay_entry", _replay)
        pool_mod.run_pool(
            entries,
            corpus_dir=tmp_path,
            backend_name="b",
            runs=1,
            backend_config=None,
            jobs=1,
            progress_cb=_progress,
        )

        assert ledger.exists(), (
            "no ledger file after five entries; a killed arm would resume "
            "from zero"
        )
        rows = [
            json.loads(line)
            for line in ledger.read_text().splitlines() if line.strip()
        ]
        assert len(rows) == 2, (
            "expected the two entries that completed before the failure to "
            "be on disk, found %d" % len(rows)
        )

    def test_progress_callback_receives_the_pool_entry(self, tmp_path,
                                                       monkeypatch):
        # The mechanism the fix depends on: without the entry, the callback
        # has a name and a duration but no verdict to record.
        from code_forge.eval import pool as pool_mod

        got = []

        def _replay(entry, **kwargs):
            return _result_for(entry)

        monkeypatch.setattr(pool_mod, "replay_entry", _replay)
        pool_mod.run_pool(
            [_entry("only")],
            corpus_dir=tmp_path,
            backend_name="b",
            runs=1,
            backend_config=None,
            jobs=1,
            progress_cb=lambda *a: got.append(a),
        )
        assert got, "progress callback never fired"
        assert len(got[0]) == 5, (
            "callback got %d arguments; the fifth is the PoolEntry the "
            "ledger write needs" % len(got[0])
        )
        assert got[0][4].result is not None


class TestCliWiring:
    """The shipped callback has to do what the tests above simulate."""

    def test_progress_writes_the_ledger(self):
        from pathlib import Path

        import code_forge.cli as cli_mod

        src = Path(cli_mod.__file__).read_text()
        head = src.split("pool_results = run_pool(")[0]
        assert "_record(pool_entry.entry, pool_entry.result, wall_s)" in head, (
            "cli._progress must record each entry as it lands, not after "
            "run_pool returns"
        )

    def test_summary_loop_does_not_double_record(self):
        from pathlib import Path

        import code_forge.cli as cli_mod

        src = Path(cli_mod.__file__).read_text()
        after = src.split("pool_results = run_pool(")[1].split("    else:")[0]
        assert "_record(" not in after, (
            "the post-run loop still records; every entry would appear "
            "twice in the ledger"
        )

    def test_pool_passes_the_entry_on_both_paths(self):
        import inspect

        from code_forge.eval import pool as pool_mod

        src = inspect.getsource(pool_mod.run_pool)
        assert src.count("progress_cb(") == 2
        assert src.count(", pe)") == 2, (
            "both the serial and parallel progress calls must pass the "
            "PoolEntry"
        )

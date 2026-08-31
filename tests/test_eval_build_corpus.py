"""The generator must be runnable, and must refuse to clobber.

The provenance record states a seed and a per-repo cap. Those describe a
reproducible selection only if something can consume them again; until
this module existed, the only path that had ever built the corpus was an
ad-hoc shell one-liner that did not survive the session.

Network is never touched here. The dataset load is the one line these
tests stub; everything below it is real.
"""

import sys
import types

from code_forge.eval import build_corpus as gen


def _fake_dataset(rows):
    """Install a datasets module whose load_dataset returns rows."""
    mod = types.ModuleType("datasets")
    mod.load_dataset = lambda *a, **kw: rows  # type: ignore[attr-defined]
    return mod


_PATCH = (
    "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
    "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n tail\n"
)


def _row(iid, repo="o/r"):
    return {
        "instance_id": iid,
        "repo": repo,
        "patch": _PATCH,
        "problem_statement": "A clear defect title that is long enough.\n\nBody.",
    }


class TestGenerator:
    def test_it_builds_a_corpus_without_network(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setitem(
            sys.modules, "datasets", _fake_dataset([_row("a-1"), _row("a-2")])
        )
        out = tmp_path / "corpus"
        rc = gen.main(["--out", str(out), "--cap", "8", "--seed", "1"])
        assert rc == 0
        assert (out / "corpus.yaml").exists()
        assert (out / "PROVENANCE.json").exists()
        # Two instances, each producing a bug entry and a clean control.
        assert "wrote 4 entries" in capsys.readouterr().out

    def test_it_refuses_to_overwrite(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "datasets", _fake_dataset([_row("a-1")]))
        out = tmp_path / "corpus"
        out.mkdir()
        rc = gen.main(["--out", str(out)])
        assert rc == 2
        assert "already exists" in capsys.readouterr().err

    def test_a_missing_extra_says_which_extra(self, tmp_path, monkeypatch, capsys):
        # Absent, not broken: the evaluator does not need datasets, so the
        # message has to distinguish the generator from the thing most
        # people run.
        monkeypatch.setitem(sys.modules, "datasets", None)
        rc = gen.main(["--out", str(tmp_path / "corpus")])
        assert rc == 2
        assert "eval-corpus" in capsys.readouterr().err

    def test_the_seed_reaches_the_provenance(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setitem(sys.modules, "datasets", _fake_dataset([_row("a-1")]))
        out = tmp_path / "corpus"
        gen.main(["--out", str(out), "--cap", "3", "--seed", "77"])
        prov = json.loads((out / "PROVENANCE.json").read_text(encoding="utf-8"))
        assert prov["seed"] == 77
        assert prov["cap"] == 3


class TestTheCommittedCorpusMatchesItsRecord:
    """The provenance hashes must describe the files actually on disk.

    Not a network test: it reads the committed corpus and the committed
    record and checks they agree. A regenerated corpus that forgot to
    update PROVENANCE.json, or a hand-edited diff, both show up here.
    """

    def test_every_diff_matches_its_recorded_hash(self):
        import hashlib
        import json
        import pathlib

        root = pathlib.Path(__file__).parent / "eval" / "swebench"
        prov = json.loads((root / "PROVENANCE.json").read_text(encoding="utf-8"))
        recorded = prov["diff_sha256"]

        assert recorded, "provenance carries no hashes"
        for rel, expected in recorded.items():
            # Keys are paths relative to the corpus root, as written.
            actual = hashlib.sha256((root / rel).read_bytes()).hexdigest()
            assert actual == expected, rel

    def test_the_entry_count_matches_the_split(self):
        import json
        import pathlib

        import yaml

        root = pathlib.Path(__file__).parent / "eval" / "swebench"
        prov = json.loads((root / "PROVENANCE.json").read_text(encoding="utf-8"))
        entries = yaml.safe_load((root / "corpus.yaml").read_text(encoding="utf-8"))["entries"]

        hold = [e for e in entries if e["expected_verdict"] == "HOLD"]
        clean = [e for e in entries if e["expected_verdict"] == "PASS"]
        assert len(hold) == prov["hold_entries"]
        assert len(clean) == prov["pass_entries"]
        # The clean controls are the half that makes precision measurable;
        # losing them would leave every ratio at 1.0 and look like success.
        assert len(clean) > 0


class TestSelectionIsPinned:
    """The seed must produce the same selection, not merely be recorded.

    Review t_3848264c: the byte-for-byte rebuild happened once, by hand,
    on one machine. Nothing in the suite held it. These do -- without the
    dataset, by driving the selection over synthetic instances.
    """

    def test_the_same_seed_selects_the_same_instances(self):
        from code_forge.eval.swebench import select_instances

        pool = [
            {"instance_id": "r%d-%d" % (r, i), "repo": "o/r%d" % r}
            for r in range(5)
            for i in range(20)
        ]
        first = [x["instance_id"] for x in select_instances(pool, cap=8)]
        second = [x["instance_id"] for x in select_instances(pool, cap=8)]
        assert first == second

    def test_input_order_does_not_change_the_selection(self):
        import random

        from code_forge.eval.swebench import select_instances

        pool = [
            {"instance_id": "r%d-%d" % (r, i), "repo": "o/r%d" % r}
            for r in range(5)
            for i in range(20)
        ]
        shuffled = list(pool)
        random.Random(99).shuffle(shuffled)
        # The dataset's iteration order is not a contract; a corpus that
        # cannot be regenerated cannot be audited.
        assert sorted(x["instance_id"] for x in select_instances(pool, cap=8)) == sorted(
            x["instance_id"] for x in select_instances(shuffled, cap=8)
        )

    def test_the_committed_selection_matches_its_manifest(self):
        import pathlib

        import yaml

        root = pathlib.Path(__file__).parent / "eval" / "swebench"
        entries = yaml.safe_load(
            (root / "corpus.yaml").read_text(encoding="utf-8")
        )["entries"]
        names = {e["name"] for e in entries}
        # Every committed diff is claimed by an entry, and every entry has
        # its diff. A regeneration that dropped or orphaned files fails here.
        on_disk = {p.stem for p in (root / "diffs").glob("*.diff")}
        assert names == on_disk

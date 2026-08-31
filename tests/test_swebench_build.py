"""Phase 57-0 and 57-5: generate the corpus, and record how.

The provenance file is the part that makes the numbers auditable. A corpus
assembled after seeing the scores is not a corpus, it is a result -- and
from the outside those two look identical unless the assembly is written
down first and hashed.
"""

import json

import pytest

from code_forge.eval.corpus import load_corpus
from code_forge.eval.swebench import build_corpus


def _inst(iid, repo="org/proj", path="m.py"):
    return {
        "instance_id": iid,
        "repo": repo,
        "patch": (
            "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n"
            "@@ -2,3 +2,3 @@\n ctx\n-bad\n+good\n tail\n" % (path, path, path, path)
        ),
        "problem_statement": "A defect title long enough to qualify\n\nbody",
    }


class TestEmitsBothShapes:
    def test_each_instance_yields_a_hold_and_a_pass(self, tmp_path):
        build_corpus([_inst("a-1"), _inst("a-2")], tmp_path, rejections=[])
        entries = load_corpus(tmp_path / "corpus.yaml")
        verdicts = sorted(e.expected_verdict for e in entries)
        assert verdicts == ["HOLD", "HOLD", "PASS", "PASS"]

    def test_the_hold_entry_reviews_the_reversed_patch(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        hold = [
            e for e in load_corpus(tmp_path / "corpus.yaml")
            if e.expected_verdict == "HOLD"
        ][0]
        diff = (tmp_path / hold.diff_file).read_text()
        # Reversed: the fix's removal becomes an addition.
        assert "+bad" in diff
        assert "-good" in diff

    def test_the_pass_entry_reviews_the_fix_itself(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        clean = [
            e for e in load_corpus(tmp_path / "corpus.yaml")
            if e.expected_verdict == "PASS"
        ][0]
        diff = (tmp_path / clean.diff_file).read_text()
        assert "+good" in diff
        assert "-bad" in diff

    def test_clean_entries_assert_no_findings(self):
        """Without the flag they would be scored as unannotated.

        That is the inert-controls bug: a clean entry that does not assert
        emptiness contributes nothing to findings_fp, and precision stays
        at 1.0 no matter how noisy the reviewer is.
        """
        import tempfile
        import pathlib

        d = pathlib.Path(tempfile.mkdtemp())
        build_corpus([_inst("a-1")], d, rejections=[])
        clean = [
            e for e in load_corpus(d / "corpus.yaml")
            if e.expected_verdict == "PASS"
        ][0]
        assert clean.asserts_no_findings is True
        assert clean.expected_findings == []

    def test_hold_entries_carry_the_answer_key(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        hold = [
            e for e in load_corpus(tmp_path / "corpus.yaml")
            if e.expected_verdict == "HOLD"
        ][0]
        assert len(hold.expected_findings) == 1
        assert hold.expected_findings[0].file == "m.py"


class TestBaseFiles:
    def test_each_entry_gets_a_base_tree(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        entries = load_corpus(tmp_path / "corpus.yaml")
        for e in entries:
            assert (tmp_path / "base_files" / e.name / "m.py").exists()

    def test_both_shapes_apply_against_their_base(self, tmp_path):
        """The check that would have caught a corpus that skips everything.

        A manifest can load perfectly and still fail every replay, and the
        skip count is the only place that shows.
        """
        import subprocess

        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        for e in load_corpus(tmp_path / "corpus.yaml"):
            work = tmp_path / "work" / e.name
            work.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
            src = tmp_path / "base_files" / e.name
            subprocess.run(
                ["cp", "-r", "%s/." % src, str(work)], check=True
            )
            subprocess.run(["git", "add", "-A"], cwd=work, check=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-qm", "b", "--allow-empty"],
                cwd=work, check=True,
            )
            rc = subprocess.run(
                ["git", "apply", "--check", str(tmp_path / e.diff_file)],
                cwd=work, capture_output=True,
            )
            assert rc.returncode == 0, (e.name, rc.stderr.decode())


class TestProvenance:
    def test_records_the_filters_and_their_counts(self, tmp_path):
        rejected = [
            (_inst("r-1"), "pure_addition"),
            (_inst("r-2"), "too_many_hunks"),
        ]
        build_corpus([_inst("a-1")], tmp_path, rejections=[r[1] for r in rejected])
        prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
        assert prov["rejections"]["pure_addition"] == 1
        assert prov["rejections"]["too_many_hunks"] == 1

    def test_hashes_every_generated_diff(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
        entries = load_corpus(tmp_path / "corpus.yaml")
        assert set(prov["diff_sha256"]) == {e.diff_file for e in entries}
        for rel, digest in prov["diff_sha256"].items():
            import hashlib

            actual = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
            assert actual == digest

    def test_records_the_split(self, tmp_path):
        """Precision cannot be read without knowing how many entries
        could have produced a false positive."""
        build_corpus([_inst("a-1"), _inst("a-2")], tmp_path, rejections=[])
        prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
        assert prov["hold_entries"] == 2
        assert prov["pass_entries"] == 2

    def test_records_the_selection_parameters(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[], cap=8, seed=20260830)
        prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
        assert prov["cap"] == 8
        assert prov["seed"] == 20260830

    def test_states_the_diff_mode_limitation(self, tmp_path):
        """Reconstructed stubs do not parse as Python.

        Harmless for diff-mode review and fatal for whole-file analysis,
        so a future pass that adds the latter meets a recorded limitation
        rather than a mystery.
        """
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
        assert "diff" in prov["limitations"].lower()


class TestRefusesToOverwrite:
    def test_will_not_clobber_an_existing_corpus(self, tmp_path):
        build_corpus([_inst("a-1")], tmp_path, rejections=[])
        with pytest.raises(FileExistsError):
            build_corpus([_inst("a-2")], tmp_path, rejections=[])

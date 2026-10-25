"""#183: each bounded FIXVAL test run is preceded by a progress line."""

from types import SimpleNamespace

from code_forge import fixval
from code_forge.fixval import FixvalCandidate


def _spy(monkeypatch, returncode):
    events = []
    monkeypatch.setattr(fixval.progress, "emit", lambda msg: events.append(("emit", msg)))

    def run(cmd, **kw):
        events.append(("run", kw.get("timeout")))
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(fixval.subprocess, "run", run)
    return events


def test_reverted_fix_run_emits_progress_first(monkeypatch, tmp_path):
    events = _spy(monkeypatch, 1)
    candidate = FixvalCandidate(test_files=["tests/test_m.py"], non_test_files=["src/m.py"])
    fixval._test_reverted_candidate(candidate, ["pytest"], {}, str(tmp_path))
    assert events == [("emit", "fixval: reverted-fix run"), ("run", 600)]


def test_overfit_run_emits_progress_first(monkeypatch, tmp_path):
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir()
    src.write_text("def compute():\n    result = 42\n    return result\n")
    events = _spy(monkeypatch, 0)
    candidate = FixvalCandidate(test_files=["tests/test_module.py"], non_test_files=[str(src)])
    fixval.run_overfit_guard(candidate, test_cmd=["pytest"], cwd=tmp_path)
    assert events == [("emit", "fixval: overfit run"), ("run", 600)]

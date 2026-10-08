"""Offline finite natural-adapter guards, without inventory scaffolding."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import facts  # noqa: E402
MODULE = Path(facts.__file__)

def test_unreadable_guard_is_not_false(monkeypatch):
    def denied(path):
        raise PermissionError("denied")
    monkeypatch.setattr(facts.os, "stat", denied)
    with pytest.raises(facts.FactError, match="unknown adapter eligibility"):
        facts.guard_path("/example", "isdir")


def test_natural_guards_are_source_checked_without_imports(monkeypatch):
    class SourceReader:
        def read(self, path):
            return Path(path).read_bytes()
    monkeypatch.setattr(facts, "guard_path", lambda path, kind: dict(path=path, kind=kind, matches=False))
    result = facts.collect_guards(MODULE.parents[3], SourceReader(), uid=1001)
    assert result["unexpectedly_eligible"] == []
    python = result["adapters"]["python"]["inputs"]
    assert python[0]["path"].endswith("user-1000.slice/user@1000.service")
    assert "user-1001.slice" in result["adapters"]["javascript"]["inputs"][0]["path"]


def test_extra_adapter_eligibility_is_reported_not_forced_skipped(monkeypatch):
    class SourceReader:
        def read(self, path):
            return Path(path).read_bytes()
    monkeypatch.setattr(facts, "guard_path", lambda path, kind: dict(path=path, kind=kind, matches=True))
    result = facts.collect_guards(MODULE.parents[3], SourceReader(), uid=1001)
    assert set(result["unexpectedly_eligible"]) == set(facts.GUARDS)


def test_changed_guard_is_refused(monkeypatch):
    class SourceReader:
        def read(self, path):
            return Path(path).read_bytes().replace(b"not (os.path.isdir(CGROUP_ROOT)", b"not (False")
    with pytest.raises(facts.FactError, match="predicate changed"):
        facts.collect_guards(MODULE.parents[3], SourceReader(), uid=1001)


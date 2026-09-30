"""PSMutant report mapping.

Status values come from schemas/v2/report.schema.json on
github.com/Fortigi/PSMutant. The mutant object only allows Killed and
Survived. A timed-out mutant is counted Killed by the tool, so this
mapper does not invent a third status.
"""

import pytest

from code_forge.mutation_engines.adapters.ps_mutant import map_psmutant_status
from code_forge.mutation_engines.schemas import NormalizedStatus


@pytest.mark.parametrize(
    "native,expected",
    [
        ("Killed", NormalizedStatus.KILLED),
        ("Survived", NormalizedStatus.SURVIVED),
    ],
)
def test_documented_statuses(native, expected):
    assert map_psmutant_status(native) is expected


def test_unknown_status_is_not_killed():
    assert map_psmutant_status("TimedOut") is NormalizedStatus.UNKNOWN
    assert map_psmutant_status("") is NormalizedStatus.UNKNOWN


def test_gate_invokes_when_probe_is_available(tmp_path, monkeypatch):
    from code_forge.mutation_dispatch import run_note

    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.ps_mutant.PSMutantAdapter.invoke",
        lambda self, root: type("R", (), {"outcomes": (), "reason": "no pester"})(),
    )
    note = run_note(["a.ps1"], tmp_path)
    assert "ps-mutant no pester" in note


def test_run_invokes_pwsh_and_does_not_invent_a_score(tmp_path, monkeypatch):
    """run must call pwsh. No Pester file means no score, not a pass."""
    import subprocess

    from code_forge.mutation_engines.adapters.ps_mutant import PSMutantAdapter

    seen = []

    def fake_run(argv, **kwargs):
        del kwargs
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = PSMutantAdapter().invoke(tmp_path)
    assert seen and seen[0][0] == "pwsh"
    assert result.outcomes == ()
    assert "no pester" in result.reason

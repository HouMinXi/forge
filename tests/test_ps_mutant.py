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

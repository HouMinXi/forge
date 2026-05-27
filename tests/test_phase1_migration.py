# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""H2: Verify Phase 1 old API (read_state/write_state) fully migrated."""

import subprocess
from pathlib import Path


class TestPhase1Migration:
    """Old read_state/write_state must not appear in src/code_forge/."""

    _SRC_DIR = Path(__file__).parent.parent / "src" / "code_forge"

    def test_no_old_api_references(self):
        """git grep for read_state|write_state returns no matches."""
        result = subprocess.run(
            ["grep", "-rnE", "read_state|write_state", str(self._SRC_DIR)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            "Old API references found in src/code_forge/:\n%s" % result.stdout
        )
        assert result.stdout == ""

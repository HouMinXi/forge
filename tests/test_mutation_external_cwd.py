# SPDX-License-Identifier: Apache-2.0
"""A mutated script started outside the mirror must not die on stats collection.

mutmut rewrites the source into a trampoline and sets MUTANT_UNDER_TEST=stats
while it measures which tests touch which function. A test that launches the
script as a subprocess from a working directory outside the mirror inherits
that variable. The trampoline then asks mutmut for its configuration, and
mutmut looks for setup.cfg in the subprocess's own working directory, which
has none. Stats collection aborts before the script's own code runs, and the
whole mutation round is reported as MUTATION_ERROR.

The guard belongs in the mutation runner, not in each product test.
"""

import os
import sys
import textwrap
from pathlib import Path

import pytest

from code_forge import mutation

pytest.importorskip("mutmut")


def _project(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg/__init__.py").write_text("", encoding="utf-8")
    (root / "pkg/tool.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_tool.py").write_text(
        textwrap.dedent(
            """\
            import os
            import subprocess
            import sys

            def test_launches_outside_the_mirror(tmp_path):
                script_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                completed = subprocess.run(
                    [sys.executable, "-c", "import pkg.tool; print(pkg.tool.answer())"],
                    cwd=tmp_path,
                    env={**os.environ, "PYTHONPATH": script_root},
                    capture_output=True,
                    text=True,
                )
                assert completed.returncode == 0, completed.stderr
                assert completed.stdout.strip() == "1"
            """
        ),
        encoding="utf-8",
    )


def test_stats_collection_survives_a_subprocess_outside_the_mirror(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    monkeypatch.delenv("MUTMUT_DEPENDENCY_DEPTH", raising=False)

    findings, infra = mutation.run_mutation(
        ["pkg/tool.py"],
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "tests"],
        cwd=project,
        timeout=180,
        baseline_timeout=60,
    )

    descriptions = " ".join(item.description for item in findings)
    assert "could not collect stats" not in descriptions
    assert not any("FileNotFoundError" in item for item in infra)
    assert not (project / "mutants").exists()
    leaked = os.environ.get("MUTANT_UNDER_TEST")
    assert not leaked, leaked

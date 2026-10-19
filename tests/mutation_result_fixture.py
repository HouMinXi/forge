"""Explicit native-shaped data for tests that replace the external tool."""

import json


def write_inventory(root, relative, mutants=None):
    mutants = {"x_example__mutmut_1": "killed"} if mutants is None else mutants
    mirror = root / "mutants" / relative
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("\n".join(f"def {name}():\n    return 1" for name in mutants))
    module = relative.removesuffix(".py").replace("/", ".").removeprefix("src.")
    records = {f"{module}.{name}".replace(".__init__.", "."): status for name, status in mutants.items()}
    exits = {name: 0 if status == "survived" else 1 for name, status in records.items()}
    mirror.with_name(mirror.name + ".meta").write_text(
        json.dumps(
            {
                "exit_code_by_key": exits,
                "durations_by_key": {},
                "estimated_durations_by_key": {},
                "type_check_error_by_key": {},
            }
        )
    )
    return "\n".join(f"{name}: {status}" for name, status in records.items())


def working_review(root, rel, *, base="def sample():\n    return 0\n"):
    """A real WORKING git review of ``rel`` under ``root`` (#208).

    Since e89d226 a native mutation measurement is bound to a WORKING review
    with base/head SHAs and its diff; a fixture without one fails closed as
    "invalid bound mutation measurement". Commits ``base`` and leaves the
    current content (created as ``return 1`` if absent) in the worktree."""
    import subprocess
    from pathlib import Path

    from code_forge.baseline import GitRefBaseline, resolve_baseline

    source = Path(root) / rel
    if source.exists():
        reviewed = source.read_text(encoding="utf-8")
    else:
        source.parent.mkdir(parents=True, exist_ok=True)
        reviewed = base.replace("return 0", "return 1")

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=/dev/null", *args],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-q")
    source.write_text(base, encoding="utf-8")
    git("add", rel)
    git("commit", "-q", "-m", "base")
    source.write_text(reviewed, encoding="utf-8")
    head = git("rev-parse", "HEAD")
    return resolve_baseline(GitRefBaseline(head), GitRefBaseline("WORKING"), [Path(rel)], root)

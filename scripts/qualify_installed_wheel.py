#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build/install offline and run the bounded wheel roster outside the checkout.

Provision wheelhouse separately. This command never downloads dependencies.
The output directory must not exist and must be outside the checkout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import venv
import xml.etree.ElementTree as ET

ROSTER = {
    "test_installed_modules_match_wheel_record", "test_installed_resources",
    "test_source_shadow_is_rejected", "test_editable_metadata_is_rejected",
    "test_network_and_process_tripwires", "test_console_help",
    "test_console_empty_ledger_is_read_only", "test_console_seeded_ledger_is_read_only",
    "test_record_and_wheel_mismatch_rejected", "test_roster_rejects_missing_and_skipped_cases",
    "test_ci_malformed_result_persists_failure", "test_ci_synthetic_confirmed_finding_reaches_ledger",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_report(report):
    cases = ET.parse(report).findall(".//testcase")  # noqa: S314 - our own local pytest output
    names = [case.get("name") for case in cases]
    if len(names) != len(ROSTER) or set(names) != ROSTER:
        raise RuntimeError(f"unexpected wheel-control roster: {names}")
    if any(list(case) for case in cases):
        raise RuntimeError("wheel controls must have zero skips, failures, or errors")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    wheelhouse = args.wheelhouse.resolve(strict=True)
    if output.is_relative_to(root):
        parser.error("output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    home = output / "home"
    home.mkdir()
    git = shutil.which("git")
    if git is None:
        parser.error("local Git is required")
    env = {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8",
    }

    def run(argv, *, cwd=output, timeout=180):
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
        with (output / "commands.log").open("a") as log:
            log.write(json.dumps([str(arg) for arg in argv]) + "\n" + result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"command failed ({result.returncode}); see {output / 'commands.log'}")
        return result.stdout

    # Product bytes must agree with the archived commit; controls themselves may
    # be under review, and their exact hashes are recorded separately below.
    run([git, "diff", "--exit-code", "HEAD", "--", "src", "pyproject.toml", "MANIFEST.in", "README.md", "LICENSE"], cwd=root)
    commit = run([git, "rev-parse", "HEAD"], cwd=root).strip()
    archive = output / "source.tar"
    run([git, "archive", "--format=tar", "--output", str(archive), commit], cwd=root)
    source = output / "source"
    source.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(source, filter="data")
    controls = output / "controls"
    shutil.copytree(root / "qualification/installed_wheel", controls,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copyfile(Path(__file__), controls / "wheel_harness.py")
    environment = output / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
    python = str(environment / "bin/python")
    run([python, "-I", "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse),
         "setuptools>=77", "wheel", "pytest==9.1.1", "pyyaml>=6.0", "unidiff>=0.7.5,<0.8.0"])
    dist = output / "dist"
    dist.mkdir()
    run([python, "-I", "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
         "--wheel-dir", str(dist), str(source)])
    wheels = list(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one built wheel: {wheels}")
    wheel = wheels[0]
    run([python, "-I", "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)])
    sandbox = output / "sandbox"
    sandbox.mkdir()
    seed = sandbox / "seed"
    run([git, "init", "--quiet", "--template=", str(seed)])
    run([git, "-c", "user.name=Minxi Hou", "-c", "user.email=houminxi@gmail.com",
         "commit", "--allow-empty", "--quiet", "-m", "isolated wheel fixture"], cwd=seed)
    fixture_rev = run([git, "rev-parse", "HEAD"], cwd=seed).strip()
    binaries = output / "bin"
    binaries.mkdir()
    (binaries / "git").symlink_to(Path(git).resolve())
    env.update({
        "PATH": str(environment / "bin") + os.pathsep + str(binaries), "WHEEL_SAFE_PATH": str(environment / "bin") + os.pathsep + str(binaries), "WHEEL_SANDBOX": str(sandbox), "WHEEL_GIT": str(Path(git).resolve()),
        "WHEEL_GIT_CONFIG_SHA256": digest(seed / ".git/config"), "WHEEL_FIXTURE_REV": fixture_rev, "WHEEL_ARTIFACT": str(wheel), "WHEEL_RUNTIME_PROOF": str(output / "runtime-proof.json"),
        "WHEEL_FORBIDDEN_PATHS": json.dumps([str(root), str(source)]),
        "GIT_CEILING_DIRECTORIES": str(output),
    })
    proof = {
        "source_commit": commit, "source_archive_sha256": digest(archive),
        "wheel": wheel.name, "wheel_sha256": digest(wheel),
        "harness_sha256": digest(Path(__file__)),
        "controls": {str(p.relative_to(controls)): digest(p) for p in controls.rglob("*") if p.is_file()},
        "dependency_wheels": {p.name: digest(p) for p in wheelhouse.glob("*.whl")},
        "scope": "partial installed-wheel runtime; no native mutation/ABA/reload/phase/coverage acceptance",
        "expected_roster": sorted(ROSTER),
    }
    (output / "build-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    report = output / "pytest.xml"
    run([python, "-I", "-m", "pytest", "-c", str(controls / "pytest.ini"),
         "--confcutdir", str(controls), "--basetemp", str(sandbox / "pytest"),
         "--junitxml", str(report), str(controls)], cwd=controls, timeout=120)
    count = verify_report(report)
    print(f"PASS: {count} installed-wheel controls; evidence: {output}")


if __name__ == "__main__":
    main()

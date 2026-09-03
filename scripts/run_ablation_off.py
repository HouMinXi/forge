#!/usr/bin/env python3
"""Phase 58-0: Run ONE corpus entry through ablation-off configuration.

Ablation-off: FORGE_FALSIFICATION_ENGINE=stub, no fixture.
This means every L1 candidate is promoted to CONFIRMED (StubFalsifier
with no fixture defaults to CONFIRMED -- Fact 2).

Records: rounds taken, final verdict, wall time.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

# Resolve paths
REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "tests" / "eval" / "swebench"
MANIFEST = CORPUS_DIR / "corpus.yaml"

# Pick a small bug entry for the single run
ENTRY_NAME = "astropy__astropy-14096-bug"
DIFF_FILE = "diffs/astropy__astropy-14096-bug.diff"

# Backend to use (review-default from gate.yaml: mimo-v2.5-pro direct)
BACKEND_NAME = "review-default"


def main():
    diff_path = CORPUS_DIR / DIFF_FILE
    if not diff_path.exists():
        print(f"ERROR: diff not found: {diff_path}", file=sys.stderr)
        return 1

    # Read base files if they exist
    base_dir = CORPUS_DIR / "base_files" / ENTRY_NAME
    has_base = base_dir.exists() and any(base_dir.iterdir())

    temp_dir = tempfile.mkdtemp(prefix="forge-ablation-off-")
    repo_path = Path(temp_dir)
    print(f"Working in: {temp_dir}")
    print(f"Entry: {ENTRY_NAME}")
    print(f"Diff: {DIFF_FILE}")
    print(f"Backend: {BACKEND_NAME}")
    print(f"Engine: stub (ablation OFF)")
    print()

    try:
        # Init git repo
        subprocess.run(
            ["git", "init"], cwd=temp_dir, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=eval", "-c", "user.email=eval@test",
             "commit", "--allow-empty", "-m", "init"],
            cwd=temp_dir, capture_output=True, check=True,
        )

        # Copy base files
        if has_base:
            for f in base_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(base_dir)
                    dst = repo_path / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
            subprocess.run(
                ["git", "add", "."],
                cwd=temp_dir, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "-c", "user.name=eval", "-c", "user.email=eval@test",
                 "commit", "-m", "seed base files"],
                cwd=temp_dir, capture_output=True, check=False,
            )

        # Apply diff
        apply_result = subprocess.run(
            ["git", "apply", str(diff_path.resolve())],
            cwd=temp_dir, capture_output=True, check=False,
        )
        if apply_result.returncode != 0:
            stderr_text = apply_result.stderr.decode("utf-8", errors="replace")
            print(f"ERROR: git apply failed: {stderr_text}", file=sys.stderr)
            return 1

        # Create gate.yaml with the backend
        gate_dir = repo_path / ".code-forge"
        gate_dir.mkdir(parents=True, exist_ok=True)

        # Read backend config from main repo's gate.yaml
        main_gate = Path("/home/houminxi/code/forge/.code-forge/gate.yaml")
        gate_data = yaml.safe_load(main_gate.read_text())
        backend_config = gate_data["backends"][BACKEND_NAME]

        gate_yaml = {
            "backends": {BACKEND_NAME: backend_config},
        }
        (gate_dir / "gate.yaml").write_text(
            yaml.safe_dump(gate_yaml, default_flow_style=False),
        )

        # Create tools.yaml (noop)
        (gate_dir / "tools.yaml").write_text(
            yaml.safe_dump({
                "tools": {
                    "noop": {
                        "command": "true",
                        "file_patterns": ["*.nomatch"],
                        "output_format": "sarif",
                    }
                }
            }, default_flow_style=False),
        )

        # Trust the backend
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from code_forge.trust import record_trust
        trust_dir = repo_path / ".xdg-config" / "code-forge"
        trust_dir.mkdir(parents=True, exist_ok=True)
        gate_path = gate_dir / "gate.yaml"
        gate_yaml_data = yaml.safe_load(gate_path.read_text())
        record_trust(gate_path, gate_yaml_data, config_dir=trust_dir)

        # Set up environment
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        env["XDG_CONFIG_HOME"] = str(repo_path / ".xdg-config")
        env["FORGE_SKIP_WORKTREE_CHECK"] = "1"
        env["FORGE_FALSIFICATION_ENGINE"] = "stub"
        env["FORGE_MAX_TOTAL_ROUNDS"] = "3"

        print(f"FORGE_FALSIFICATION_ENGINE=stub")
        print(f"FORGE_MAX_TOTAL_ROUNDS=6")
        print()

        # Run the review
        cmd = ["code-forge", "review", "--backend", BACKEND_NAME]
        print(f"Running: {' '.join(cmd)}")
        print("=" * 60)

        t0 = time.monotonic()
        proc = subprocess.run(
            cmd, cwd=temp_dir, env=env,
            capture_output=True, timeout=3600,
        )
        wall_s = time.monotonic() - t0

        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

        print(f"\nExit code: {proc.returncode}")
        print(f"Wall time: {wall_s:.1f}s")
        print()
        if stderr:
            # Print last 2000 chars of stderr
            print("=== STDERR (tail) ===")
            print(stderr[-2000:])
        if stdout:
            print("=== STDOUT (tail) ===")
            print(stdout[-1000:])

        # Read state.json
        state_path = gate_dir / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            print("\n=== STATE.JSON ===")
            print(f"Verdict: {state.get('verdict')}")
            print(f"Converged: {state.get('converged')}")
            print(f"Rounds: {len(state.get('round_history', []))}")
            print(f"Consecutive clean rounds: {state.get('consecutive_clean_rounds')}")

            findings = state.get("findings", [])
            confirmed = [f for f in findings if f.get("disposition") == "CONFIRMED"]
            print(f"Total findings: {len(findings)}")
            print(f"CONFIRMED: {len(confirmed)}")

            infra_errors = state.get("infra_errors", [])
            print(f"Infra errors: {len(infra_errors)}")
            for ie in infra_errors:
                print(f"  - {ie[:120]}")

            # Severity distribution
            for f in confirmed:
                desc = f.get("description", "")
                severity = "P2"
                for p in ("P0:", "P1:", "P2:", "P3:"):
                    if desc.startswith(p):
                        severity = p[:-1]
                        break
                else:
                    if f.get("source") in ("L0", "L1"):
                        severity = "P1"
                print(f"  [{severity}] {f.get('fingerprint', '?')} :: {desc[:80]}")
        else:
            print("\nERROR: No state.json written -- review did not run.")

        return 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

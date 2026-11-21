"""Single fixed Forge R3 grouping diagnostic; not an admission check."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from verify import CONTROL, NODE, REV, SOURCE, TEST_HASH, verify

OLD = b"group_key = first_seg if len(parts) > 1 else fpath"
NEW = b'group_key = "one_group"'
BRANCH = "refs/heads/experiment/r3-grouping-pilot-20261121"

def digest(data):
    return hashlib.sha256(data).hexdigest()

def git(path, *args):
    return subprocess.check_output(["git", *args], cwd=path, stderr=subprocess.DEVNULL).strip()

def identity():
    sha, run, attempt, ref = (
        os.environ[key] for key in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REF")
    )
    if not re.fullmatch("[0-9a-f]{40}", sha) or ref != BRANCH:
        raise ValueError("workflow identity")
    if attempt != "1":
        raise ValueError("single attempt only")
    if any(not re.fullmatch("[1-9][0-9]{0,19}", value) for value in (run, attempt)):
        raise ValueError("run identity")
    return sha, run, attempt

def run(path, output):
    workflow_sha, run_id, attempt = identity()
    source = path / SOURCE
    test = path / NODE.split("::", 1)[0]
    status = ("status", "--porcelain", "--untracked-files=all")
    if output.exists() or output.parent == path or path in output.parents:
        raise ValueError("output location")
    if git(path, "rev-parse", "HEAD").decode() != REV or git(path, *status):
        raise ValueError("subject checkout")
    original = source.read_bytes()
    if original.count(OLD) != 1 or digest(original) != CONTROL["baseline"][0]:
        raise ValueError("source bytes")
    if digest(test.read_bytes()) != TEST_HASH:
        raise ValueError("test bytes")
    rows = []
    runner_env = {
        "PATH": os.defpath, "PYTHONPATH": str(path / "src"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    with tempfile.TemporaryDirectory(prefix="r3-private-") as work:
        runner_env["HOME"] = work
        try:
            for stage, (_, code, _, _) in CONTROL.items():
                if stage == "variant":
                    source.write_bytes(original.replace(OLD, NEW, 1))
                elif stage == "restored":
                    source.write_bytes(original)
                p = Path(work) / (stage + ".json")
                result = subprocess.run(
                    [sys.executable, "-B", str(Path(__file__).with_name("probe.py").resolve()), stage, str(p)],
                    cwd=path, env=runner_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=45,
                )
                if result.returncode != code or p.stat().st_size > 16384:
                    raise ValueError("runner result")
                record = json.loads(p.read_bytes())
                verify(record, stage)
                rows.append({
                    "stage": stage, "exit": result.returncode, "source_sha256": record["source_sha256"],
                    "outcomes": [row["outcome"] for row in record["reports"]],
                    "line": record["source_line"], "observation_phase": "call",
                    "observations": [event["group_key"] for event in record["trace_events"]],
                    "oracle": "expected_assertion" if stage == "variant" else "pass",
                })
        finally:
            if source.read_bytes() != original:
                source.write_bytes(original)
    if source.read_bytes() != original or digest(test.read_bytes()) != TEST_HASH or git(path, *status):
        raise ValueError("restore")
    summary = {
        "schema": 1, "case": "grouping", "workflow_sha": workflow_sha,
        "run_id": run_id, "run_attempt": attempt, "subject_sha": REV,
        "node": NODE, "test_sha256": TEST_HASH, "stages": rows,
    }
    data = (json.dumps(summary, ensure_ascii=True, sort_keys=True) + "\n").encode()
    if len(data) > 4096:
        raise ValueError("summary size")
    with output.open("xb") as stream:
        stream.write(data)

if __name__ == "__main__":
    try:
        run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError, IndexError):
        raise SystemExit("R3 advisory check failed; no evidence published") from None

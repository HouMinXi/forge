"""Run the fixed #33 multibatch protocol manually on Linux."""

import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

if not __debug__:
    raise SystemExit("Run without -O; validation checks must be enabled.")
root = Path.cwd()
if (root / "design.json").exists() or (root / "runner-preflight.json").exists():
    raise SystemExit("Use a new run directory with the pinned source under fixed/.")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(p):
    return digest(p.read_bytes())


def write(p, obj):
    with (root / p).open("x") as f:
        json.dump(obj, f, sort_keys=True, indent=2)


REVISION = "0e87b3c61c0cd1e1a5408b76c030044dc20b7259"
PRIOR_FREEZE_SHA256 = "4306f3f161bd9ec8342f35165b6d29a0511adea154b04a0b5610a20c619897ff"
loc = Path("src/code_forge/verify.py")
script = Path("scripts/perf_regression.py")
fixed = root / "fixed"


def blob(p):
    b = p.read_bytes()
    return hashlib.sha1(
        f"blob {len(b)}\0".encode() + b, usedforsecurity=False
    ).hexdigest()


assert blob(fixed / loc) == "067e7e91a1273712f338368d028a3228b2be319c"
assert blob(fixed / script) == "ebad4386014cd44c130546c453045d86e6627b7d"
assert (
    blob(fixed / "src/code_forge/diff.py") == "a64dea02687e3c2b0da82f73e3095abfe6b1c2f9"
)
fingerprint = {
    n: sha(fixed / n) for n in ["scripts/perf_regression.py", "src/code_forge/diff.py"]
}
code = fixed.joinpath(loc).read_bytes()
needle = b'    for line in diff_text.splitlines():\n        if line.startswith("diff --git "):\n'
assert code.count(needle) == 1

boot = r"""
import hashlib,importlib.util,json,runpy,sys
from pathlib import Path
import code_forge.verify as verify
import code_forge.diff as diff
path=Path(verify.parse_diff_files.__code__.co_filename).resolve()
assert path==Path("src/code_forge/verify.py").resolve(),path
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(path)==sys.argv[2]
assert Path(diff.__file__).resolve()==Path("src/code_forge/diff.py").resolve()
assert sha(diff.__file__)==sys.argv[3]
assert sha("scripts/perf_regression.py")==sys.argv[4]
mode=sys.argv[1]
if mode=="cli":
    sys.argv=["scripts/perf_regression.py",*sys.argv[5:]]
    runpy.run_path(sys.argv[0],run_name="__main__")
else:
    spec=importlib.util.spec_from_file_location("bench","scripts/perf_regression.py")
    p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
    assert p.SLOWDOWN==0.5 and p.RUNS==7 and p.parse_diff_files is verify.parse_diff_files
    if mode=="trace":
        lines=path.read_text().splitlines()
        orig=extra=supplement=0
        def trace(f,event,arg):
            global orig,extra,supplement
            if f.f_code is not verify.parse_diff_files.__code__:return None
            if event=="line":
                s=lines[f.f_lineno-1].strip()
                orig+=s=='if line.startswith("diff --git "):'
                extra+=s=='line.startswith("diff --git ")'
                supplement+=s in {'line.startswith("d")','len(line) > 5'}
            return trace
        sys.settrace(trace)
        try: assert p.parse_diff_files(p._DIFF)==p._EXPECTED
        finally: sys.settrace(None)
        print(json.dumps({"lines":len(p._DIFF.splitlines()),"original":orig,"extra":extra,"supplement":supplement,
          "workload_sha256":hashlib.sha256(p._DIFF.encode()).hexdigest(),"imported_sha256":sha(path)}))
"""


def env():
    (n,) = os.sched_getaffinity(0)
    entries = Path("/proc/cpuinfo").read_text().split("\n\n")
    model = next(
        (
            s
            for s in entries
            if any(
                l.split(":", 1)[0].strip() == "processor"
                and l.split(":", 1)[1].strip() == str(n)
                for l in s.splitlines()
                if ":" in l
            )
        ),
        "",
    )
    stable = {
        l.split(":", 1)[0].strip(): l.split(":", 1)[1].strip()
        for l in model.splitlines()
        if ":" in l
        and l.split(":", 1)[0].strip()
        in ("model name", "model", "stepping", "vendor_id", "microcode")
    }
    return {
        "affinity": n,
        "cpu": stable,
        "python": sys.version,
        "python_sha256": sha(Path(sys.executable)),
        "kernel": platform.release(),
        "clock": time.get_clock_info("perf_counter").implementation,
        "hashseed": "0",
    }


def nonidle():
    return {
        int(f[0][3:]): sum(int(f[i]) for i in (1, 2, 3, 7, 8, 9))
        for row in Path("/proc/stat").read_text().splitlines()
        if (f := row.split()) and f[0][3:].isdigit() and f[0].startswith("cpu")
    }


allowed = os.sched_getaffinity(0)
a = nonidle()
time.sleep(5)
b = nonidle()
deltas = {n: b[n] - a[n] for n in sorted(allowed)}
chosen = min(deltas, key=lambda n: (deltas[n], n))
write(
    "runner-preflight.json",
    {
        "sample_seconds": 5,
        "nonidle_ticks": deltas,
        "chosen_cpu": chosen,
        "qualification": "Five-second utilization sample; does not establish isolation.",
    },
)
os.sched_setaffinity(0, {chosen})
environment = env()
runner = "forge33-" + digest(json.dumps(environment, sort_keys=True).encode())[:20]
variants = {0: (fixed, sha(fixed / loc))}
supplement = {8: 'line.startswith("d")', 10: "len(line) > 5"}

prior = {
    "imported_parser_sha256": {
        "above": "573eb7fea1171000f2f6756a05431d6e648916fb158977fbe748c806db56dc2c",
        "below": "1f87fb36746096546f9c893237fcfb0e0dd0811fc960166396177af4f404c4b1",
        "clean": "4588a72177c414a558b1eccdb3dca86d3637861aa532974ea36ce99a5bc82c08",
    },
    "other_source_sha256": {
        "scripts/perf_regression.py": "adeafe57642f94396a24f3e869462e1608a16845e84547e081036aeaaa12e22b",
        "src/code_forge/diff.py": "089fba1f06fb3910bffb79423c2064f7cdcf934b3203bb9f67a27f36c51262db",
    },
    "selected": {"above": 8, "below": 10, "clean": 0},
    "traces": {
        "above": {
            "extra": 8120,
            "imported_sha256": "573eb7fea1171000f2f6756a05431d6e648916fb158977fbe748c806db56dc2c",
            "lines": 8120,
            "original": 8120,
            "supplement": 8120,
            "workload_sha256": "2a3817522daa398660b8f2a6b2bb1da3177e49a5a36cb1890ee0669c6ebb97ef",
        },
        "below": {
            "extra": 8120,
            "imported_sha256": "1f87fb36746096546f9c893237fcfb0e0dd0811fc960166396177af4f404c4b1",
            "lines": 8120,
            "original": 8120,
            "supplement": 8120,
            "workload_sha256": "2a3817522daa398660b8f2a6b2bb1da3177e49a5a36cb1890ee0669c6ebb97ef",
        },
        "clean": {
            "extra": 0,
            "imported_sha256": "4588a72177c414a558b1eccdb3dca86d3637861aa532974ea36ce99a5bc82c08",
            "lines": 8120,
            "original": 8120,
            "supplement": 0,
            "workload_sha256": "2a3817522daa398660b8f2a6b2bb1da3177e49a5a36cb1890ee0669c6ebb97ef",
        },
    },
}
selected = prior["selected"]
assert selected == {"clean": 0, "below": 10, "above": 8}
for n in (selected["below"], selected["above"]):
    target = root / f"repeat{n}"
    shutil.copytree(fixed, target)
    extra = b'        line.startswith("diff --git ")\n'
    if n in supplement:
        extra += ("        " + supplement[n] + "\n").encode()
    start, original = needle.splitlines(keepends=True)
    v = code.replace(needle, start + extra + original, 1)
    (target / loc).write_bytes(v)
    variants[n] = (target, sha(target / loc))


def run(n, mode, *args):
    target, expected = variants[n]
    childenv = {
        **os.environ,
        "PYTHONPATH": str(target / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            boot,
            mode,
            expected,
            fingerprint["src/code_forge/diff.py"],
            fingerprint["scripts/perf_regression.py"],
            *args,
        ],
        cwd=target,
        env=childenv,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert not result.stderr, result.stderr
    assert result.returncode in ((0, 1, 2) if mode == "cli" else (0,)), (
        result.returncode
    )
    return {"exit": result.returncode, "record": json.loads(result.stdout)}


def cli(n, *args, runner=runner):
    return run(n, "cli", "--runner", runner, *args)


traces = {label: run(n, "trace")["record"] for label, n in selected.items()}
assert traces == prior["traces"]
assert fingerprint == prior["other_source_sha256"]
assert all(
    variants[v][1] == prior["imported_parser_sha256"][k] for k, v in selected.items()
)
# Use non-overlapping windows. Each block measures a fresh clean reference
# and a distinct clean control and both fixed perturbations, in balanced order.
groups, size, threshold = 6, 9, 1.5
arms = ("reference", "clean", "below", "above")
patterns = [
    ("reference", "clean", "above", "below"),
    ("clean", "below", "reference", "above"),
    ("below", "above", "clean", "reference"),
    ("above", "reference", "below", "clean"),
]
schedule = [[patterns[(w * size + b) % 4] for b in range(size)] for w in range(groups)]
assert all(
    abs(
        sum(seq[pos] == role for window in schedule for seq in window)
        - groups * size / 4
    )
    <= 0.5
    for role in arms
    for pos in range(4)
)


def verdict(ratios):
    if len(ratios) != size or any(not math.isfinite(x) or x <= 0 for x in ratios):
        raise ValueError("requires nine finite positive ratios")
    sr = sorted(ratios)
    low, high = sr[1], sr[-2]
    tag = (
        "within" if high <= threshold else "slower" if low > threshold else "uncertain"
    )
    return {"verdict": tag, "low": low, "high": high, "median": sr[4]}


assert verdict([1.5] * 9)["verdict"] == "within"
assert verdict([1.50001] * 9)["verdict"] == "slower"
assert verdict([1.4] * 7 + [1.6] * 2)["verdict"] == "uncertain"
assert verdict([1.4] * 2 + [1.6] * 7)["verdict"] == "uncertain"
assert verdict([1.4] * 8 + [1.6])["verdict"] == "within"
assert verdict([1.4] + [1.6] * 8)["verdict"] == "slower"
for bad in ([1.0] * 8, [1.0] * 8 + [float("nan")], [1.0] * 8 + [0]):
    try:
        verdict(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("bad input admitted")

expected = {"clean": "within", "below": "within", "above": "slower"}
design = {
    "revision": REVISION,
    "prior_freeze_sha256": PRIOR_FREEZE_SHA256,
    "harness_sha256": sha(Path(__file__)),
    "environment": environment,
    "runner": runner,
    "selected": selected,
    "traces": traces,
    "other_source_sha256": fingerprint,
    "threshold": threshold,
    "schedule": schedule,
    "baseline": "fresh independent clean reference process in each block; each process uses the unchanged seven-call median",
    "decision": "second to eighth sorted ratios in each nonoverlapping nine-block window; upper <=1.5 within, lower >1.5 slower, else uncertain",
    "criterion": "all six windows must classify each arm as expected; report every wrong and uncertain outcome; no reruns or exclusions",
    "limits": "single shared runner; separate processes are not proof of statistical independence; no confidence level claimed; paired reference and aggregation tested jointly",
}
write("design.json", design)
frozen = sha(root / "design.json")
print("FROZEN", frozen, flush=True)
windows = []
envsig = None
for i, orders in enumerate(schedule):
    assert sha(root / "design.json") == frozen and env() == environment
    rows = []
    for order in orders:
        rec = {}
        for role in order:
            row = cli(selected.get(role, 0), "--record")
            assert row["exit"] == 0
            row = row["record"]
            assert (
                row["definition"] == "forge-perf-regression-v2"
                and row["workload"] == "parse_diff_files"
            )
            if envsig is None:
                envsig = row["environment"]
            assert row["environment"] == envsig
            assert math.isfinite(row["median_s"]) and row["median_s"] > 0
            rec[role] = row
        rows.append(rec)
    ratios = {
        role: [r[role]["median_s"] / r["reference"]["median_s"] for r in rows]
        for role in expected
    }
    window = {
        "records": rows,
        "ratios": ratios,
        "decisions": {role: verdict(v) for role, v in ratios.items()},
    }
    write(f"window-{i + 1}.json", window)
    windows.append(window)
    print("WINDOW", i + 1, window["decisions"], flush=True)
assert env() == environment and sha(root / "design.json") == frozen
counts = {
    role: {
        t: sum(w["decisions"][role]["verdict"] == t for w in windows)
        for t in ("within", "uncertain", "slower")
    }
    for role in expected
}
single = {
    role: sum(v > threshold for w in windows for v in w["ratios"][role])
    for role in expected
}
success = all(counts[k][v] == groups for k, v in expected.items())
write(
    "summary.json",
    {
        "design_sha256": frozen,
        "counts": counts,
        "single_batch_alerts": single,
        "single_batch_total": groups * size,
        "qualified_this_experiment": success,
    },
)
print("RESULT", success, counts, single, flush=True)
# Qualification status lives in summary.json; this launcher is not a gate.

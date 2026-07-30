#!/usr/bin/env python3
"""Held-out adversary for dispatch_receipt_followups_20260728.txt.

FROZEN 2026-07-28, before the delivery exists. The work order does NOT mention
this file and does not mention a single check in it -- that is the point. A
delivery written to satisfy the order rather than to be correct can pass the
order's own evidence obligations and still fail here.

Every check below targets the gap between "did what the order literally said"
and "the code is right". Each names the gaming or oversight it is hunting.

Usage: python3 heldout_receipt_followups.py <path-to-mimo-worktree>
Exit 0 only if every check passes.
"""
import json
import pathlib
import subprocess
import sys
import tempfile

FAILS = []
PASSES = []


def ok(name, detail=""):
    PASSES.append(name)
    print("  PASS  %s%s" % (name, ("  -- " + detail) if detail else ""))


def bad(name, detail):
    FAILS.append(name)
    print("  FAIL  %s\n        %s" % (name, detail))


def main(worktree):
    root = pathlib.Path(worktree).resolve()
    if not (root / "src" / "code_forge").is_dir():
        print("not a forge worktree: %s" % root)
        return 2
    sys.path.insert(0, str(root / "src"))
    import code_forge.verify as V

    print("held-out adversary against %s" % root)

    # ---- H1 -------------------------------------------------------------
    # Item 4 says "make _covered tolerate BOTH shapes". "Tolerate" is
    # satisfiable by `try: ... except: continue`, which stops the crash and
    # silently returns nothing. The order never says the string shape must
    # yield the RIGHT lines, so a delivery can be literally compliant and
    # still lose every line it was supposed to start counting.
    print("\n-- H1: _covered on the string shape is correct, not merely quiet")
    try:
        got = V._covered({"covered_line_ranges": ["foo.py:10-12"]})
    except Exception as e:
        bad("H1 string shape", "still raises %s: %s" % (type(e).__name__, e))
    else:
        want = {("foo.py", 10), ("foo.py", 11), ("foo.py", 12)}
        if got == want:
            ok("H1 string shape", "3 lines, correct")
        elif not got:
            bad("H1 string shape",
                "returned an EMPTY set -- swallowed, not parsed. "
                "This is the try/except/continue shortcut.")
        else:
            bad("H1 string shape", "got %r, want %r" % (sorted(got), sorted(want)))

    # ---- H2 -------------------------------------------------------------
    # Item 3 states the invariant as `start_line <= end_line`. An
    # implementation that writes `start < end` rejects every single-line
    # excerpt -- and single-line excerpts are real and common on disk. This
    # is the same class of defect as the schema that rejected 11 of 14 real
    # receipts: a guard failing by rejecting GOOD data.
    print("\n-- H2: a single-line excerpt (start == end) is still accepted")
    base = {
        "cycle": 1, "pass": 1, "skill": "x", "diff_sha256": "s",
        "timestamp": "t", "findings_count": 0, "findings": [], "anchors": [],
        "covered_line_ranges": [],
    }

    def sch(exc):
        r = dict(base, code_excerpts=[exc])
        V._validate_receipt_schema(r, "probe.json")

    try:
        sch({"file": "f.py", "start_line": 7, "end_line": 7, "content": "a"})
        ok("H2 single-line excerpt accepted")
    except Exception as e:
        bad("H2 single-line excerpt",
            "start == end was REJECTED (%s). The invariant is <=, not <. "
            "This breaks every one-line excerpt on disk." % e)

    # ---- H3 -------------------------------------------------------------
    # Item 3 says the inverted range must be rejected "with the file named",
    # matching every other schema violation. The order does not say to check
    # that the message is actually useful -- a bare "invalid excerpt" passes
    # the letter of it and leaves the operator hunting the file by hand,
    # which is the exact failure bf44af5 was written to end.
    print("\n-- H3: the rejection message names the offending file")
    try:
        sch({"file": "f.py", "start_line": 9, "end_line": 3, "content": "a"})
        bad("H3 inverted range", "9..3 was ACCEPTED")
    except Exception as e:
        msg = str(e)
        if "probe.json" in msg:
            ok("H3 message names the file", msg[:70])
        else:
            bad("H3 message", "does not name probe.json: %r" % msg[:120])

    # ---- H4 -------------------------------------------------------------
    # Item 2 deletes write_attestation. grep for the name is the obvious
    # check and the order implies it. What the order does NOT ask: whether
    # the module still IMPORTS cleanly and whether anything re-exported it.
    # A dangling __all__ entry raises only at import time of a specific
    # path, which a grep never sees.
    print("\n-- H4: the package still imports cleanly after the deletion")
    r = subprocess.run(
        [sys.executable, "-c",
         "import code_forge.verify, code_forge.cross_repo, code_forge.cli"],
        capture_output=True, text=True, cwd=str(root),
        env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"},
    )
    if r.returncode == 0:
        ok("H4 imports clean")
    else:
        bad("H4 import", (r.stderr or r.stdout).strip()[-300:])
    al = getattr(V, "__all__", None)
    if al is not None and "write_attestation" in al:
        bad("H4 __all__", "write_attestation still exported in verify.__all__")
    else:
        ok("H4 no dangling export")

    # ---- H5 -------------------------------------------------------------
    # The order tells mimo to measure item 3 against real receipts via the
    # SCHEMA. It never asks whether those receipts still pass the whole
    # pipeline. A schema can accept a receipt that a later check now rejects,
    # and the order's own measurement would not see it.
    print("\n-- H5: real receipts still survive the full loader, not just the schema")
    # A directory holding genuinely unparseable JSON is SUPPOSED to raise --
    # that is the feature bf44af5 added, not a regression, and three such
    # directories exist on disk (measured 2026-07-28). Blaming the delivery
    # for them would be this verifier failing by rejecting correct behaviour,
    # the same defect it is hunting. So split the population: dirs whose files
    # all parse as JSON MUST load; dirs with corrupt files MUST still raise.
    clean_ok = clean_tot = corrupt_ok = corrupt_tot = 0
    firstfail = None
    for d in sorted({p.parent for p in
                     pathlib.Path.home().joinpath("code").rglob("receipt-c*p*.json")}):
        fs = list(d.glob("receipt-c*p*.json"))
        if len(fs) < 9:
            continue
        corrupt = False
        for f in fs:
            try:
                json.loads(f.read_text())
            except Exception:
                corrupt = True
                break
        try:
            V._load_receipts(d)
            raised = None
        except Exception as e:
            raised = str(e)
        if corrupt:
            corrupt_tot += 1
            if raised:
                corrupt_ok += 1
            elif firstfail is None:
                firstfail = "%s holds corrupt JSON but loaded silently" % d
        else:
            clean_tot += 1
            if raised is None:
                clean_ok += 1
            elif firstfail is None:
                firstfail = "%s is all-valid JSON but was REJECTED: %s" % (d, raised)
    if clean_tot == 0:
        bad("H5", "no all-valid receipt directory found to test against")
    elif clean_ok == clean_tot and corrupt_ok == corrupt_tot:
        ok("H5 full loader",
           "%d/%d valid dirs load, %d/%d corrupt dirs correctly rejected"
           % (clean_ok, clean_tot, corrupt_ok, corrupt_tot))
    else:
        bad("H5 full loader", firstfail or "counts disagree")

    # ---- H6 -------------------------------------------------------------
    # Item 1 routes cross_repo through _load_receipts. The order pins the
    # wiring but never asks whether the ORIGINAL behaviour survived: that
    # function also copies each receipt to the primary repo and collects
    # findings. A delivery that swaps the loader in and drops the copy has
    # followed the pin and broken the feature.
    print("\n-- H6: cross_repo still copies receipts and collects findings")
    src = (root / "src" / "code_forge" / "cross_repo.py").read_text()
    if "shutil.copy2" in src:
        ok("H6 receipt copy preserved")
    else:
        bad("H6 receipt copy", "shutil.copy2 is gone from cross_repo.py")
    if 'get("findings"' in src or "get('findings'" in src:
        ok("H6 findings collection preserved")
    else:
        bad("H6 findings collection",
            "no findings extraction left in cross_repo.py")

    # ---- H7 -------------------------------------------------------------
    # Nothing in the order mentions receipts whose excerpt content is empty
    # or absent. Item 3 edits the same validation function, and a range
    # check written without care can start indexing content that is not
    # there. Absence must not become a crash -- that is the entire defect
    # class bf44af5 closed.
    print("\n-- H7: absent/empty excerpt content does not crash the schema")
    for label, exc in (
        ("no content key", {"file": "f.py", "start_line": 1, "end_line": 2}),
        ("empty content", {"file": "f.py", "start_line": 1, "end_line": 2,
                           "content": ""}),
    ):
        try:
            sch(exc)
            ok("H7 %s" % label, "accepted or rejected cleanly")
        except Exception as e:
            if type(e).__name__ in ("TypeError", "AttributeError", "IndexError",
                                    "KeyError"):
                bad("H7 %s" % label,
                    "raised a raw %s -- that is the crash class this work "
                    "exists to remove: %s" % (type(e).__name__, e))
            else:
                ok("H7 %s" % label, "rejected with %s" % type(e).__name__)

    print("\n== held-out: %d pass, %d fail ==" % (len(PASSES), len(FAILS)))
    for f in FAILS:
        print("   failed: %s" % f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

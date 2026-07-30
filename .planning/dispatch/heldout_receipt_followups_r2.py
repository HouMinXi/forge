#!/usr/bin/env python3
"""Held-out adversary for the receipt-followups R2 rework.

Frozen 2026-07-29, before the delivery exists. The work order
(dispatch_receipt_followups_r2_20260729.txt) does not mention this file or
any check in it.

The order pins item A as BEHAVIOUR and warns against string-shaped
assertions. So does this file: nothing here greps the generated hook for a
substring. Every hook check writes the generated script to disk, puts a stub
`code-forge` on PATH, runs it, and looks at what the operator would actually
see.

Usage: heldout_receipt_followups_r2.py <worktree>
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER_OUT = "HELDOUT-REASON-ON-STDOUT-a41f"
MARKER_ERR = "HELDOUT-REASON-ON-STDERR-a41f"

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((ok, label, detail))
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", label,
                          "  -- " + detail if detail else ""))


def head(title: str) -> None:
    print("\n-- %s" % title)


def run(cmd, cwd=None, env=None, timeout=120):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def generate_hook(wt: Path, forge_invocation: str) -> str:
    """Call the delivery's own generate_hook_content, not a copy of it."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from code_forge.install_hooks import generate_hook_content\n"
        "sys.stdout.write(generate_hook_content(%r, None))\n"
        % (str(wt / "src"), forge_invocation)
    )
    r = run([sys.executable, "-c", code], cwd=str(wt))
    if r.returncode != 0:
        raise RuntimeError("generate_hook_content failed: %s" % r.stderr[-800:])
    return r.stdout


def make_stub(bindir: Path, *, fail: bool) -> None:
    """A stand-in `code-forge`.

    In fail mode it imitates what cli.py actually does when verify fails:
    the reason goes to stdout (cli.py:1514) and other errors go to stderr
    (cli.py:1505). Both markers must survive to the operator, or the hook is
    pointing at output it destroyed.

    It must also honour --quiet exactly as cli.py:1514 does. A stub that
    prints the reason regardless of --quiet reports success while the real
    hook stays silent -- the first draft of this file did that and would
    have passed the unfixed tree.
    """
    stub = bindir / "code-forge"
    if fail:
        body = (
            "#!/bin/sh\n"
            'if [ "$1" = "verify" ]; then\n'
            "  quiet=0\n"
            '  for a in "$@"; do [ "$a" = "--quiet" ] && quiet=1; done\n'
            '  [ "$quiet" = "0" ] && echo "verify: FAIL -- corrupt receipt: %s"\n'
            '  echo "%s" >&2\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n" % (MARKER_OUT, MARKER_ERR)
        )
    else:
        body = (
            "#!/bin/sh\n"
            'if [ "$1" = "verify" ]; then exit 0; fi\n'
            "exit 0\n"
        )
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)


def run_hook(wt: Path, *, fail: bool):
    """Run the generated hook in a throwaway git repo with a staged file."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        bindir = d / "bin"
        bindir.mkdir()
        make_stub(bindir, fail=fail)

        repo = d / "repo"
        repo.mkdir()
        env = dict(os.environ)
        env["PATH"] = "%s:%s" % (bindir, env.get("PATH", ""))
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@t"
        run(["git", "init", "-q"], cwd=str(repo), env=env)
        (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
        run(["git", "add", "mod.py"], cwd=str(repo), env=env)

        hook = d / "pre-commit"
        hook.write_text(generate_hook(wt, "/bin/true"), encoding="utf-8")
        hook.chmod(0o755)

        r = run(["sh", str(hook)], cwd=str(repo), env=env)
        return r


def main() -> int:
    wt = Path(sys.argv[1]).resolve()
    print("held-out adversary R2 against %s" % wt)

    # ---- HR1: the failure reason actually reaches the operator ------------
    # The order pins the behaviour; it does not say the gate executes the
    # hook. A wording-only fix, or one that keeps 2>/dev/null, dies here.
    head("HR1: on failure the operator sees verify's reason, both channels")
    try:
        r = run_hook(wt, fail=True)
        seen = r.stdout + r.stderr
        check(MARKER_OUT in seen, "HR1a stdout reason survives",
              "" if MARKER_OUT in seen else "reason line was discarded")
        check(MARKER_ERR in seen, "HR1b stderr reason survives",
              "" if MARKER_ERR in seen else "stderr was sent to /dev/null")
    except Exception as e:  # noqa: BLE001
        check(False, "HR1 harness", str(e)[:200])
        r = None

    # ---- HR2: the hook still fails the commit ----------------------------
    # Capture-and-replay rewrites lose the exit code in a subshell. A hook
    # that prints the reason and then exits 0 is worse than the bug.
    head("HR2: a failing verify still blocks the commit")
    if r is not None:
        check(r.returncode != 0, "HR2 non-zero exit",
              "exit=%d" % r.returncode)
    else:
        check(False, "HR2 non-zero exit", "harness failed")

    # ---- HR3: the PASS path stays silent and succeeds --------------------
    # Contract 1 of item A. Held out: exit status on the pass path, which
    # the order never states.
    head("HR3: on success the hook is silent and does not block")
    try:
        rp = run_hook(wt, fail=False)
        noisy = [ln for ln in (rp.stdout + rp.stderr).splitlines()
                 if "verify" in ln.lower()]
        check(not noisy, "HR3a silent on pass",
              "" if not noisy else "leaked: %s" % noisy[:2])
        check(rp.returncode == 0, "HR3b exit 0 on pass",
              "exit=%d" % rp.returncode)
    except Exception as e:  # noqa: BLE001
        check(False, "HR3 harness", str(e)[:200])

    # ---- HR4: the generated script is valid sh ---------------------------
    # Quoting a captured variable inside a Python string literal is where
    # this rewrite breaks. `sh -n` catches it; a substring assertion cannot.
    head("HR4: the generated hook parses as sh")
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.sh"
            p.write_text(generate_hook(wt, "/bin/true"), encoding="utf-8")
            rn = run(["sh", "-n", str(p)])
            check(rn.returncode == 0, "HR4 sh -n clean",
                  rn.stderr.strip()[:200])
    except Exception as e:  # noqa: BLE001
        check(False, "HR4 harness", str(e)[:200])

    # ---- HR5: no drive-by edits to the neighbouring blocks ---------------
    # Everything in the generated hook except the attestation block should
    # be byte-identical to main's output.
    head("HR5: only the attestation block changed")
    try:
        main_wt = Path("/home/houminxi/code/forge")
        cur = generate_hook(wt, "/bin/true").splitlines()
        base = generate_hook(main_wt, "/bin/true").splitlines()

        def strip_attestation(lines):
            out, skip = [], False
            for ln in lines:
                if "attestation check" in ln:
                    skip = True
                    continue
                if skip:
                    if ln.strip() == "}":
                        skip = False
                    continue
                out.append(ln)
            return out

        a, b = strip_attestation(cur), strip_attestation(base)
        check(a == b, "HR5 neighbouring blocks untouched",
              "" if a == b else "%d line(s) differ outside attestation"
              % sum(1 for x, y in zip(a, b) if x != y))
    except Exception as e:  # noqa: BLE001
        check(False, "HR5 harness", str(e)[:200])

    # ---- HR6: every commit stands on its own -----------------------------
    # The order says split the commit. It never says each resulting commit
    # must import cleanly -- a split that parks the schema change one commit
    # away from the code that needs it bisects into a broken tree.
    head("HR6: each commit in the rewritten history imports cleanly")
    try:
        shas = run(["git", "log", "--format=%H", "891772a..HEAD"],
                   cwd=str(wt)).stdout.split()
        bad = []
        for sha in shas:
            src = run(["git", "show", "%s:src/code_forge/verify.py" % sha],
                      cwd=str(wt))
            if src.returncode != 0:
                continue
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / "verify_probe.py"
                f.write_text(src.stdout, encoding="utf-8")
                c = run([sys.executable, "-m", "py_compile", str(f)])
                if c.returncode != 0:
                    bad.append(sha[:7])
        check(not bad, "HR6 all %d commits compile" % len(shas),
              "" if not bad else "broken: %s" % ", ".join(bad))
    except Exception as e:  # noqa: BLE001
        check(False, "HR6 harness", str(e)[:200])

    # ---- HR7: the rework did not quietly drop test coverage --------------
    head("HR7: no test function or assertion was removed")
    try:
        d = run(["git", "diff", "891772a..HEAD", "--", "tests/"],
                cwd=str(wt)).stdout.splitlines()
        removed_tests = [ln for ln in d
                         if ln.startswith("-") and "def test_" in ln]
        removed_asserts = sum(1 for ln in d
                              if ln.startswith("-")
                              and ln.lstrip("-").strip().startswith("assert"))
        check(not removed_tests, "HR7a no test function removed",
              "" if not removed_tests else "%d removed" % len(removed_tests))
        check(removed_asserts <= 2, "HR7b assertions removed: %d (<=2 ok)"
              % removed_asserts)
    except Exception as e:  # noqa: BLE001
        check(False, "HR7 harness", str(e)[:200])

    npass = sum(1 for ok, _, _ in _results if ok)
    nfail = len(_results) - npass
    print("\n== held-out R2: %d pass, %d fail ==" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())

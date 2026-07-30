#!/usr/bin/env python3
"""Held-out adversary for Phase 42 rework W4.

The rework order never mentioned this check, and the delivered tests do
not perform it: TestCredentialErrorTable exercises credential_error
alone and asserts by docstring that "_check_backend_credentials and
_probe_api inherit the contract".  Inheritance by structure is what F2
already disproved once -- two validators that looked unified and were
not.

So this drives BOTH wrappers over the same input matrix and compares
their verdicts row by row.  A row where one accepts and the other
rejects is F2 reappearing, whatever the shared rule's own tests say.

Run:  PYTHONPATH=<worktree>/src python3 42-w4-heldout-union-adversary.py
Exit: 0 = both wrappers agree on every row; 1 = divergence or error.
"""
import os
import sys
import tempfile
from pathlib import Path

from code_forge.backend import BackendConfig, probe_backend
from code_forge.cli import _check_backend_credentials, CliError


def mk(tmp, name, **kw):
    return BackendConfig(name=name, type="api", model="m", **kw)


def wrapper_fastfail(backend, env):
    """True = rejected.

    Version-compatible on purpose: the pre-rework signature is
    _check_backend_credentials(backend) reading os.environ, the reworked
    one takes env=.  The known-answer run needs the SAME instrument to
    reach both, or it proves nothing about either.
    """
    try:
        try:
            _check_backend_credentials(backend, env=env)
        except TypeError as exc:
            if "env" not in str(exc):
                raise
            saved = dict(os.environ)
            os.environ.clear()
            os.environ.update(env)
            try:
                _check_backend_credentials(backend)
            finally:
                os.environ.clear()
                os.environ.update(saved)
        return False, ""
    except CliError as exc:
        return True, str(exc)


def wrapper_probe(backend, env, cache_dir):
    """True = rejected."""
    res = probe_backend(backend, env=env, cache_dir=cache_dir)
    return (not res.ok), (res.error or "")


def build_rows(tmp):
    rows = []

    missing = tmp / "nope.key"
    rows.append(("api_key_file missing", dict(format="openai",
                 api_key_file=str(missing)), {}, True, "not found"))

    empty = tmp / "empty.key"
    empty.write_text("")
    empty.chmod(0o600)
    rows.append(("api_key_file empty", dict(format="openai",
                 api_key_file=str(empty)), {}, True, "empty"))

    loose = tmp / "loose.key"
    loose.write_text("sk-x")
    loose.chmod(0o644)
    rows.append(("api_key_file 0644", dict(format="openai",
                 api_key_file=str(loose)), {}, True, "chmod 600"))

    good = tmp / "good.key"
    good.write_text("sk-x")
    good.chmod(0o600)
    rows.append(("api_key_file 0600 non-empty", dict(format="openai",
                 api_key_file=str(good)), {}, False, ""))

    if os.geteuid() != 0:
        noread = tmp / "noread.key"
        noread.write_text("sk-x")
        noread.chmod(0o000)
        rows.append(("api_key_file unreadable", dict(format="openai",
                     api_key_file=str(noread)), {}, True, "unreadable"))
    else:
        print("SKIP row 'api_key_file unreadable': running as root")

    rows.append(("api_key_env present", dict(format="openai",
                 api_key_env="ADV_KEY"), {"ADV_KEY": "sk-x"}, False, ""))
    rows.append(("api_key_env absent", dict(format="openai",
                 api_key_env="ADV_KEY"), {}, True, "not set"))
    rows.append(("neither configured", dict(format="openai"), {},
                 True, "no api_key_env or api_key_file"))

    vgood = tmp / "sa.json"
    vgood.write_text("{}")
    vgood.chmod(0o600)
    rows.append(("vertex credentials_path ok", dict(format="vertex",
                 credentials_path=str(vgood)), {}, False, ""))
    rows.append(("vertex credentials_path missing", dict(format="vertex",
                 credentials_path=str(tmp / "gone.json")), {},
                 True, "not found"))
    return rows


def main():
    tmpdir = tempfile.mkdtemp(prefix="w4adv-")
    tmp = Path(tmpdir)
    rows = build_rows(tmp)

    print("%-34s %-10s %-10s %-8s %s"
          % ("row", "fast-fail", "probe", "agree", "expected"))
    print("-" * 84)

    diverged = 0
    wrong = 0
    for i, (label, kw, env, expect_reject, substr) in enumerate(rows):
        cache = tmp / ("cache%d" % i)
        cache.mkdir()
        b = mk(tmp, "adv%d" % i, **kw)
        a_rej, a_msg = wrapper_fastfail(b, env)
        b_rej, b_msg = wrapper_probe(b, env, cache)
        agree = (a_rej == b_rej)
        if not agree:
            diverged += 1
        ok_expect = (a_rej == expect_reject)
        if not ok_expect:
            wrong += 1
        if expect_reject and a_rej and substr and substr not in a_msg:
            wrong += 1
            ok_expect = False
        print("%-34s %-10s %-10s %-8s %s"
              % (label,
                 "REJECT" if a_rej else "accept",
                 "REJECT" if b_rej else "accept",
                 "yes" if agree else "NO",
                 "ok" if ok_expect else "MISMATCH(want %s %r)"
                 % ("reject" if expect_reject else "accept", substr)))
        if not agree:
            print("    fast-fail: %s" % a_msg)
            print("    probe    : %s" % b_msg)

    print("-" * 84)
    print("rows=%d diverged=%d table_mismatch=%d" % (len(rows), diverged, wrong))
    if diverged or wrong:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS -- both wrappers agree on every row")
    return 0


if __name__ == "__main__":
    sys.exit(main())

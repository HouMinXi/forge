#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the falsify calibration set and report agreement with the frozen labels.

    PYTHONPATH=src /usr/bin/python3 scripts/run_falsify_calibration.py \\
        tests/calibration/falsify --engine real [--min-agree 0.9]

Exit 0 when agree/total >= --min-agree, 1 when below, 2 on infrastructure
(bad expected.json, backend cannot answer). Prints one line per item,
`MISS <id> expected=<x> got=<y>  why=<why>` on disagreement, then
`agree N/M`.

The `stub` engine takes --stub-default so the runner's own tests can drive
both exits without a backend. Only `real` measures anything.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from code_forge.disposition import Disposition  # noqa: E402
from code_forge.llm_invoke import LLMInvokeError  # noqa: E402
from code_forge.state import StateFinding  # noqa: E402


def _load_items(calib_dir: Path) -> list[dict]:
    p = calib_dir / "expected.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("cannot read %s: %s" % (p, exc)) from exc
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise SystemExit("expected.json has no items list")
    for it in items:
        if it.get("expected") not in ("CONFIRMED", "DISMISSED"):
            raise SystemExit("item %s: expected must be CONFIRMED|DISMISSED" % it.get("id"))
        for key in ("id", "entry", "why", "finding"):
            if key not in it:
                raise SystemExit("item %s: missing %r" % (it.get("id", "?"), key))
        for key in ("file", "line_range", "description"):
            if key not in it["finding"]:
                raise SystemExit("item %s: finding missing %r" % (it["id"], key))
        # entry becomes a chdir target under --raw and a key under
        # --corpus; a segment that walks up is refused before any item
        # is billed to the backend
        if "/" in it["entry"] or it["entry"] in ("", ".", ".."):
            raise SystemExit("item %s: entry escapes --raw: %r"
                             % (it["id"], it["entry"]))
    return items


def _backend(name: str):
    import yaml
    from code_forge.backend import load_backend_configs
    from code_forge.trust import is_trusted
    gate = Path(".code-forge/gate.yaml")
    raw = yaml.safe_load(gate.read_text()) if gate.exists() else {}
    # same guard the CLI applies (cli.py _load_gate_backends): a repo
    # gate.yaml whose credential fields were edited since `code-forge
    # trust` is not a source of backends, here or there
    if raw and not is_trusted(gate, raw):
        print("Untrusted repo backends ignored. Run 'code-forge trust'.",
              file=sys.stderr)
        raw = {}
    for b in load_backend_configs(raw):
        if b.name == name:
            return b
    from code_forge.user_config import load_user_backends
    for b in load_backend_configs({"backends": load_user_backends() or {}}):
        if b.name == name:
            return b
    raise SystemExit("backend %r not in %s or user config" % (name, gate))


def _make_falsifier(engine: str, stub_default: str, backend,
                    diff_text: str | None = None, context_rows=None):
    """One judge per calibration item. `backend` is a resolved
    BackendConfig (None for stub); `diff_text` is that item's entry diff
    (the A4-0 arm). The real engine goes through build_falsifier so the
    script constructs it the way review does and never reaches into the
    falsifier's private fields."""
    from code_forge.factories import build_falsifier
    if engine == "stub":
        import tempfile
        from code_forge.falsify import StubFalsifier
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"default": stub_default}, fh)
        try:
            return StubFalsifier(Path(fh.name))
        finally:
            Path(fh.name).unlink(missing_ok=True)
    if engine == "binary":
        raise SystemExit("binary engine is not wired in this runner yet")
    return build_falsifier("real", backend=backend, diff_text=diff_text,
                           context_rows=context_rows)


def _check_clean_tree(root: Path, entry: str) -> None:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("git status failed on %s (rc=%d): %s"
                           % (entry, proc.returncode, proc.stderr.strip()[:200]))
    if proc.stdout.strip():
        raise RuntimeError("tree for %s is dirty: %s"
                           % (entry, proc.stdout.strip()[:200]))


def _reader_rows(tree_root, entry: str, diff_text: str | None):
    """A4-0b arm: apply the entry diff to its real post-image tree under
    tree_root/<entry>, grep for readers of removed identifiers, restore.
    The tree must already be at the diff's pre-image (Phase 59 prep
    puts SWE-bench base + upstream fix there for -bug entries)."""
    from code_forge.context_sources import RemovedSymbolReaders
    from code_forge.diff import get_changed_files
    if not tree_root or not diff_text:
        return []
    root = tree_root / entry
    if not root.is_dir():
        print("  (no tree for %s; readers skipped)" % entry)
        return []

    _check_clean_tree(root, entry)

    applied = subprocess.run(["git", "apply", "-"], cwd=root, input=diff_text,
                             text=True, encoding="utf-8", capture_output=True,
                             timeout=60)
    if applied.returncode != 0:
        raise RuntimeError("git apply failed on %s (rc=%d): %s"
                           % (entry, applied.returncode, applied.stderr.strip()[:200]))

    try:
        return RemovedSymbolReaders(root).facts(get_changed_files(diff_text), diff_text)
    finally:
        restore_err: str | None = None
        try:
            chk = subprocess.run(
                ["git", "checkout", "-q", "--", "."],
                cwd=root, capture_output=True, text=True,
                encoding="utf-8", timeout=60,
            )
            if chk.returncode != 0:
                restore_err = "git checkout failed to restore %s: %s" % (
                    entry, chk.stderr.strip()[:200])
            else:
                st = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=root, capture_output=True, text=True,
                    encoding="utf-8", timeout=60,
                )
                if st.returncode != 0:
                    restore_err = "git status failed after restore on %s (rc=%d): %s" % (
                        entry, st.returncode, st.stderr.strip()[:200])
                elif st.stdout.strip():
                    restore_err = "tree for %s remains dirty after restore: %s" % (
                        entry, st.stdout.strip()[:200])
        except (subprocess.SubprocessError, OSError) as exc:
            restore_err = "restore failed on %s: %s" % (entry, exc)

        if restore_err is not None:
            if sys.exc_info()[0] is None:
                raise RuntimeError(restore_err)
            print("  " + restore_err, file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("calib_dir", type=Path)
    ap.add_argument("--engine", choices=("real", "stub", "binary"), default="real")
    ap.add_argument("--stub-default", choices=("CONFIRMED", "DISMISSED"), default="CONFIRMED")
    ap.add_argument("--backend", default="review-default")
    ap.add_argument("--min-agree", type=float, default=0.9)
    ap.add_argument("--tree-root", type=Path, default=None,
                    help="dir with one real post-image checkout per entry "
                         "(<root>/<entry>/); enables the removed-symbol-"
                         "readers arm (Phase 59-A4-0b)")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="corpus.yaml; when given, each item's falsifier gets "
                         "the entry's diff (Phase 59-A4-0 arm)")
    ap.add_argument("--only", default="",
                    help="comma-separated item ids to run (resume after a kill)")
    ap.add_argument("--raw", type=Path, default=None,
                    help="calibration-raw dir; when <raw>/<entry>/ exists the "
                         "falsifier runs with that cwd")
    args = ap.parse_args()

    try:
        items = _load_items(args.calib_dir)
    except SystemExit as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    diffs: dict[str, str] = {}
    if args.corpus:
        import yaml
        doc = yaml.safe_load(args.corpus.read_text()) or {}
        base = args.corpus.parent
        for e in doc.get("entries", []):
            diffs[e["name"]] = (base / e["diff_file"]).read_text(encoding="utf-8")
        print("with-diff: %d entries loaded from %s" % (len(diffs), args.corpus))

    try:
        backend = _backend(args.backend) if args.engine == "real" else None
        fals = _make_falsifier(args.engine, args.stub_default, backend)
    except SystemExit as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if args.only:
        wanted = {x for x in args.only.split(",") if x}
        items = [it for it in items if it["id"] in wanted]
        if not items:
            print("ERROR: --only matched no items", file=sys.stderr)
            return 2

    agree = 0
    infra = 0
    for it in items:
        f = it["finding"]
        sf = StateFinding(
            id=it["id"], fingerprint=it["id"], source="L1",
            disposition=Disposition.CONFIRMED,
            file=f["file"], line_range=list(f["line_range"]),
            description=f["description"],
        )
        cwd_before = os.getcwd()
        entry_dir = (args.raw / it["entry"]) if args.raw else None
        t0 = time.monotonic()
        try:
            # everything from the chdir on is inside the finally that
            # restores it, including _reader_rows (git apply/checkout can
            # raise TimeoutExpired / CalledProcessError)
            if entry_dir and entry_dir.is_dir():
                os.chdir(entry_dir)
            judge = fals
            if diffs:
                d = diffs.get(it["entry"])
                rows = _reader_rows(args.tree_root, it["entry"], d)
                if rows:
                    print("  readers: %s" % ", ".join(
                        "%s(%s)" % (r.entity, r.downstream) for r in rows[:6]))
                judge = _make_falsifier(args.engine, args.stub_default, backend,
                                        diff_text=d, context_rows=rows)
            got = judge.falsify(sf)
        except (LLMInvokeError, subprocess.SubprocessError, OSError, RuntimeError) as exc:
            infra += 1
            print("INFRA %s %s: %s" % (it["id"], type(exc).__name__, str(exc)[:120]))
            continue
        finally:
            os.chdir(cwd_before)
        dt = time.monotonic() - t0
        got_s = got.value if hasattr(got, "value") else str(got)
        if got_s == it["expected"]:
            agree += 1
            print("OK   %s %-9s %5.1fs" % (it["id"], got_s, dt))
        else:
            print("MISS %s expected=%s got=%s %5.1fs  why=%s" % (
                it["id"], it["expected"], got_s, dt, it["why"]))
        sys.stdout.flush()

    total = len(items)
    print("agree %d/%d  (%.1f%%)  infra=%d" % (agree, total, 100.0 * agree / total, infra))
    if infra:
        return 2
    return 0 if agree / total >= args.min_agree else 1


if __name__ == "__main__":
    sys.exit(main())

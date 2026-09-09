#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Freeze the 20-item falsify calibration labels from a candidates dump.

Reads <raw>/candidates.json (from dump_calibration_candidates.py), selects
one finding per entry by a description fragment fixed in LABELS below, and
writes tests/calibration/falsify/expected.json with frozen_at = HEAD.

The fragments, expected verdicts, reasons and evidence were written by
hand on 2026-09-05 after reading each entry's diff and the SWE-bench
issue it came from. Re-running this script against a NEW dump will fail
loudly if a fragment no longer matches; that is intended -- the labels
are frozen and a label change is its own commit.
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

# (entry, description fragment, expected, why, evidence)
LABELS = [
    ("astropy__astropy-14182-bug", "no longer accepts a header_rows", "CONFIRMED",
     "reversed fix removes header_rows support from the RST writer",
     "answer key hunk rst.py:57-66 (header_rows param and __init__ signature removed)"),
    ("django__django-12143-bug", "regex injection vulnerability", "CONFIRMED",
     "re.escape(prefix) removed before interpolating into a regex",
     "answer key hunk options.py:1631-1637 (`re.escape(prefix)` -> `prefix`)"),
    ("matplotlib__matplotlib-20488-bug", "fails to handle s_vmin == 0", "CONFIRMED",
     "LogNorm guard narrowed from <=0 to <0 lets vmin=0 reach log10",
     "answer key hunk image.py:532-538 (`s_vmin <= 0` -> `s_vmin < 0`)"),
    ("psf__requests-1142-bug", "Unconditional initialization of Content-Length", "CONFIRMED",
     "Content-Length: 0 sent on GET/HEAD with no body",
     "answer key hunk models.py:386-399 (elif method not in GET/HEAD guard removed)"),
    ("pydata__xarray-2905-bug", "may cause unintended conversions for objects that have a `.values`", "CONFIRMED",
     "getattr(data, 'values') unwraps any object with .values, not only pandas",
     "answer key hunk variable.py:218-224 (isinstance pandas check -> getattr)"),
    ("pylint-dev__pylint-4661-bug", "Setting PYLINT_HOME to '.pylint.d'", "CONFIRMED",
     "cache dir moved back from the XDG-compliant appdirs path to ~/.pylint.d",
     "answer key hunk __init__.py:63-69 (appdirs.user_cache_dir -> os.path.join(USER_HOME, '.pylint.d'))"),
    ("pytest-dev__pytest-10356-bug", "loses MRO-aware mark collection", "CONFIRMED",
     "get_unpacked_marks stops walking __mro__; marks on base classes are lost",
     "answer key hunk structures.py:355-372 (consider_mro loop removed)"),
    ("scikit-learn__scikit-learn-10297-bug",
     "Removal of store_cv_values parameter from RidgeClassifierCV.__init__ while parent", "CONFIRMED",
     "RidgeClassifierCV loses store_cv_values while its docstring still promises cv_values_",
     "answer key hunk ridge.py:1333-1342 (store_cv_values param removed from __init__)"),
    ("sphinx-doc__sphinx-7440-bug", "Added lowercase=True to XRefRole", "CONFIRMED",
     "term registration lowercased again: case-different glossary entries collide as duplicates",
     "answer key hunks std.py:305-311 and 565-571 (the two lower() sites)"),
    ("sympy__sympy-12096-bug", "Removed `evalf(prec)` on each argument", "CONFIRMED",
     "_imp_ receives symbolic args; implemented_function(...).evalf() no longer recurses",
     "answer key hunk function.py:507-513 (`arg.evalf(prec)` -> `arg`)"),
    ("astropy__astropy-13033-clean", "will raise TypeError if required_columns is a scalar", "DISMISSED",
     "required_columns is a list at this call site; the new helper handles the scalar case for the message only",
     "core.py:55-63 helper branches on hasattr(__len__); line 84 len() is on self._required_columns, "
     "which every subclass defines as a list"),
    ("django__django-12143-clean", "escapes prefix but not self.model", "DISMISSED",
     "pk.name is a Python identifier validated by the model metaclass; it cannot contain regex metacharacters",
     "the diff only restores re.escape(prefix); pk.name was never escaped upstream either"),
    ("matplotlib__matplotlib-20676-clean", "for initial edge handle setup", "DISMISSED",
     "this is the upstream fix (mpl#20676): seeding handles from self.extents extended the axis limits at construction",
     "using the axis bounds keeps xlim unchanged, which is the behaviour the upstream test asserts"),
    ("psf__requests-1142-clean", "Good change: removes unconditional Content-Length", "DISMISSED",
     "the finding text itself says the change is correct; CONFIRMED on praise is a false positive",
     "models.py:395 guard is the upstream fix for requests#1142"),
    ("pydata__xarray-3305-clean", "Use of deprecated 'interpolation' parameter", "DISMISSED",
     "the parameter is pre-existing and untouched; the diff only threads keep_attrs through quantile",
     "hunks at variable.py:1592 and 1615 add keep_attrs only; interpolation= is in context lines"),
    ("pylint-dev__pylint-7277-clean", "Changing the unconditional sys.path.pop(0) to a conditional pop", "DISMISSED",
     "this is the upstream fix (pylint#7277): the unconditional pop removed a legitimate first entry",
     "upstream tests/test_self.py::test_modify_sys_path covers the three literals"),
    ("pytest-dev__pytest-10051-clean", "may break if the handler object does not have a clear method", "DISMISSED",
     "LogCaptureHandler.clear() is defined in the same file as part of this change; reset() was the misnamed one",
     "logging.py LogCaptureHandler defines clear(); the diff renames the call to match"),
    ("scikit-learn__scikit-learn-10844-clean", "Casting contingency matrix to int64", "DISMISSED",
     "the cast is the fix (sklearn#10844): int32 overflow in c.data**2 for large n; the finding restates it as a defect",
     "supervised.py:855 astype(np.int64) plus the sqrt refactor at 860 avoid the reported overflow"),
    ("sphinx-doc__sphinx-7440-clean",
     "Removal of '.lower()' in std.note_object makes glossary term registration case-sensitive", "DISMISSED",
     "removing lower() is the upstream fix (sphinx#7440): case-different glossary terms must not collide",
     "std.py:308 and 568 drop the normalisation together, so registration and xref agree"),
    ("sympy__sympy-13551-clean", "assumes p is positive and real", "DISMISSED",
     "exp(Sum(log)) is the upstream fix (sympy#13551); the additive split gave wrong products",
     "products.py:285 upstream commit; the q-Pochhammer test case the old code got wrong"),
]


def main() -> int:
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".planning/eval/phase-59/calibration-raw")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("tests/calibration/falsify/expected.json")
    cands = json.loads((raw / "candidates.json").read_text())
    by: dict[str, list] = collections.defaultdict(list)
    for x in cands:
        by[x["entry"]].append(x)
    items = []
    for i, (entry, frag, expected, why, evidence) in enumerate(LABELS, 1):
        hits = [x for x in by[entry] if frag in x["description"]]
        if not hits:
            sys.exit("no candidate in %s matches %r; have: %s" % (
                entry, frag, [x["description"][:60] for x in by[entry]]))
        x = hits[0]
        items.append({
            "id": "c%02d" % i, "entry": entry,
            "finding": {"file": x["file"], "line_range": x["line_range"],
                        "description": x["description"]},
            "expected": expected, "why": why, "evidence": evidence,
            "model_disposition_at_dump": x["disposition"],
        })
    head = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    doc = {
        "version": 1, "frozen_at": head,
        "backend": "review-default (mimo-v2.5-pro)",
        "dumped_from": str(raw) + " (depth=1, 2026-09-05)",
        "items": items,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    c = collections.Counter((i["expected"], i["model_disposition_at_dump"]) for i in items)
    print("%d items frozen at %s" % (len(items), head[:8]))
    for k, v in sorted(c.items()):
        print("  expected=%-9s model_at_dump=%-9s %d" % (k[0], k[1], v))
    return 0


if __name__ == "__main__":
    sys.exit(main())

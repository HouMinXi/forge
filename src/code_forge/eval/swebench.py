"""Build eval corpus entries from SWE-bench_Verified.

SWE-bench ships, per instance, the patch that FIXES a real bug plus the
problem statement its users wrote. Neither is directly a corpus entry:

- The patch is the repair. Reviewing it asks whether correct code is
  correct. What a reviewer actually faces is the change that INTRODUCES
  the defect, so the patch is reversed (`reverse_patch`).
- SWE-bench carries no source files, only patch text and commit hashes,
  while the eval runner seeds each replay from `base_files/<entry>/` and
  then runs `git apply`. Without a base tree every entry fails to apply
  and scores as a miss. The patch's own context lines reconstruct enough
  of the pre-fix file to apply against (`reconstruct_base_files`).

Neither step makes the corpus synthetic: the defect, the repair, and the
problem statement are all real, from projects that had nothing to do with
forge.
"""

from __future__ import annotations

import random
import re
from enum import Enum

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

_NULL = "/dev/null"


def _swap_hunk_header(line: str) -> str:
    """Turn '@@ -A,B +C,D @@ ctx' into '@@ -C,D +A,B @@ ctx'.

    git omits the count for single-line ranges ('@@ -5 +7 @@'), and the
    text after the closing @@ is the enclosing function, emitted as
    context. Both are preserved: dropping the count changes the range,
    and dropping the context loses the only human landmark in the hunk.
    """
    m = _HUNK_RE.match(line)
    if m is None:
        return line
    old_start, old_count, new_start, new_count, tail = m.groups()
    left = new_start if new_count is None else "%s,%s" % (new_start, new_count)
    right = old_start if old_count is None else "%s,%s" % (old_start, old_count)
    return "@@ -%s +%s @@%s" % (left, right, tail)


def _swap_file_header(line: str) -> str:
    """Swap a --- / +++ header, fixing both the side and the path prefix.

    Two things move, and handling only one is the trap. The side swaps
    (--- becomes +++), and so does the prefix: git's 'a/' always names
    the pre-image and 'b/' the post-image, so a path that was the new
    version becomes the old one and must be re-prefixed. Swapping sides
    alone yields '--- b/f.py', which reads as "the old version is the
    b-side" and is self-contradictory.

    /dev/null carries no prefix and moves sides unchanged. A patch that
    creates a file carries '--- /dev/null'; reversed, it deletes one, so
    /dev/null must end up on the +++ side. git validates that against
    the mode line and rejects a mismatch ('bad git-diff - expected
    /dev/null').
    """
    path = line[4:]
    to_minus = line.startswith("+++ ")
    if path != _NULL:
        if path.startswith("a/"):
            path = "b/" + path[2:]
        elif path.startswith("b/"):
            path = "a/" + path[2:]
    return ("--- " if to_minus else "+++ ") + path


def reconstruct_base_files(patch: str) -> dict[str, str]:
    """Rebuild enough of each touched file for `git apply` to accept patch.

    Returns {path: content}. `git apply` verifies that a hunk's context
    and removed lines match the file at the hunk's line numbers; it does
    not care what lies between hunks. So the pre-image of each hunk,
    placed at its start line with blank padding in between, is sufficient
    -- and needs no repository clone.

    A file the patch CREATES is deliberately absent from the result. git
    refuses to create a file that already exists, so a stub there breaks
    the patch it was meant to enable. That failure is inverted from the
    usual one and easy to miss.

    Two limits, recorded rather than hidden. The padding splits class and
    function bodies, so most reconstructions are not valid Python -- fine
    for diff-mode review, fatal for any pass that parses whole files. And
    a reviewer sees only the hunk neighbourhood, never the surrounding
    module, which makes the review task harder than the real one. Both
    belong in the provenance record wherever these numbers are quoted.
    """
    files: dict[str, str] = {}
    path: str | None = None
    creates = False
    lines: dict[int, str] = {}
    cursor = 0

    def commit() -> None:
        if path is None or creates or not lines:
            return
        size = max(lines)
        body = [lines.get(n, "") for n in range(1, size + 1)]
        files[path] = "\n".join(body) + "\n"

    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            commit()
            path, creates, lines, cursor = None, False, {}, 0
        elif line.startswith("new file mode "):
            creates = True
        elif line.startswith("--- "):
            src = line[4:]
            # The pre-image path. /dev/null means the file is created,
            # already flagged by the mode line but stated in both places.
            if src == _NULL:
                creates = True
            elif src.startswith(("a/", "b/")):
                path = src[2:]
            else:
                path = src
        elif line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m is not None:
                cursor = int(m.group(1))
        elif cursor and (line.startswith(" ") or line.startswith("-")):
            # Context and removed lines are both present in the pre-image;
            # added lines are not.
            lines[cursor] = line[1:]
            cursor += 1

    commit()
    return files


def reverse_patch(patch: str) -> str:
    """Return the diff that introduces the defect this patch fixes.

    Reversal is symmetric by construction -- applying it twice returns
    the input -- which is the cheapest check that no trap is handled on
    only one side.
    """
    out: list[str] = []
    # A file's two headers must be emitted --- first, but reversing swaps
    # which one comes off the input first. Buffer the pair, then order it.
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        swapped = [_swap_file_header(h) for h in pending]
        swapped.sort(key=lambda h: 0 if h.startswith("--- ") else 1)
        out.extend(swapped)
        pending.clear()

    for line in patch.split("\n"):
        if line.startswith("--- ") or line.startswith("+++ "):
            pending.append(line)
            continue
        flush()
        if line.startswith("@@"):
            out.append(_swap_hunk_header(line))
        elif line.startswith("new file mode "):
            out.append("deleted file mode " + line[len("new file mode "):])
        elif line.startswith("deleted file mode "):
            out.append("new file mode " + line[len("deleted file mode "):])
        elif line.startswith("+"):
            out.append("-" + line[1:])
        elif line.startswith("-"):
            out.append("+" + line[1:])
        else:
            out.append(line)
    flush()
    return "\n".join(out)


def _first_line(statement: str) -> str:
    """The defect title, taken as the statement's first non-empty line.

    Measured across the 500 instances: median 62 characters, and in every
    sample inspected the title of the defect. The full statement runs a
    median 1185 characters and carries reproduction code, which matters
    because score_findings matches on shared terms -- a long description
    makes a hit trivial to satisfy, inflating recall without the tool
    having improved.
    """
    for raw in statement.split("\n"):
        line = raw.strip()
        if not line:
            continue
        # Markdown headings are common in these statements and the marks
        # are not part of the title.
        return line.lstrip("#").strip()
    return ""


def expected_findings_for(patch: str, problem_statement: str):
    """Answer key for one instance: one ExpectedFinding per hunk.

    Per-hunk rather than per-file, because scorer.py compares line ranges
    and returns on that alone when both sides have one -- it never falls
    back to description matching. A per-file key on a multi-hunk patch
    therefore makes every hunk but one unhittable, and a per-file key on a
    multi-file patch turns one defect into N expected findings that 1:1
    matching can only satisfy once.

    The range is the reversed hunk's new side, since the entry under
    review is the reversed patch and the defect occupies the lines it
    adds. A hunk whose reversed new side is empty gets `line_range=None`
    rather than an inverted range. Measured across the 500 instances this
    is rare -- 1 hunk in 1220 -- but one is enough: (start, start-1) is
    rejected by valid_line_range, which takes load_corpus down before
    anything is scored, and `@@ -10,0` is legal git. An earlier draft of
    this comment claimed 32%, conflating "no + line in the reversed body"
    (391 of 1220, true and harmless, since header counts include context)
    with "header count of zero" (1 of 1220, the case that actually
    breaks).

    Raises ValueError when the statement has no usable first line or the
    patch has no hunks -- both mean the instance cannot become an entry,
    and returning an empty list would let it become one silently.
    """
    from code_forge.eval.corpus import ExpectedFinding

    description = _first_line(problem_statement)
    if not description:
        raise ValueError("problem statement has no usable first line")

    findings = []
    current_file = None
    for line in patch.split("\n"):
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == _NULL:
                # The fix DELETES this file. Reversed, the entry restores
                # it, and there is no post-image path to point a finding
                # at. Clearing rather than leaving the previous value is
                # the point: a stale current_file would file this hunk's
                # findings against the previous file in the patch, where
                # nothing matches and the answer key is unhittable. No
                # instance in the current corpus deletes a source file, so
                # this costs nothing today and silently corrupts the key
                # the first time one does.
                current_file = None
            else:
                current_file = path[2:] if path.startswith("b/") else path
            continue
        m = _HUNK_RE.match(line)
        if not m or current_file is None:
            continue
        # Reversing swaps the sides, so the reversed patch's new side is
        # this patch's old side.
        start = int(m.group(1))
        count = 1 if m.group(2) is None else int(m.group(2))
        line_range = (start, start + count - 1) if count > 0 else None
        findings.append(
            ExpectedFinding(
                file=current_file,
                description=description,
                line_range=line_range,
            )
        )

    if not findings:
        raise ValueError("patch contains no hunks")
    return findings


class RejectReason(str, Enum):
    """Why an instance cannot become a corpus entry.

    Written verbatim into the provenance record, so these are a persisted
    contract rather than internal labels. A corpus that reports only its
    survivors cannot be audited -- the reader cannot tell a strict filter
    from a broken one.
    """

    NO_SOURCE_FILES = "no_source_files"
    TOO_MANY_FILES = "too_many_files"
    TOO_MANY_HUNKS = "too_many_hunks"
    HUNK_TOO_LARGE = "hunk_too_large"
    UNUSABLE_STATEMENT = "unusable_statement"
    PURE_ADDITION = "pure_addition"
    REVERSAL_FAILED = "reversal_failed"


MAX_FILES = 3
MAX_HUNKS = 5
MAX_HUNK_LINES = 50
MIN_TITLE_CHARS = 20

_NON_SOURCE_PREFIXES = ("docs/", "doc/", "tests/", "test/", "testing/")
_NON_SOURCE_SUFFIXES = (".md", ".rst", ".txt", ".cfg", ".ini", ".toml")
_TRACEBACK_MARKERS = ("Traceback", 'File "', "  at ")


def _touched_files(patch: str) -> list[str]:
    out = []
    for line in patch.split("\n"):
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != _NULL:
                out.append(path[2:] if path.startswith("b/") else path)
    return out


def _is_source(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path.startswith(_NON_SOURCE_PREFIXES) or path.endswith(_NON_SOURCE_SUFFIXES):
        return False
    return not (name.startswith("test_") or name.endswith("_test.py"))


def qualifies(instance: dict):
    """None when the instance can become an entry, else a RejectReason.

    Every threshold is a module constant rather than an argument. They are
    qualification predicates -- they decide what counts as a valid test
    case -- and relaxing one to raise the corpus count is the failure this
    phase is most exposed to. Keeping them out of the call signature makes
    such a change a visible diff rather than a caller's choice.
    """
    patch = instance["patch"]

    files = _touched_files(patch)
    if not any(_is_source(f) for f in files):
        return RejectReason.NO_SOURCE_FILES
    if len(files) > MAX_FILES:
        return RejectReason.TOO_MANY_FILES

    hunks = [ln for ln in patch.split("\n") if _HUNK_RE.match(ln)]
    if len(hunks) > MAX_HUNKS:
        return RejectReason.TOO_MANY_HUNKS
    for ln in hunks:
        m = _HUNK_RE.match(ln)
        old = 1 if m.group(2) is None else int(m.group(2))
        new = 1 if m.group(4) is None else int(m.group(4))
        if max(old, new) > MAX_HUNK_LINES:
            return RejectReason.HUNK_TOO_LARGE

    title = _first_line(instance["problem_statement"])
    if len(title) < MIN_TITLE_CHARS or title.startswith(_TRACEBACK_MARKERS):
        return RejectReason.UNUSABLE_STATEMENT

    # A fix that removes nothing adds a capability the code never had.
    # Reversed, it deletes a working feature -- deliberate scope reduction
    # to any reader, not a defect. Scoring a reviewer for missing it would
    # measure nothing. A fix that ADDS a guard reverses into deleting one,
    # which is a different cognitive task but still a real review target,
    # and is kept.
    body_removals = any(
        ln.startswith("-") and not ln.startswith("---") for ln in patch.split("\n")
    )
    if not body_removals:
        return RejectReason.PURE_ADDITION

    return None


def select_instances(instances: list[dict], cap: int = 8, seed: int = 20260830):
    """Stratified sample of ALREADY-QUALIFIED instances, capped per repo.

    An allocation parameter, not a filter: changing `cap` adds or removes
    instances that qualified either way, and can never change which ones
    qualify. That boundary is why the cap lives here and the thresholds
    live in `qualifies` as constants.

    Ids are sorted before sampling because the dataset's iteration order
    is not a contract. A seeded sample over an unsorted pool reproduces
    only by luck, and a corpus that cannot be regenerated cannot be
    audited.
    """
    by_repo: dict[str, list[dict]] = {}
    for inst in instances:
        by_repo.setdefault(inst["repo"], []).append(inst)

    picked = []
    for repo in sorted(by_repo):
        pool = sorted(by_repo[repo], key=lambda i: i["instance_id"])
        rng = random.Random("%d:%s" % (seed, repo))
        if len(pool) <= cap:
            picked.extend(pool)
        else:
            picked.extend(rng.sample(pool, cap))
    return sorted(picked, key=lambda i: i["instance_id"])


_LIMITATIONS = (
    "Base files are reconstructed from each patch's own context lines, so "
    "they carry the hunk neighbourhood and nothing else. They are valid "
    "input for diff-mode review, which reads the patch. They are NOT valid "
    "Python: padding to the hunk's start line splits class and function "
    "bodies, so any future pass that parses whole files will fail on these "
    "entries. The review task is also harder than the real one, since the "
    "reviewer sees no surrounding code -- state this wherever these "
    "numbers are quoted. "
    "Descriptions are the problem statement's first line verbatim, and "
    "SWE-bench statements are written by reporters: some name the desired "
    "fix or a failing test rather than the defect. This does not affect "
    "scoring for this corpus -- every answer key carries a line range, and "
    "the scorer only falls back to description tokens when one is absent "
    "-- but it makes individual keys less self-explanatory, and a corpus "
    "built with fewer ranges would expose it."
)


def build_corpus(
    instances: list[dict],
    out_dir,
    rejections: list[str],
    cap: int = 8,
    seed: int = 20260830,
) -> None:
    """Write the corpus and the provenance record that makes it auditable.

    Two entries per instance. The HOLD entry reviews the reversed patch --
    the change that introduces the defect. The PASS entry reviews the fix
    itself: a real change by the same authors in the same repo that
    resolves a defect rather than causing one, which is what a clean
    control has to be.

    The PASS entries carry `asserts_no_findings`, without which they would
    be treated as unannotated and contribute nothing to findings_fp. That
    is the difference between negative controls that measure precision and
    negative controls that are decoration.

    Refuses to write into a directory that already holds a corpus. A
    regenerated corpus must be a deliberate act, because the provenance
    hashes are what prove the entries were not edited after scoring.
    """
    import hashlib
    import json
    import pathlib

    import yaml

    out = pathlib.Path(out_dir)
    if (out / "corpus.yaml").exists():
        raise FileExistsError("corpus already exists at %s" % out)
    (out / "diffs").mkdir(parents=True, exist_ok=True)

    entries = []
    for inst in instances:
        iid = inst["instance_id"]
        patch = inst["patch"]
        reversed_patch = reverse_patch(patch)

        # The two shapes need DIFFERENT base trees, which is easy to miss
        # because both derive from one instance. The clean entry applies
        # the fix, so its base is the pre-fix file -- reconstructed from
        # the original patch. The bug entry applies the REVERSED fix, so
        # its base is the post-fix file, which is the reversed patch's own
        # pre-image. Reconstructing both from the original patch leaves
        # every bug entry unappliable, and the corpus would look complete
        # while skipping half of itself.
        base_by_suffix = {
            "bug": reconstruct_base_files(reversed_patch),
            "clean": reconstruct_base_files(patch),
        }

        for suffix, verdict, diff_text in (
            ("bug", "HOLD", reversed_patch),
            ("clean", "PASS", patch),
        ):
            name = "%s-%s" % (iid, suffix)
            rel = "diffs/%s.diff" % name
            (out / rel).write_text(diff_text, encoding="utf-8")

            base_dir = out / "base_files" / name
            for path, text in base_by_suffix[suffix].items():
                target = base_dir / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            base_dir.mkdir(parents=True, exist_ok=True)

            entry = {
                "name": name,
                "diff_file": rel,
                "expected_verdict": verdict,
                "axis_tags": ["RUNTIME"],
            }
            if verdict == "HOLD":
                found = expected_findings_for(patch, inst["problem_statement"])
                entry["expected_findings"] = [
                    {
                        "file": f.file,
                        "description": f.description,
                        **(
                            {"line_range": list(f.line_range)}
                            if f.line_range is not None
                            else {}
                        ),
                    }
                    for f in found
                ]
            else:
                entry["asserts_no_findings"] = True
            entries.append(entry)

    (out / "corpus.yaml").write_text(
        yaml.safe_dump({"entries": entries}), encoding="utf-8"
    )

    # Required rather than optional: an omitted list and a genuinely
    # empty one produce the same empty provenance map, and a corpus whose
    # record shows no rejections is indistinguishable from one whose
    # caller forgot to pass them. The rejection counts are how a reader
    # tells a strict filter from a broken one, so they cannot be
    # accidentally absent.
    counts: dict[str, int] = {}
    for reason in rejections:
        counts[reason] = counts.get(reason, 0) + 1

    provenance = {
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "split": "test",
        "generated_by": "code_forge.eval.swebench.build_corpus",
        "cap": cap,
        "seed": seed,
        "instances_selected": len(instances),
        "hold_entries": sum(1 for e in entries if e["expected_verdict"] == "HOLD"),
        "pass_entries": sum(1 for e in entries if e["expected_verdict"] == "PASS"),
        "rejections": counts,
        "limitations": _LIMITATIONS,
        "diff_sha256": {
            e["diff_file"]: hashlib.sha256(
                (out / e["diff_file"]).read_bytes()
            ).hexdigest()
            for e in entries
        },
        "corpus_sha256": hashlib.sha256(
            (out / "corpus.yaml").read_bytes()
        ).hexdigest(),
    }
    (out / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )

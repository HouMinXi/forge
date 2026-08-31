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

import re

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
    rather than an inverted range: 32% of hunks are pure insertions in the
    fix, and (start, start-1) is rejected by valid_line_range, taking
    load_corpus down before anything is scored.

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
            if path != _NULL:
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

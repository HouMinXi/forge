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
        swapped = [_swap_file_header(l) for l in pending]
        swapped.sort(key=lambda l: 0 if l.startswith("--- ") else 1)
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

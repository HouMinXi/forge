"""code-forge verify: receipt validation.

parse_diff_files is a shared helper used by both the verify CLI
handler and the receipt writer.

When hardened=True (default), checks 5/6/7 use reviewer-provided
code_excerpts vs the diff post-image snapshot. When hardened=False,
the original pre-Phase-14 checks run (for fail-before tests and
backward compatibility).

The real anti-shirk guarantees are the R1 pre-commit test gate and
the StateMachine consecutive-clean counter; verify is a tamper check
on receipts, not a replacement for them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from .diff import _extract_post_image_lines, parse_diff_hunks
from .errors import CorruptedReceiptError, UnreadableGateError
from .reviewer_json import excerpt_line_count_matches, excerpt_lines

logger = logging.getLogger(__name__)

# A cycle is three receipts because three skills run -- qodo, expert,
# adversarial -- and that number is not a threshold to tune. It is the
# count of distinct perspectives, so lowering it does not make review
# cheaper, it makes a whole class of defect unlooked-for: drop
# adversarial and nothing is hunting edge cases, drop expert and nothing
# is reading architecture. The completeness check below enforces exactly
# passes 1-3 per cycle for the same reason. Do not give this a knob.
PASSES_PER_CYCLE = 3

# How many consecutive clean cycles the gate demands, on the other hand,
# is a real tradeoff and configurable through gate.yaml. Three is the
# convergence claim: a cycle that finds something resets the counter, so
# the gate watches the fix get re-reviewed. Fewer buys a shorter wait
# with that evidence.
DEFAULT_REQUIRED_CYCLES = 3


def read_required_cycles(cwd: Path) -> int:
    """How many consecutive clean cycles this repo's gate demands.

    Reads verify.required_cycles from gate.yaml. Deliberately not
    load_gate_config: that one raises unless the file carries a full
    'test' section, and a repo that has not configured a test runner
    should still be able to run verify.

    No gate.yaml and no verify section both fall back to
    DEFAULT_REQUIRED_CYCLES -- a repo that never stated a policy. A
    verify section that is present but null (verify: / verify:~) or not
    a mapping (verify: 5, verify: "5") raises: the key is there, and
    reading a written-down invalid policy as "no policy" silently relaxes
    what the author asked for.

    A required_cycles key that is present but not a positive int raises
    for the same reason: a repo that wrote required_cycles: "5" meant
    five cycles and is being asked for three; the typo reads as weaker,
    so defaulting there would silently relax what was written down.

    A gate.yaml that exists but cannot be read raises too. That case is
    different in kind: the file is a policy we cannot see, so the
    fallback would be guessing at it, and the guess is lower than what a
    repo demanding five cycles wrote down. Failing loudly there costs a
    confusing error; defaulting costs a gate that quietly stopped
    enforcing what it was configured to enforce.
    """
    path = cwd / ".code-forge" / "gate.yaml"
    import yaml
    # Trust model: gate.yaml is local repo config the user controls,
    # not untrusted external input.  Unlike backend credentials (which
    # _load_gate_backends guards behind is_trusted), verify.required_cycles
    # is a gate-tightening knob -- the user chose it.  An untrusted repo
    # cannot weaken the local gate below the CLI's --required-cycles,
    # which is the caller's floor.
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # The one absent case is a path that is not there at all. A
        # dangling symlink is present and unreadable: the file the policy
        # lives in is a broken link, which is not the same as the repo
        # never having configured one, so it raises like any other
        # unreadable gate instead of masquerading as "no policy".
        if path.is_symlink():
            raise UnreadableGateError(
                f"{path} is a dangling symlink; cannot read the policy"
            ) from None
        if path.parent.is_symlink() and not path.parent.exists():
            raise UnreadableGateError(
                f"{path} is inside a dangling symlink; cannot read the policy"
            ) from None
        return DEFAULT_REQUIRED_CYCLES
    except Exception as exc:
        # FileNotFoundError is handled above, so everything that lands
        # here is a policy we cannot read. The import sits outside the
        # try so a missing PyYAML surfaces as an environment error, not
        # as this gate blaming the file.
        raise UnreadableGateError(
            f"{path} exists but could not be parsed: {exc}"
        ) from exc
    if data is None or not isinstance(data, dict):
        # An empty file, or a parse that yielded no mapping: no policy
        # stated.
        return DEFAULT_REQUIRED_CYCLES
    if "verify" not in data:
        return DEFAULT_REQUIRED_CYCLES
    section = data["verify"]
    if section is None:
        raise UnreadableGateError(
            f"{path} verify section is present but null; a written-down policy must be a mapping or absent"
        )
    if not isinstance(section, dict):
        raise UnreadableGateError(
            f"{path} verify section is {section!r}; must be a mapping"
        )
    unknown = set(section) - {"required_cycles"}
    if unknown:
        raise UnreadableGateError(
            f"{path} verify section has unknown key(s): {', '.join(sorted((str(k) for k in unknown)))}; a misspelled knob would read as absent and silently open the gate"
        )
    if "required_cycles" not in section:
        return DEFAULT_REQUIRED_CYCLES
    n = section["required_cycles"]
    if n is None:
        raise UnreadableGateError(
            f"{path} verify.required_cycles must be an integer; got null/blank"
        )
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise UnreadableGateError(
            f"{path} verify.required_cycles is {n!r}; must be a positive int"
        )
    return n


@dataclass
class VerifyResult:
    passed: bool
    reason: str
    checks_run: int = 0
    checks_passed: int = 0


def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
    """Parse git diff text into {file: [changed line numbers]}."""
    import re
    diff_files: dict[str, list[int]] = {}
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@") and current_file:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or "1")
                if current_file not in diff_files:
                    diff_files[current_file] = []
                diff_files[current_file].extend(
                    range(start, start + count)
                )
    return diff_files


# The field shapes every check below indexes into. Receipts have TWO
# writers, and the schema has to hold for both: write_receipts() in
# receipt.py, and a reviewer hand-writing JSON from the documented shape in
# skills/code-forge/SKILL.md. Deriving this from receipt.py alone is what
# made an earlier draft reject real receipts written the other way, so
# widen the measurement, not just the guess, before adding a field here.
# _validate_receipt_schema enforces these once, so the 7 checks in
# run_verify can use plain dict access instead of each carrying its own
# copy of the same defensive isinstance guards.
_STR_FIELDS = ("diff_sha256", "timestamp")
_INT_FIELDS = ("cycle", "pass", "findings_count")
_LIST_OF_DICT_FIELDS = ("findings", "anchors", "code_excerpts")
# context_quotes is where a reviewer puts code it read for orientation but did
# not verify: a function signature above the change, a caller in another file.
# That code is not in the diff, so nothing here can check it, and it must not
# be able to sit in code_excerpts wearing the same clothes as evidence. It is
# optional because every receipt written before this field existed is still a
# valid receipt.
_OPTIONAL_LIST_OF_DICT_FIELDS = ("context_quotes",)
_NESTED_SCHEMAS = {
    "code_excerpts": {"file": str, "content": str, "start_line": int, "end_line": int},
    "context_quotes": {"file": str, "content": str},
}
_TYPE_LABEL = {str: "a string", int: "an integer"}


def _is_type(value, expected_type: type) -> bool:
    if expected_type is int:
        # bool subclasses int in Python; a stray JSON true/false must not
        # silently pass a cycle/pass/findings_count check as 1/0.
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected_type)


def _validate_receipt_schema(obj: dict, name: str) -> None:
    """Raise CorruptedReceiptError if obj's field types do not match what
    the checks below actually index into: cycle/pass/findings_count are
    int, diff_sha256/timestamp are str, and findings/anchors/code_excerpts
    are lists of dicts (code_excerpts further checked field-by-field,
    since the hardened excerpt check indexes straight into it). Checked
    once here so none of the 7 checks in run_verify need to re-guard the
    same shape at their own call site.

    covered_line_ranges is deliberately NOT checked. Receipts on disk carry
    it in two shapes -- {"file","start","end"} and the string form
    "path:start-end" -- and asserting either one rejects real, healthy
    receipts written by an older forge. Nothing on the production path
    reads it: run_verify's caller always takes the hardened branch, which
    treats the field as self-reported audit data and ignores it.
    """
    for field in _STR_FIELDS:
        if not _is_type(obj.get(field), str):
            raise CorruptedReceiptError(
                f"{name}: {field} must be {_TYPE_LABEL[str]}")
    for field in _INT_FIELDS:
        if not _is_type(obj.get(field), int):
            raise CorruptedReceiptError(
                f"{name}: {field} must be {_TYPE_LABEL[int]}")
    for field in _LIST_OF_DICT_FIELDS:
        v = obj.get(field)
        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
            raise CorruptedReceiptError(
                f"{name}: {field} must be a list of objects")
    for field in _OPTIONAL_LIST_OF_DICT_FIELDS:
        if field not in obj:
            continue
        v = obj[field]
        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
            raise CorruptedReceiptError(
                f"{name}: {field} must be a list of objects")
    # Safe only because the two loops above have proved every field named in
    # _NESTED_SCHEMAS is either absent or a list of dicts -- otherwise calling
    # .get() on a non-dict item here would raise the exact crash this function
    # exists to prevent. Keep the three collections in step when adding a
    # field: a name in _NESTED_SCHEMAS with no home in either list loop is
    # unguarded.
    for list_field, subschema in _NESTED_SCHEMAS.items():
        for item in obj.get(list_field, []):
            for subfield, subtype in subschema.items():
                if not _is_type(item.get(subfield), subtype):
                    raise CorruptedReceiptError(
                        f"{name}: {list_field}.{subfield} must be {_TYPE_LABEL[subtype]}")
    # Excerpt line ranges must be ordered and positive. An inverted
    # range silently credits zero lines, which looks identical to an
    # honest excerpt that sits outside the diff -- two different
    # problems, one symptom, no way to tell apart. A bool coordinate
    # (JSON true/false) or a nonpositive one is not a source line and
    # must not reach the checks below.
    for exc in obj.get("code_excerpts", []):
        s = exc.get("start_line")
        e = exc.get("end_line")
        if not _is_type(s, int) or not _is_type(e, int):
            continue
        if s > e:
            raise CorruptedReceiptError(
                f"{name}: code_excerpts start_line {s} > end_line {e}")
        if s <= 0 or e <= 0:
            raise CorruptedReceiptError(
                f"{name}: code_excerpts start_line and end_line must be positive, got {s!r} and {e!r}")


def _load_receipts(rd: Path) -> list[dict]:
    """Load every receipt-*.json in rd.

    Raises CorruptedReceiptError naming the file when one cannot be read,
    cannot be parsed, does not hold a JSON object, or does not match the
    receipt schema (see _validate_receipt_schema). Unreadable receipts
    are not skipped: the count and the cycle/pass matrix are themselves
    checks, so dropping a file would report a corrupt receipt as a missing
    one and hide the real cause.
    """
    if not rd.exists():
        return []
    receipts = []
    for f in sorted(rd.glob("receipt-*.json")):
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError, RecursionError) as exc:
            # Catch ValueError itself, not its subclasses: JSONDecodeError and
            # UnicodeDecodeError both derive from it, and so does json.loads
            # refusing an integer literal longer than
            # sys.get_int_max_str_digits(). Naming the subclasses let that last
            # one through. RecursionError (deeply nested input) is a
            # RuntimeError and still needs naming. MemoryError is left uncaught
            # on purpose: that is a resource condition, not a bad file.
            raise CorruptedReceiptError(f"{f.name}: {exc}") from exc
        if not isinstance(obj, dict):
            # Every check downstream calls .get() on these. A bare array or
            # number parses cleanly and then crashes the caller with an
            # AttributeError, so the annotation above is enforced here.
            raise CorruptedReceiptError(
                f"{f.name}: expected a JSON object, got {type(obj).__name__}"
            )
        _validate_receipt_schema(obj, f.name)
        receipts.append(obj)
    # Order by the numbers inside the receipts, not by their filenames. The
    # glob sorts as text, so once a review passes nine cycles "receipt-c10p1"
    # sorts ahead of "receipt-c9p1" and the monotonic-timestamp check reads a
    # correctly written set as out of order. The schema check above has
    # already proved cycle and pass are integers.
    receipts.sort(key=lambda r: (r["cycle"], r["pass"]))
    return receipts


def _covered(receipt: dict) -> set[tuple[str, int]]:
    # Reached only from the legacy branch, which run_verify's production
    # caller never takes. Receipts carry covered_line_ranges in two shapes:
    # dict {"file","start","end"} and string "path:start-end". Both are
    # real (352 dict-shaped, 156 string-shaped on disk as of 2026-07-28).
    s = set()
    for r in receipt.get("covered_line_ranges", []):
        if isinstance(r, str):
            # "path:start-end"
            try:
                path, range_part = r.rsplit(":", 1)
                start_s, end_s = range_part.split("-", 1)
                start, end = int(start_s), int(end_s)
            except (ValueError, IndexError):
                continue
            for ln in range(start, end + 1):
                s.add((path, ln))
        elif isinstance(r, dict):
            for ln in range(r["start"], r["end"] + 1):
                s.add((r["file"], ln))
    return s


def _cycle_covered(receipts: list[dict], cycle: int) -> set[tuple[str, int]]:
    u = set()
    for r in receipts:
        if r["cycle"] == cycle:
            u |= _covered(r)
    return u


def _excerpt_covered(receipt: dict) -> set[tuple[str, int]]:
    s = set()
    for exc in receipt.get("code_excerpts", []):
        f = exc.get("file", "")
        start = exc.get("start_line", 0)
        end = exc.get("end_line", 0)
        content = exc.get("content", "")
        if isinstance(start, int) and isinstance(end, int) and f:
            # Credit only the lines the excerpt actually shows. The declared
            # range used to be trusted on its own, so claiming 1-1000 while
            # pasting three lines earned 1000 lines toward the 60% floor in
            # check 6 -- and the content check upstream never noticed,
            # because it only compares lines the content actually has.
            shown = len(content.splitlines()) if isinstance(content, str) else 0
            for ln in range(start, min(end, start + shown - 1) + 1):
                s.add((f, ln))
    return s


def _cycle_excerpt_covered(receipts: list[dict], cycle: int) -> set[tuple[str, int]]:
    u = set()
    for r in receipts:
        if r["cycle"] == cycle:
            u |= _excerpt_covered(r)
    return u


def _coverage_failure_detail(
    cov: set[tuple[str, int]], all_diff: set[tuple[str, int]],
) -> str:
    """Name the files a failed coverage check is missing, so the
    failure points at the gap rather than only at a percentage.
    """
    uncovered = all_diff - cov
    by_file: dict[str, int] = {}
    for f, _ln in uncovered:
        by_file[f] = by_file.get(f, 0) + 1
    top = sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    if not top:
        return "none"

    def _fmt(f: str, n: int) -> str:
        return f"{f} ({n} {'line' if n == 1 else 'lines'})"

    return ", ".join(_fmt(f, n) for f, n in top)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def _constant_offset(
    excerpt_line_map: dict[int, str],
    file_lines: dict[int, str],
    lo: int,
    hi: int,
) -> int | None:
    """Return the shift that makes every excerpt line match the file,
    or None when no single offset explains the mismatch.

    A misnumbered excerpt (the reviewer ignored the annotated column)
    matches the post-image at a constant delta; a fabricated one matches
    at no delta. Offsets are searched in the inclusive range lo..hi.
    A single-line excerpt is too weak a signal: one line can coincide
    with any shifted position, so it must not convict as misnumbering.

    Repeated boilerplate gives more than one honest answer: the same guard
    clause two places apart matches at both deltas. The smallest one is the
    slip the reviewer actually made, so candidates are walked outward from
    zero rather than upward from the low bound.
    """
    if len(excerpt_line_map) < 2:
        return None

    def norm(s):
        return s.rstrip()

    for delta in sorted(
        # Ties go to the negative side: a quote that sits one line above
        # and one line below equally well is far more often a reviewer
        # who counted the anchor line in than one who counted it out.
        (d for d in range(lo, hi + 1) if d != 0), key=lambda d: (abs(d), d)
    ):
        matches = 0
        compared = 0
        for claimed, content in excerpt_line_map.items():
            actual = file_lines.get(claimed + delta)
            if actual is None:
                continue
            compared += 1
            if norm(content) == norm(actual):
                matches += 1
        # Every claimed line must match at this delta, and every one
        # must be comparable: a line whose shifted position falls
        # outside the post-image cannot vouch for the shift, so a
        # partial comparison convicting on the comparable subset would
        # call a fabricated tail "misnumbered". The single-line guard
        # above already ensures len >= 2, so this also keeps the
        # two-comparable-line floor.
        if compared == len(excerpt_line_map) and matches == compared:
            return delta
    return None


def _only_leading_ws_differs(quoted: str, actual: str) -> bool:
    """True when the lines match after strip() but not after rstrip().

    That is the indent-stripped quote: tokens are intact, only the
    leading spaces (or tabs) were dropped or added. A punctuation or
    identifier change fails strip() and stays a content mismatch.
    """
    if quoted.rstrip() == actual.rstrip():
        return False
    return quoted.strip() == actual.strip()


def _blank_boundary_slip(
    exc_start: int,
    exc_end: int,
    offset: int,
    file_lines: dict[int, str],
) -> bool:
    """Report whether a one-line offset is explained by a blank boundary.

    A markdown reviewer quoting a passage routinely anchors on the blank line
    that separates two paragraphs, producing coordinates one line off from
    the text it actually quoted. The blank line carries no evidence either
    way, so convicting that excerpt of misnumbering raises a CONFIRMED INFRA
    finding over nothing and zeroes the clean-round counter.

    Only a single-line slip across a blank boundary qualifies. A reviewer who
    ignored the annotated column entirely lands further away than one line, and
    stays reported as misnumbering. A boundary line absent from the post-image
    proves nothing either way, so absence alone cannot excuse the slip: one end
    must be present and blank.
    """
    if abs(offset) != 1:
        return False
    return any(
        line is not None and not line.strip()
        for line in (file_lines.get(exc_start), file_lines.get(exc_end))
    )


def validate_excerpt_evidence(
    exc: dict,
    hunk_map: dict[str, list[dict]] | None = None,
    post_image: dict[str, dict[int, str]] | None = None,
    exempt_files: list[str] | None = None,
) -> str | None:
    """Validate one excerpt's shape, line-count parity and literal anchoring.

    Shared deterministic predicate used by both producer acceptance and
    run_verify: returns an error message string when the excerpt is
    invalid, or None when it is valid. Shape and line-count checks run
    unconditionally; diff-anchoring and literal checks run only when the
    caller supplies hunk_map/post_image/exempt_files parsed from the
    frozen diff_text (never the mutable working tree).
    """
    exc_file = exc.get("file", "<unknown>")
    exc_start = exc.get("start_line", None)
    exc_end = exc.get("end_line", None)
    if not isinstance(exc_file, str) or not exc_file.strip():
        return "excerpt file must be a non-empty string"
    if (
        not isinstance(exc_start, int)
        or isinstance(exc_start, bool)
        or not isinstance(exc_end, int)
        or isinstance(exc_end, bool)
    ):
        return f"excerpt {exc_file} coordinates must be integers"
    if exc_start <= 0 or exc_end <= 0 or exc_start > exc_end:
        return (
            f"excerpt {exc_file}:{exc_start!r}-{exc_end!r} has nonpositive or unordered range"
        )
    content = exc.get("content", "")
    if isinstance(content, list):
        # Preserve the writer's all-string list join; a mixed list is
        # not evidence and must not be stringified into it.
        if not all(isinstance(ln, str) for ln in content):
            return f"excerpt {exc_file}:{exc_start}-{exc_end} content list must contain only strings"
        text = "\n".join(content)
    elif isinstance(content, str):
        text = content
    else:
        return f"excerpt {exc_file}:{exc_start}-{exc_end} content must be a string"
    if not text or not text.strip():
        return f"excerpt {exc_file}:{exc_start} has empty content"
    claimed = exc_end - exc_start + 1
    actual_lines = excerpt_lines(text)
    short_by_one = claimed == len(actual_lines) + 1
    count_error = f"excerpt {exc_file}:{exc_start}-{exc_end} declares {claimed} lines but carries {len(actual_lines)}"
    if not excerpt_line_count_matches(text, claimed):
        return count_error
    if hunk_map is None:
        return None
    exempt = exempt_files or []
    if exc_file not in hunk_map and exc_file not in exempt:
        return f"excerpt {exc_file}:{exc_start} not in diff"
    if exc_file in hunk_map and not any(
        max(exc_start, h["start"]) <= min(exc_end, h["end"])
        for h in hunk_map[exc_file]
    ):
        return (
            f"excerpt {exc_file}:{exc_start}-{exc_end} is outside every hunk; if the reviewer read it for context rather than checking it, it belongs in context_quotes"
        )
    if post_image is None or exc_file in exempt:
        # No post-image to confirm the +/-1 slack from
        # excerpt_line_count_matches. Count must be exact: a dropped
        # trailing blank and an extra pasted line are equally unverified.
        return count_error if claimed != len(actual_lines) else None
    file_lines = post_image.get(exc_file, {})
    body_start = exc_start
    # A boundary blank can excuse the body shift below or a one-line offset
    # further down, but not both: spending it twice skips the content check
    # altogether and lets an excerpt drop a real line unnoticed.
    blank_spent = False
    if short_by_one:
        # The count check let this through as a dropped blank line. The
        # post-image says which end lost it. A quote running across a
        # paragraph separator leaves it out at whichever end it falls,
        # so both bounds have to be asked; a content line at both means
        # the excerpt is genuinely thin rather than missing a separator.
        tail = file_lines.get(exc_end)
        head = file_lines.get(exc_start)
        tail_blank = tail is not None and not tail.strip()
        head_blank = head is not None and not head.strip()
        if not tail_blank and not head_blank:
            return count_error
        if head_blank and not tail_blank:
            # The separator sits at the start, so the body that was actually
            # quoted begins one line into the declared range.
            body_start = exc_start + 1
            blank_spent = True
    excerpt_line_map = {
        body_start + i: line for i, line in enumerate(actual_lines)
    }
    overlap = set(excerpt_line_map) & set(file_lines)
    if overlap:
        offset = None
        indent_ln = None
        mismatch_ln = None
        for ln in sorted(overlap):
            if excerpt_line_map[ln].rstrip() != file_lines[ln].rstrip():
                if offset is None:
                    offset = _constant_offset(
                        excerpt_line_map, file_lines, -64, 65)
                if _only_leading_ws_differs(
                    excerpt_line_map[ln], file_lines[ln]
                ):
                    if indent_ln is None:
                        indent_ln = ln
                else:
                    mismatch_ln = ln
                    break
        if mismatch_ln is not None or indent_ln is not None:
            ln = mismatch_ln if mismatch_ln is not None else indent_ln
            if offset is not None and (
                blank_spent
                or not _blank_boundary_slip(
                    exc_start, exc_end, offset, file_lines
                )
            ):
                return (
                    f"excerpt misnumbered by {offset:+d} at {exc_file}:{exc_start}-{exc_end} (claims {exc_file}:{ln}, actually {exc_file}:{ln + offset})"
                )
            if offset is not None:
                # Blank-boundary slip: the quote is anchored one line off
                # across a paragraph separator that carries no evidence.
                # The content itself checked out at the shift, so there is
                # nothing left to report.
                return None
            if mismatch_ln is not None:
                return (
                    f"excerpt content mismatch at {exc_file}:{exc_start}-{exc_end} (line {mismatch_ln})"
                )
            return (
                f"excerpt indent-stripped at {exc_file}:{exc_start}-{exc_end}"
            )
    outside = set(excerpt_line_map) - set(file_lines)
    if outside and not overlap:
        offset = _constant_offset(excerpt_line_map, file_lines, -64, 65)
        if offset is not None and (
            blank_spent
            or not _blank_boundary_slip(exc_start, exc_end, offset, file_lines)
        ):
            ln = min(outside)
            return (
                f"excerpt misnumbered by {offset:+d} at {exc_file}:{exc_start}-{exc_end} (claims {exc_file}:{ln}, actually {exc_file}:{ln + offset})"
            )
        if offset is not None:
            return None
        return (
            f"excerpt {exc_file}:{exc_start}-{exc_end} claims line {min(outside)} outside the diff post-image; it cannot be verified"
        )
    return None


def _diff_validation_context(
    diff_text: str,
) -> tuple[dict[str, dict[int, str]], dict[str, list[dict]], list[str]]:
    """Parse frozen diff text into (post_image, hunk_map, exempt_files).

    Same parsing run_verify's hardened excerpt checks use; exposing it
    lets the StateMachine validate a round's in-memory excerpts against
    the same deterministic predicate instead of re-reading receipts from
    disk (disk selection is not bound to the current run).
    """
    post_image: dict[str, dict[int, str]] = {}
    hunk_map: dict[str, list[dict]] = {}
    current_file: str | None = None
    line_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            line_no = 0
            post_image.setdefault(current_file, {})
            hunk_map.setdefault(current_file, [])
        elif raw.startswith("@@") and current_file:
            import re

            m = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if m:
                line_no = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                hunk_map[current_file].append(
                    {"start": line_no, "end": line_no + count - 1}
                )
        elif current_file and raw.startswith("+") and not raw.startswith("+++"):
            if raw.startswith("++"):  # new-file marker
                continue
            post_image[current_file][line_no] = raw[1:]
            line_no += 1
        elif current_file and raw.startswith("-") and not raw.startswith("---"):
            if raw.startswith("--"):  # deleted-file marker
                continue
            # Deleted lines shift nothing; keep line_no pinned for the
            # post-image of surviving lines.
            continue
        elif current_file and line_no > 0:
            # Context line: appears in the post-image, advances line_no.
            post_image[current_file][line_no] = raw[1:]
            line_no += 1
    # Files whose entire diff is deletions have no post-image lines but
    # are still in hunk_map; the excerpt check treats "in hunk_map" as
    # requiring an anchor, which a deletion-only file cannot satisfy.
    # They are exempt from literal checks because there is no post-image
    # to match against.
    exempt_files = [
        f for f, lines in post_image.items() if not lines
    ]
    return post_image, hunk_map, exempt_files


def is_one_line_misnumber(err: str) -> bool:
    """True when err names a constant +/-1 coordinate slip."""
    return bool(re.search(r"excerpt misnumbered by [+-]1 ", err))


def is_indent_stripped(err: str) -> bool:
    """True when err names an indent-stripped quote.

    Tokens match after strip(); only leading whitespace differs.
    Callers treat this as evidence quality, same channel as a
    one-line misnumber: UNTRUSTED, not RECEIPT_INVALID.
    """
    return "excerpt indent-stripped at " in err


def is_evidence_quality_fault(err: str) -> bool:
    """True when err is a one-line slip or an indent-stripped quote."""
    return is_one_line_misnumber(err) or is_indent_stripped(err)


def validate_excerpts_against_diff(
    diff_text: str, excerpts: list[dict]
) -> list[str]:
    """Validate excerpts against frozen diff text; return error strings.

    Empty excerpts list is not an error here (coverage/witness policy is
    enforced by the producer's coverage guard and run_verify's cycle
    checks); this validates only the evidence actually offered, using the
    same predicate run_verify applies to receipts on disk.
    """
    if not diff_text or not excerpts:
        return []
    post_image, hunk_map, exempt_files = _diff_validation_context(diff_text)
    errors: list[str] = []
    for exc in excerpts:
        err = validate_excerpt_evidence(
            exc, hunk_map, post_image, exempt_files
        )
        if err is not None and not is_evidence_quality_fault(err):
            errors.append(err)
    return errors


def run_verify(
    cwd: Path, diff_sha256: str,
    diff_files: dict[str, list[int]],
    hardened: bool = True,
    diff_text: str | None = None,
    required_cycles: int | None = None,
    cycles: list[int] | None = None,
    respect_floor: bool = True,
    reviewed_repositories: dict[str, str] | None = None,
) -> VerifyResult:
    cp = 0
    repository_manifest = None
    if reviewed_repositories is not None:
        from .receipt_scope import repository_scope
        try:
            diff_text, repository_manifest = repository_scope(reviewed_repositories)
        except ValueError as exc:
            return VerifyResult(False, str(exc), 1, cp)
        diff_files = parse_diff_files(diff_text)
    # Validated here rather than at the CLI, because this is the public
    # entry point and the CLI is only one of its callers. Zero is the
    # sharp value: required becomes 0 so the count check passes
    # vacuously, and cycles[-0:] is cycles[0:], so the slice widens to
    # every cycle instead of narrowing to none -- an invalid argument
    # that reads as the most permissive one. bool is an int subclass, so
    # False arrives here as a zero that isinstance would wave through.
    if required_cycles is not None and (
            not isinstance(required_cycles, int)
            or isinstance(required_cycles, bool)
            or required_cycles < 1):
        return VerifyResult(
            False,
            f"required_cycles must be an integer >= 1, got {required_cycles!r}",
            1, cp)
    # cycles pins the attested window to specific cycle numbers instead of
    # "the last N on disk". The StateMachine uses it to attest exactly the
    # cycles IT wrote this run, so a later run's higher cycles -- or old
    # cycles seeded on disk -- can never vouch for (or mask) the current
    # run's evidence. Duplicates and non-positive values are invalid: a
    # cycle set is a set of distinct positive round numbers.
    if cycles is not None:
        if (
            not isinstance(cycles, list)
            or len(cycles) < 1
            or any(
                not isinstance(c, int) or isinstance(c, bool) or c < 1
                for c in cycles
            )
            or len(set(cycles)) != len(cycles)
        ):
            return VerifyResult(
                False,
                f"cycles must be a list of distinct positive ints, got {cycles!r}",
                1, cp)
    # The argument raises the bar the repo set; it never lowers it. The
    # floor belongs here and not in the CLI branch that used to hold it,
    # because a caller who can pass required_cycles=1 to a repo whose
    # gate.yaml demands 3 does not become trustworthy by arriving through
    # a different door -- and every non-CLI caller (the MCP server, a
    # test, an editor plugin) comes through one.
    #
    # respect_floor=False is the StateMachine's own terminal attestation,
    # which passes the floor it ALREADY computed for its own run policy
    # (CI attests exactly one cycle per the approved design; LOCAL attests
    # its clean window). Re-raising by the repo floor there would make CI
    # impossible on a repo configured for 3 cycles -- CI is one round by
    # construction and must attest what it ran, not what LOCAL would.
    if respect_floor:
        try:
            floor = read_required_cycles(cwd)
        except UnreadableGateError as exc:
            return VerifyResult(False, f"unreadable gate: {exc}", 1, cp)
        required_cycles = (
            floor if required_cycles is None else max(required_cycles, floor)
        )
    elif required_cycles is None:
        try:
            required_cycles = read_required_cycles(cwd)
        except UnreadableGateError as exc:
            return VerifyResult(False, f"unreadable gate: {exc}", 1, cp)
    required = required_cycles * PASSES_PER_CYCLE
    try:
        receipts = _load_receipts(cwd / ".code-forge" / "receipts")
    except CorruptedReceiptError as exc:
        return VerifyResult(False, f"corrupt receipt: {exc}", 1, cp)

    # 1. completeness: last N consecutive cycles
    # findings_count. Reviews that take more rounds write later cycle
    # numbers; the last N consecutive clean cycles are what matters,
    # regardless of what those numbers are.
    if len(receipts) < required:
        msg = f"missing receipts: {len(receipts)}/{required}"
        if len(receipts) == 0:
            msg += (
                " -- no review receipts found. Run 'code-forge review' "
                "on your staged changes first"
            )
        return VerifyResult(False, msg, 1, cp)
    # Only the attested window may vouch. Compute last_n first,
    # then scope every structural check to those cycles.
    cycles = sorted(set(cycles)) if cycles is not None else None
    all_cycle_vals = sorted({r["cycle"] for r in receipts})
    if cycles is not None:
        # Pinned window: the caller names exactly which cycles vouch.
        # The count check above already requires enough receipts on disk;
        # the per-cycle checks below then apply to this set only.
        last_n = cycles
        if len(last_n) < required_cycles:
            return VerifyResult(
                False,
                f"attested window has {len(last_n)} cycle(s); repository verifier floor demands {required_cycles}: {last_n}",
                1, cp)
    else:
        if len(all_cycle_vals) < required_cycles:
            return VerifyResult(
                False, f"fewer than {required_cycles} cycles: {len(all_cycle_vals)}",
                1, cp)
        last_n = all_cycle_vals[-required_cycles:]
    for i in range(len(last_n) - 1):
        if last_n[i + 1] - last_n[i] != 1:
            return VerifyResult(
                False,
                f"last {required_cycles} cycles not consecutive: {last_n}",
                1, cp)
    attested = [r for r in receipts if r["cycle"] in last_n]
    if any(r.get("reviewed_repositories") != repository_manifest for r in attested):
        return VerifyResult(False, "INFRA: reviewed repository/source identity mismatch", 1, cp)

    seen_keys = set()
    for r in attested:
        key = (r["cycle"], r["pass"])
        if key in seen_keys:
            return VerifyResult(False, f"duplicate receipt c{key[0]}p{key[1]}", 1, cp)
        seen_keys.add(key)
        if r["findings_count"] != len(r["findings"]):
            return VerifyResult(
                False, f"findings_count mismatch c{key[0]}p{key[1]}", 1, cp)
        if repository_manifest is not None and any(
            not isinstance(f.get("file"), str) or f["file"] not in diff_files
            for f in r["findings"]
        ):
            return VerifyResult(
                False, "INFRA: finding repository/source identity mismatch", 1, cp)
    for c in last_n:
        passes = {p for (cyc, p) in seen_keys if cyc == c}
        # Exactly the three protocol passes, not merely at least them. Asking
        # only that 1-3 be present lets a cycle carry a pass 4 or 5, which the
        # protocol never produces -- three skills run per cycle -- so an extra
        # one is a receipt nobody wrote for a pass nobody ran.
        missing = {1, 2, 3} - passes
        if missing:
            return VerifyResult(
                False, f"missing cycle {c}/pass {min(missing)}", 1, cp)
        extra = passes - {1, 2, 3}
        if extra:
            return VerifyResult(
                False,
                f"cycle {c} has pass {min(extra)}, outside the three review passes",
                1, cp)
    cp += 1

    # 2. hash
    for r in attested:
        if r.get("diff_sha256") != diff_sha256:
            return VerifyResult(False, f"diff hash mismatch c{r['cycle']}p{r['pass']}", 2, cp)
    cp += 1

    # 3. anchors: file must be in diff
    for r in attested:
        for a in r["anchors"]:
            afile = a.get("file", "")
            if afile not in diff_files:
                return VerifyResult(False, f"anchor file {afile} not in diff", 3, cp)
    cp += 1

    # 4. timestamps: non-decreasing in (cycle, pass) order. Passes in a round
    #    share the round's write time, so tripping this means the set was
    #    stitched from separate runs or the clock went backwards.
    ts = [r.get("timestamp", "") for r in attested]
    if ts != sorted(ts):
        return VerifyResult(False, "timestamps not monotonic", 4, cp)
    cp += 1

    if hardened and diff_text is not None:
        # 5. per-hunk excerpt witness + content/coverage gate. Returns FAIL on an
        #    unwitnessed or fabricated excerpt. Complements (does not replace) the
        #    R1/R2/R3 dynamic verification layer.
        hunk_map, exempt_files = parse_diff_hunks(diff_text)
        post_image = _extract_post_image_lines(diff_text)

        if diff_text.strip() and not hunk_map and not exempt_files:
            return VerifyResult(False, "diff parse failed -- cannot verify excerpts", 5, cp)

        # Only the attested window may vouch. Excerpts from a cycle
        # outside last_n are evidence of what this repo used to demand,
        # not of what this gate is attesting: with required_cycles=1 an
        # older receipt would let STEP A/B/C pass on a diff the attested
        # cycle never reviewed. The count and matrix checks already
        # scope to last_n; the excerpt checks must too.
        all_excerpts = []
        for r in receipts:
            if r.get("cycle") in last_n:
                all_excerpts.extend(r.get("code_excerpts", []))

        # STEP 0: excerpt shape and line-count parity, via the shared
        # helper (before any field access in STEP A). Underlength now
        # fails instead of receiving partial credit.
        for exc in all_excerpts:
            err = validate_excerpt_evidence(exc)
            if err is not None:
                return VerifyResult(False, err, 5, cp)

        # STEP A: per-hunk witness check
        for file, hunks in hunk_map.items():
            for hunk in hunks:
                if hunk["is_deletion_only"]:
                    continue
                witnessed = any(
                    exc["file"] == file
                    and max(exc["start_line"], hunk["start"]) <= min(exc["end_line"], hunk["end"])
                    for exc in all_excerpts
                )
                if not witnessed:
                    return VerifyResult(
                        False,
                        f"unwitnessed hunk {file}:{hunk['start']}-{hunk['end']}",
                        5, cp,
                    )

        # STEP B: excerpt-to-hunk anchoring plus literal post-image
        # match, via the same shared helper the producer uses. Every
        # claimed source line must be in the frozen diff post-image and
        # match under the existing trailing-whitespace rule only.
        #
        # An excerpt names lines the reviewer says it checked, so every one has
        # to land somewhere this can check it -- the diff. Code read for
        # orientation but outside the diff is real and worth recording, and it
        # belongs in context_quotes, which claims nothing and is never read
        # here. Letting it into code_excerpts instead would mean accepting a
        # line nobody can confirm next to lines that were confirmed, with
        # nothing in the receipt telling the two apart. Checking it against the
        # working tree is not the way out: the diff is fixed at verify time and
        # the tree is not, so the tree can change between the reviewer reading
        # it and this running.
        for exc in all_excerpts:
            err = validate_excerpt_evidence(exc, hunk_map, post_image, exempt_files)
            if err is not None and not is_evidence_quality_fault(err):
                return VerifyResult(False, err, 5, cp)

        # STEP C: content verification against diff post-image
        # The diff is immutable at verify time -- no TOCTOU with working tree.
        # Only lines overlapping between excerpt and diff are compared (GM-B1).
        # Known limitation: STEP C verifies that covered lines are faithful to the
        # post-image but cannot distinguish "covers only context lines" from "covers
        # actual changed lines." A reviewer can pass STEP C by citing only context
        # lines around the change. The 60% coverage floor (check 6) mitigates this.
        for exc in all_excerpts:
            actual_lines = exc.get("content", "").splitlines()
            excerpt_line_map = {}
            start = exc["start_line"]
            for i, line in enumerate(actual_lines):
                excerpt_line_map[start + i] = line

            file_lines = post_image.get(exc["file"], {})
            overlap_lines = set(excerpt_line_map.keys()) & set(file_lines.keys())

            # Exempt files (binary/rename/mode-only) have no hunks and
            # therefore no post-image lines; every claimed line would
            # read as outside. They are checked at STEP B and skipped
            # here by design.
            if exc["file"] in exempt_files:
                continue

            one_line_slip = False
            indent_only = False
            if overlap_lines:
                def normalize(s):
                    return s.rstrip()
                for ln in sorted(overlap_lines):
                    if normalize(excerpt_line_map[ln]) != normalize(file_lines[ln]):
                        if _only_leading_ws_differs(
                            excerpt_line_map[ln], file_lines[ln]
                        ):
                            indent_only = True
                            continue
                        # Distinguish a misnumbered excerpt from a fabricated
                        # one. A reviewer that ignored the annotated line
                        # numbers produces content that matches the file at a
                        # constant offset; a fabricated excerpt matches at no
                        # offset at all. Report the offset so the diagnosis
                        # does not point at the wrong line.
                        offset = _constant_offset(
                            excerpt_line_map, file_lines, -64, 65,
                        )
                        if offset is not None:
                            if abs(offset) == 1:
                                # Content matches one line over: evidence
                                # quality, not a dead backend. Skip the
                                # outside-line check for this excerpt.
                                one_line_slip = True
                                break
                            return VerifyResult(
                                False,
                                f"excerpt misnumbered by {offset:+d} at {exc['file']}:{exc['start_line']}-{exc['end_line']} (claims {exc['file']}:{ln}, actually {exc['file']}:{ln + offset})",
                                5, cp,
                            )
                        return VerifyResult(
                            False,
                            f"excerpt content mismatch at {exc['file']}:{exc['start_line']}-{exc['end_line']} (line {ln})",
                            5, cp,
                        )

            if indent_only:
                one_line_slip = True

            # Every claimed line must land in the post-image. A line
            # outside it is content nobody can check -- the tail of a
            # genuine excerpt can carry invented lines and the receipt
            # would read as "the reviewer verified these" while they were
            # never compared against anything. This runs after the
            # misnumber check so a shifted excerpt reports its offset
            # rather than a bare outside-the-diff line.
            if one_line_slip:
                cp += 1
                continue
            outside = set(excerpt_line_map.keys()) - set(file_lines.keys())
            if outside and not overlap_lines:
                return VerifyResult(
                    False,
                    f"excerpt {exc['file']}:{exc['start_line']}-{exc['end_line']} claims line "
                    f"{min(outside)} outside the diff post-image; it cannot be verified",
                    5, cp,
                )
            # Hunk-halo: overlapping excerpts that also quote lines
            # outside the @@ span are skipped. Invented tails with
            # no overlap already failed above. Do not read cwd.
        # 6. excerpt-derived coverage >= 60%
        # The floor deliberately counts test lines: tests do not test
        # themselves, so a test-heavy diff is exactly where a reviewer
        # must show they read the test body. This is review-evidence
        # coverage, not test-execution coverage (charter item 6,
        # 43.1-DECISIONS-20260815.md).
        # covered_line_ranges is self-reported, not measured -- audit-only. Ignored here.
        all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
        if all_diff:
            for c in last_n:
                cov = _cycle_excerpt_covered(receipts, c) & all_diff
                if len(cov) / len(all_diff) < 0.6:
                    # The prefix before ';' is the stable format a
                    # consumer may match; the suffix is human guidance.
                    return VerifyResult(
                        False,
                        f"coverage {100 * len(cov) / len(all_diff):.0f}% < 60% cycle {c}; largest uncovered: {_coverage_failure_detail(cov, all_diff)}",
                        6, cp)
        cp += 1

        # 7. Jaccard overlap > 0.8 = rubber stamp.
        # NOTE: identical excerpts across cycles will cause Jaccard > 0.8.
        # This is CORRECT -- it detects rubber-stamping.
        # Known limitation: when all cycles have empty findings (findings=[]),
        # the skip condition below causes Jaccard to never trigger, so
        # identical-excerpt clean reviews always pass (intentional design).
        cycle_findings = {}
        for r in receipts:
            cyc = r.get("cycle", 0)
            if cyc not in cycle_findings:
                cycle_findings[cyc] = []
            cycle_findings[cyc].extend(r.get("findings", []))

        for a, b in combinations(last_n, 2):
            if not cycle_findings.get(a) and not cycle_findings.get(b):
                continue
            cov_a = _cycle_excerpt_covered(receipts, a)
            cov_b = _cycle_excerpt_covered(receipts, b)
            if not cov_a and not cov_b:
                return VerifyResult(
                    False,
                    f"no excerpt coverage in cycles {a} and {b} (findings present but excerpts empty)",
                    7, cp,
                )
            j = _jaccard(cov_a, cov_b)
            if j > 0.8:
                return VerifyResult(False, f"Jaccard overlap {j:.2f} > 0.8 c{a}-c{b}", 7, cp)
        cp += 1

    else:
        if hardened and diff_text is None:
            logger.info("hardened=True but diff_text=None, using legacy checks")

        # 5. legacy excerpt verification (working tree). Only the attested
        #    window may vouch here too: an older cycle's excerpts are not
        #    evidence for what this gate attests, same rule as the hardened
        #    path, or a stale receipt could fail -- or pass -- the check for
        #    a diff the attested cycle never reviewed.
        for r in attested:
            for exc in r.get("code_excerpts", []):
                fp = cwd / exc["file"]
                if not fp.exists():
                    return VerifyResult(
                        False,
                        f"excerpt file missing: {exc['file']} (c{r['cycle']}p{r['pass']})",
                        5, cp)
                try:
                    lines = fp.read_text(encoding="utf-8").splitlines()
                    actual = "\n".join(lines[exc["start_line"] - 1:exc["end_line"]]) + "\n"
                    claimed = exc["content"]
                    if not claimed.endswith("\n"):
                        claimed += "\n"
                    if actual != claimed:
                        return VerifyResult(
                            False,
                            f"excerpt mismatch {exc['file']}:{exc['start_line']}-{exc['end_line']} c{r['cycle']}p{r['pass']}",
                            5, cp)
                except (IndexError, OSError) as e:
                    logging.warning("check 5 legacy: %s", e)
                    return VerifyResult(
                        False,
                        f"excerpt line range error {exc['file']}:{exc['start_line']}-{exc['end_line']}",
                        5, cp)
        cp += 1

        # 6. legacy coverage >= 60% (self-reported covered_line_ranges)
        all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
        if all_diff:
            for c in last_n:
                cov = _cycle_covered(receipts, c) & all_diff
                if len(cov) / len(all_diff) < 0.6:
                    # Same format contract as the excerpt-derived check 6.
                    return VerifyResult(
                        False,
                        f"coverage {100 * len(cov) / len(all_diff):.0f}% < 60% cycle {c}; largest uncovered: {_coverage_failure_detail(cov, all_diff)}",
                        6, cp)
        cp += 1

        # 7. legacy Jaccard
        cycle_findings = {}
        for r in receipts:
            cyc = r.get("cycle", 0)
            if cyc not in cycle_findings:
                cycle_findings[cyc] = []
            cycle_findings[cyc].extend(r.get("findings", []))

        for a, b in combinations(last_n, 2):
            if not cycle_findings.get(a) and not cycle_findings.get(b):
                continue
            j = _jaccard(_cycle_covered(receipts, a), _cycle_covered(receipts, b))
            if j > 0.8:
                return VerifyResult(False, f"Jaccard overlap {j:.2f} > 0.8 c{a}-c{b}", 7, cp)
        cp += 1

    # 8. every pass in the attested window has to have actually run.
    # derive_pass_outcomes already works this out from the INFRA findings and
    # receipt.py stores it per receipt as pass_status; until now nothing read
    # it. A pass that timed out or errored still writes a receipt, and checks
    # 1-7 can all pass on that receipt: it has a matching hash, a monotonic
    # timestamp, no anchors to contradict and no excerpts to disprove. The
    # coverage floor was the only thing in the way, and it unions across the
    # passes of a cycle, so two healthy passes on a large enough diff carry a
    # third that never happened.
    #
    # Absence is not failure. pass_status is not in SKILL.md, so a reviewer
    # hand-writing a receipt from the documented shape leaves it out, and
    # every receipt written before the field existed lacks it too. Refusing on
    # a missing field would reject good receipts -- which is the exact failure
    # the schema comment at the top of this file was written about. Measured
    # before writing this: all 204 receipts on disk carry the field
    # (189 completed, 12 error, 3 timeout), so the signal is real; the
    # tolerance is for the writers that are not receipt.py.
    for r in receipts:
        if r.get("cycle") not in last_n:
            continue
        status = r.get("pass_status")
        if status is not None and status != "completed":
            return VerifyResult(
                False,
                f"pass did not complete: c{r['cycle']}p{r['pass']} status={status} -- that pass contributed no review, so the cycle cannot attest",
                8, cp,
            )
    cp += 1

    return VerifyResult(True, "all 8 checks passed", 8, 8)



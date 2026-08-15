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
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from .diff import _extract_post_image_lines, parse_diff_hunks
from .errors import CorruptedReceiptError, UnreadableGateError

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
                "%s is a dangling symlink; cannot read the policy" % path
            ) from None
        if path.parent.is_symlink() and not path.parent.exists():
            raise UnreadableGateError(
                "%s is inside a dangling symlink; cannot read the policy"
                % path
            ) from None
        return DEFAULT_REQUIRED_CYCLES
    except Exception as exc:
        # FileNotFoundError is handled above, so everything that lands
        # here is a policy we cannot read. The import sits outside the
        # try so a missing PyYAML surfaces as an environment error, not
        # as this gate blaming the file.
        raise UnreadableGateError(
            "%s exists but could not be parsed: %s" % (path, exc)
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
            "%s verify section is present but null; "
            "a written-down policy must be a mapping or absent"
            % path
        )
    if not isinstance(section, dict):
        raise UnreadableGateError(
            "%s verify section is %r; must be a mapping" % (path, section)
        )
    unknown = set(section) - {"required_cycles"}
    if unknown:
        raise UnreadableGateError(
            "%s verify section has unknown key(s): %s; a misspelled knob "
            "would read as absent and silently open the gate"
            % (path, ", ".join(sorted(str(k) for k in unknown)))
        )
    if "required_cycles" not in section:
        return DEFAULT_REQUIRED_CYCLES
    n = section["required_cycles"]
    if n is None:
        raise UnreadableGateError(
            "%s verify.required_cycles must be an integer; got null/blank"
            % path
        )
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise UnreadableGateError(
            "%s verify.required_cycles is %r; must be a positive int"
            % (path, n)
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
                "%s: %s must be %s" % (name, field, _TYPE_LABEL[str]))
    for field in _INT_FIELDS:
        if not _is_type(obj.get(field), int):
            raise CorruptedReceiptError(
                "%s: %s must be %s" % (name, field, _TYPE_LABEL[int]))
    for field in _LIST_OF_DICT_FIELDS:
        v = obj.get(field)
        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
            raise CorruptedReceiptError(
                "%s: %s must be a list of objects" % (name, field))
    for field in _OPTIONAL_LIST_OF_DICT_FIELDS:
        if field not in obj:
            continue
        v = obj[field]
        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
            raise CorruptedReceiptError(
                "%s: %s must be a list of objects" % (name, field))
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
                        "%s: %s.%s must be %s" % (
                            name, list_field, subfield, _TYPE_LABEL[subtype]))
    # Excerpt line ranges must be ordered. An inverted range silently credits
    # zero lines, which looks identical to an honest excerpt that sits outside
    # the diff -- two different problems, one symptom, no way to tell apart.
    for exc in obj.get("code_excerpts", []):
        s = exc.get("start_line")
        e = exc.get("end_line")
        if isinstance(s, int) and isinstance(e, int) and s > e:
            raise CorruptedReceiptError(
                "%s: code_excerpts start_line %d > end_line %d" % (name, s, e))


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
            raise CorruptedReceiptError("%s: %s" % (f.name, exc)) from exc
        if not isinstance(obj, dict):
            # Every check downstream calls .get() on these. A bare array or
            # number parses cleanly and then crashes the caller with an
            # AttributeError, so the annotation above is enforced here.
            raise CorruptedReceiptError(
                "%s: expected a JSON object, got %s" % (f.name, type(obj).__name__)
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
    """
    if len(excerpt_line_map) < 2:
        return None

    def norm(s):
        return s.rstrip()

    for delta in range(lo, hi + 1):
        if delta == 0:
            continue
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


def run_verify(
    cwd: Path, diff_sha256: str,
    diff_files: dict[str, list[int]],
    hardened: bool = True,
    diff_text: str | None = None,
    required_cycles: int | None = None,
) -> VerifyResult:
    cp = 0
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
            "required_cycles must be an integer >= 1, got %r"
            % (required_cycles,),
            1, cp)
    # The argument raises the bar the repo set; it never lowers it. The
    # floor belongs here and not in the CLI branch that used to hold it,
    # because a caller who can pass required_cycles=1 to a repo whose
    # gate.yaml demands 3 does not become trustworthy by arriving through
    # a different door -- and every non-CLI caller (the MCP server, a
    # test, an editor plugin) comes through one.
    try:
        floor = read_required_cycles(cwd)
    except UnreadableGateError as exc:
        return VerifyResult(False, "unreadable gate: %s" % exc, 1, cp)
    required_cycles = (
        floor if required_cycles is None else max(required_cycles, floor)
    )
    required = required_cycles * PASSES_PER_CYCLE
    try:
        receipts = _load_receipts(cwd / ".code-forge" / "receipts")
    except CorruptedReceiptError as exc:
        return VerifyResult(False, "corrupt receipt: %s" % exc, 1, cp)

    # 1. completeness: the last N consecutive cycles x passes 1-3, and
    # findings_count. Reviews that take more rounds write later cycle
    # numbers; the last N consecutive clean cycles are what matters,
    # regardless of what those numbers are.
    if len(receipts) < required:
        msg = "missing receipts: %d/%d" % (len(receipts), required)
        if len(receipts) == 0:
            msg += (
                " -- no review receipts found. Run 'code-forge review' "
                "on your staged changes first"
            )
        return VerifyResult(False, msg, 1, cp)
    # Only the attested window may vouch. Compute last_n first,
    # then scope every structural check to those cycles.
    cycles = sorted({r["cycle"] for r in receipts})
    if len(cycles) < required_cycles:
        return VerifyResult(
            False, "fewer than %d cycles: %d" % (required_cycles, len(cycles)),
            1, cp)
    last_n = cycles[-required_cycles:]
    for i in range(len(last_n) - 1):
        if last_n[i + 1] - last_n[i] != 1:
            return VerifyResult(
                False,
                "last %d cycles not consecutive: %s" % (required_cycles,
                                                        last_n),
                1, cp)
    attested = [r for r in receipts if r["cycle"] in last_n]

    seen_keys = set()
    for r in attested:
        key = (r["cycle"], r["pass"])
        if key in seen_keys:
            return VerifyResult(False, "duplicate receipt c%dp%d" % key, 1, cp)
        seen_keys.add(key)
        if r["findings_count"] != len(r["findings"]):
            return VerifyResult(
                False, "findings_count mismatch c%dp%d" % key, 1, cp)
    for c in last_n:
        passes = {p for (cyc, p) in seen_keys if cyc == c}
        # Exactly the three protocol passes, not merely at least them. Asking
        # only that 1-3 be present lets a cycle carry a pass 4 or 5, which the
        # protocol never produces -- three skills run per cycle -- so an extra
        # one is a receipt nobody wrote for a pass nobody ran.
        missing = {1, 2, 3} - passes
        if missing:
            return VerifyResult(
                False, "missing cycle %d/pass %d" % (c, min(missing)), 1, cp)
        extra = passes - {1, 2, 3}
        if extra:
            return VerifyResult(
                False,
                "cycle %d has pass %d, outside the three review passes" % (
                    c, min(extra)),
                1, cp)
    cp += 1

    # 2. hash
    for r in attested:
        if r.get("diff_sha256") != diff_sha256:
            return VerifyResult(False, "diff hash mismatch c%dp%d" % (r["cycle"], r["pass"]), 2, cp)
    cp += 1

    # 3. anchors: file must be in diff
    for r in attested:
        for a in r["anchors"]:
            afile = a.get("file", "")
            if afile not in diff_files:
                return VerifyResult(False, "anchor file %s not in diff" % afile, 3, cp)
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

        # STEP 0: excerpt field validation (before any field access)
        for exc in all_excerpts:
            exc_file = exc.get("file", "<unknown>")
            exc_start = exc.get("start_line", None)
            exc_end = exc.get("end_line", None)
            if (
                exc_file == "<unknown>"
                or not isinstance(exc_start, int)
                or not isinstance(exc_end, int)
                or not isinstance(exc.get("content"), str)
            ):
                return VerifyResult(False, "excerpt missing required fields", 5, cp)

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
                        "unwitnessed hunk %s:%d-%d" % (file, hunk["start"], hunk["end"]),
                        5, cp,
                    )

        # STEP B: excerpt-to-hunk anchoring
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
            content = exc.get("content", "")
            if not content or not content.strip():
                return VerifyResult(
                    False,
                    "excerpt %s:%d has empty content" % (exc["file"], exc["start_line"]),
                    5, cp,
                )
            # The declared range must carry exactly the lines the
            # excerpt claims to have checked. Content beyond the range
            # maps to no line number, so STEP C would never compare it
            # against the post-image -- a fabricated tail rides along
            # unchecked.
            claimed = exc["end_line"] - exc["start_line"] + 1
            if claimed > 0 and len(content.splitlines()) > claimed:
                return VerifyResult(
                    False,
                    "excerpt %s:%d-%d declares %d lines but carries %d" % (
                        exc["file"], exc["start_line"], exc["end_line"],
                        claimed, len(content.splitlines())),
                    5, cp,
                )
            if exc["file"] not in hunk_map and exc["file"] not in exempt_files:
                return VerifyResult(
                    False,
                    "excerpt %s:%d not in diff" % (exc["file"], exc["start_line"]),
                    5, cp,
                )
            # Exempt files (binary/rename/mode-change) pass without overlap check --
            # they have no hunks in hunk_map, so hunk anchoring cannot be verified.
            # This is intentional: exempt files produce no coverage obligation.
            if exc["file"] in hunk_map and not any(
                max(exc["start_line"], h["start"]) <= min(exc["end_line"], h["end"])
                for h in hunk_map[exc["file"]]
            ):
                return VerifyResult(
                    False,
                    "excerpt %s:%d-%d is outside every hunk; if the reviewer "
                    "read it for context rather than checking it, it belongs "
                    "in context_quotes" % (
                        exc["file"], exc["start_line"], exc["end_line"]),
                    5, cp,
                )

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
            for i, ln in enumerate(range(exc["start_line"], exc["end_line"] + 1)):
                if i < len(actual_lines):
                    excerpt_line_map[ln] = actual_lines[i]

            file_lines = post_image.get(exc["file"], {})
            overlap_lines = set(excerpt_line_map.keys()) & set(file_lines.keys())

            # Exempt files (binary/rename/mode-only) have no hunks and
            # therefore no post-image lines; every claimed line would
            # read as outside. They are checked at STEP B and skipped
            # here by design.
            if exc["file"] in exempt_files:
                continue

            if overlap_lines:
                def normalize(s):
                    return s.rstrip()
                for ln in sorted(overlap_lines):
                    if normalize(excerpt_line_map[ln]) != normalize(file_lines[ln]):
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
                            return VerifyResult(
                                False,
                                "excerpt misnumbered by %+d at %s:%d-%d "
                                "(claims %s:%d, actually %s:%d)" % (
                                    offset, exc["file"],
                                    exc["start_line"], exc["end_line"],
                                    exc["file"], ln,
                                    exc["file"], ln + offset),
                                5, cp,
                            )
                        return VerifyResult(
                            False,
                            "excerpt content mismatch at %s:%d-%d (line %d)" % (
                                exc["file"], exc["start_line"],
                                exc["end_line"], ln),
                            5, cp,
                        )

            # Every claimed line must land in the post-image. A line
            # outside it is content nobody can check -- the tail of a
            # genuine excerpt can carry invented lines and the receipt
            # would read as "the reviewer verified these" while they were
            # never compared against anything. This runs after the
            # misnumber check so a shifted excerpt reports its offset
            # rather than a bare outside-the-diff line.
            outside = set(excerpt_line_map.keys()) - set(file_lines.keys())
            if outside:
                return VerifyResult(
                    False,
                    "excerpt %s:%d-%d claims line %d outside the diff "
                    "post-image; it cannot be verified" % (
                        exc["file"], exc["start_line"], exc["end_line"],
                        min(outside)),
                    5, cp,
                )
        cp += 1

        # 6. excerpt-derived coverage >= 60%
        # covered_line_ranges is self-reported, not measured -- audit-only. Ignored here.
        all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
        if all_diff:
            for c in last_n:
                cov = _cycle_excerpt_covered(receipts, c) & all_diff
                if len(cov) / len(all_diff) < 0.6:
                    return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
                        100 * len(cov) / len(all_diff), c), 6, cp)
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
                    "no excerpt coverage in cycles %d and %d (findings present but excerpts empty)" % (a, b),
                    7, cp,
                )
            j = _jaccard(cov_a, cov_b)
            if j > 0.8:
                return VerifyResult(False, "Jaccard overlap %.2f > 0.8 c%d-c%d" % (j, a, b), 7, cp)
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
                        "excerpt file missing: %s (c%dp%d)" % (
                            exc["file"], r["cycle"], r["pass"]),
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
                            "excerpt mismatch %s:%d-%d c%dp%d" % (
                                exc["file"], exc["start_line"], exc["end_line"],
                                r["cycle"], r["pass"]),
                            5, cp)
                except (IndexError, OSError) as e:
                    logging.warning("check 5 legacy: %s", e)
                    return VerifyResult(
                        False,
                        "excerpt line range error %s:%d-%d" % (
                            exc["file"], exc["start_line"], exc["end_line"]),
                        5, cp)
        cp += 1

        # 6. legacy coverage >= 60% (self-reported covered_line_ranges)
        all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
        if all_diff:
            for c in last_n:
                cov = _cycle_covered(receipts, c) & all_diff
                if len(cov) / len(all_diff) < 0.6:
                    return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
                        100 * len(cov) / len(all_diff), c), 6, cp)
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
                return VerifyResult(False, "Jaccard overlap %.2f > 0.8 c%d-c%d" % (j, a, b), 7, cp)
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
                "pass did not complete: c%dp%d status=%s -- that pass "
                "contributed no review, so the cycle cannot attest"
                % (r["cycle"], r["pass"], status),
                8, cp,
            )
    cp += 1

    return VerifyResult(True, "all 8 checks passed", 8, 8)



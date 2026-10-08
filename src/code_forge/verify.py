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

import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from pathlib import Path

from .diff import parse_diff_hunks, path_from_plus_header
from .disposition import Disposition
from .errors import CorruptedReceiptError, UnreadableGateError
from .reviewer_json import (
    _requires_l1_excerpts,
    excerpt_line_count_matches,
    excerpt_lines,
    read_source_lines,
)

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
            raise UnreadableGateError(f"{path} is a dangling symlink; cannot read the policy") from None
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
        raise UnreadableGateError(f"{path} exists but could not be parsed: {exc}") from exc
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
        raise UnreadableGateError(f"{path} verify section is {section!r}; must be a mapping")
    unknown = set(section) - {"required_cycles"}
    if unknown:
        raise UnreadableGateError(
            f"{path} verify section has unknown key(s): {', '.join(sorted((str(k) for k in unknown)))}; a misspelled knob would read as absent and silently open the gate"
        )
    if "required_cycles" not in section:
        return DEFAULT_REQUIRED_CYCLES
    n = section["required_cycles"]
    if n is None:
        raise UnreadableGateError(f"{path} verify.required_cycles must be an integer; got null/blank")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise UnreadableGateError(f"{path} verify.required_cycles is {n!r}; must be a positive int")
    return n


class VerifyFailureKind(str, Enum):
    INCOMPLETE_PASS = "incomplete-pass"


@dataclass
class VerifyResult:
    passed: bool
    reason: str
    checks_run: int = 0
    checks_passed: int = 0
    failure_kind: VerifyFailureKind | None = None
    incomplete_passes: tuple[int, ...] = ()
    completion_statuses: tuple[tuple[int, int, str | None], ...] = ()
    unresolved_findings: tuple[tuple[int, int, str, str], ...] = ()


def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
    """Parse git diff text into {file: [changed line numbers]}."""
    import re

    diff_files: dict[str, list[int]] = {}
    current_file = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            in_hunk = False
            continue
        if not in_hunk:
            plus = path_from_plus_header(line)
            if plus is not None:
                current_file = plus
                continue
        if line.startswith("@@") and current_file:
            in_hunk = True
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or "1")
                if current_file not in diff_files:
                    diff_files[current_file] = []
                diff_files[current_file].extend(range(start, start + count))
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
            raise CorruptedReceiptError(f"{name}: {field} must be {_TYPE_LABEL[str]}")
    for field in _INT_FIELDS:
        if not _is_type(obj.get(field), int):
            raise CorruptedReceiptError(f"{name}: {field} must be {_TYPE_LABEL[int]}")
    # Optional audit data; it does not contribute review evidence.
    if "excerpt_validation_errors" in obj:
        errors = obj["excerpt_validation_errors"]
        if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
            raise CorruptedReceiptError(f"{name}: excerpt_validation_errors must be a list of strings")
    for field in _LIST_OF_DICT_FIELDS:
        v = obj.get(field)
        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
            raise CorruptedReceiptError(f"{name}: {field} must be a list of objects")
    for field in _OPTIONAL_LIST_OF_DICT_FIELDS:
        if field not in obj:
            continue
        v = obj[field]
        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
            raise CorruptedReceiptError(f"{name}: {field} must be a list of objects")
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
                        f"{name}: {list_field}.{subfield} must be {_TYPE_LABEL[subtype]}"
                    )
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
            raise CorruptedReceiptError(f"{name}: code_excerpts start_line {s} > end_line {e}")
        if s <= 0 or e <= 0:
            raise CorruptedReceiptError(
                f"{name}: code_excerpts start_line and end_line must be positive, got {s!r} and {e!r}"
            )


_ReceiptRecord = tuple[Path, bytes, dict]


def _load_receipt_records(rd: Path) -> tuple[_ReceiptRecord, ...]:
    """Load every receipt-*.json in rd.

    Raises CorruptedReceiptError naming the file when one cannot be read,
    cannot be parsed, does not hold a JSON object, or does not match the
    receipt schema (see _validate_receipt_schema). Unreadable receipts
    are not skipped: the count and the cycle/pass matrix are themselves
    checks, so dropping a file would report a corrupt receipt as a missing
    one and hide the real cause.
    """
    if not rd.exists():
        return ()
    receipts = []
    for f in sorted(rd.glob("receipt-*.json")):
        try:
            raw = f.read_bytes()
            obj = json.loads(raw.decode("utf-8"))
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
            raise CorruptedReceiptError(f"{f.name}: expected a JSON object, got {type(obj).__name__}")
        _validate_receipt_schema(obj, f.name)
        receipts.append((f, raw, obj))
    # Order by the numbers inside the receipts, not by their filenames. The
    # glob sorts as text, so once a review passes nine cycles "receipt-c10p1"
    # sorts ahead of "receipt-c9p1" and the monotonic-timestamp check reads a
    # correctly written set as out of order. The schema check above has
    # already proved cycle and pass are integers.
    receipts.sort(key=lambda r: (r[2]["cycle"], r[2]["pass"]))
    return tuple(receipts)


def _load_receipts(rd: Path) -> list[dict]:
    """Compatible parsed view; raw proof always uses the record loader."""
    return [record[2] for record in _load_receipt_records(rd)]


def _capture_earned_cycle(
    cwd: Path,
    diff_sha256: str,
    diff_files: dict[str, list[int]],
    *,
    cycle: int,
    hardened: bool = True,
    diff_text: str | None = None,
    reviewed_repositories: dict[str, str] | None = None,
    receipt_records: tuple[_ReceiptRecord, ...] | None = None,
) -> tuple[dict | None, VerifyResult]:
    """Earn a triplet only from explicitly completed, actually verified bytes."""
    if type(cycle) is not int or cycle < 1:
        return None, VerifyResult(False, "earned cycle must be a positive int", 1, 0)
    try:
        if receipt_records is None:
            receipt_records = _load_receipt_records(cwd / ".code-forge" / "receipts")
    except CorruptedReceiptError as exc:
        return None, VerifyResult(False, f"corrupt receipt: {exc}", 1, 0)
    selected = [record for record in receipt_records if record[2]["cycle"] == cycle]
    if len(selected) != 3 or {record[2]["pass"] for record in selected} != {1, 2, 3}:
        return None, VerifyResult(False, f"earned cycle {cycle} requires exactly three receipts", 1, 0)
    result = _run_verify_impl(
        cwd,
        diff_sha256,
        diff_files,
        hardened=hardened,
        diff_text=diff_text,
        required_cycles=1,
        cycles=[cycle],
        respect_floor=False,
        reviewed_repositories=reviewed_repositories,
        require_convergence=True,
        receipt_records=receipt_records,
    )
    if not result.passed:
        if result.failure_kind is VerifyFailureKind.INCOMPLETE_PASS:
            result.reason = f"earned cycle {cycle} requires explicit completed status"
        return None, result
    if any(record[2].get("pass_status") != "completed" for record in selected):
        return None, VerifyResult(
            False, f"earned cycle {cycle} requires explicit completed status", 1, 0
        )
    return {
        "cycle": cycle,
        "receipt_sha256": {
            str(record[2]["pass"]): hashlib.sha256(record[1]).hexdigest() for record in selected
        },
    }, result


def _validate_earned_state(
    state,
    cwd: Path,
    diff_sha256: str,
    diff_files: dict[str, list[int]],
    *,
    receipt_records: tuple[_ReceiptRecord, ...],
    hardened: bool,
    diff_text: str | None,
    reviewed_repositories: dict[str, str] | None,
    _restore_pending: bool = False,
) -> VerifyResult:
    """Revalidate credit, keeping latest pending reservations private to restoration."""
    from .errors import CorruptedStateError
    from .receipt_scope import repository_scope
    from .state import earned_history_cycles, is_host_round, validate_earned_clean_window

    manifest = None if reviewed_repositories is None else repository_scope(reviewed_repositories)[1]
    window = state.earned_clean_window
    try:
        validate_earned_clean_window(window)
        if window["source_hash"] != diff_sha256 or window["reviewed_repositories"] != manifest:
            return VerifyResult(False, "earned window source/repository scope mismatch", 1, 0)
        expected = earned_history_cycles(state, diff_sha256, manifest)
        if expected != [entry["cycle"] for entry in window["cycles"]]:
            return VerifyResult(False, "earned window disagrees with clean/reset history", 1, 0)
        own_history = [
            row
            for row in state.round_history
            if not is_host_round(row) or row["source_hash"] == diff_sha256
        ]
        if (
            not _restore_pending
            and own_history
            and is_host_round(own_history[-1])
            and own_history[-1]["clean_credit_action"] == "pending"
        ):
            return VerifyResult(False, "latest host attempt is still pending", 1, 0)
    except CorruptedStateError as exc:
        return VerifyResult(False, f"unavailable earned proof: {exc}", 1, 0)
    for original in window["cycles"]:
        current, result = _capture_earned_cycle(
            cwd,
            diff_sha256,
            diff_files,
            cycle=original["cycle"],
            hardened=hardened,
            diff_text=diff_text,
            reviewed_repositories=reviewed_repositories,
            receipt_records=receipt_records,
        )
        if not result.passed:
            return VerifyResult(
                False, f"earned proof: {result.reason}", result.checks_run, result.checks_passed
            )
        if current != original:
            return VerifyResult(False, f"earned receipt digest mismatch cycle {original['cycle']}", 1, 0)
    return VerifyResult(True, "earned proof revalidated", 0, 0)


def _restore_earned_window(
    state,
    cwd: Path,
    diff_sha256: str,
    diff_files: dict[str, list[int]],
    *,
    diff_text: str | None,
    reviewed_repositories: dict[str, str] | None,
) -> VerifyResult:
    """Validate saved credit or prove a unique legacy migration before dispatch."""
    from .errors import CorruptedStateError
    from .receipt_scope import repository_scope
    from .state import earned_history_cycles, is_host_round

    try:
        records = _load_receipt_records(cwd / ".code-forge" / "receipts")
        if state.earned_clean_window is None:
            if state._earned_window_present or any(is_host_round(row) for row in state.round_history):
                return VerifyResult(False, "modern history requires an earned clean window", 1, 0)
            manifest = (
                None if reviewed_repositories is None else repository_scope(reviewed_repositories)[1]
            )
            cycles = earned_history_cycles(state, diff_sha256, manifest)
            entries = []
            for cycle in cycles:
                entry, result = _capture_earned_cycle(
                    cwd,
                    diff_sha256,
                    diff_files,
                    cycle=cycle,
                    diff_text=diff_text,
                    reviewed_repositories=reviewed_repositories,
                    receipt_records=records,
                )
                if not result.passed:
                    return result
                entries.append(entry)
            state.earned_clean_window = {
                "version": 1,
                "source_hash": diff_sha256,
                "reviewed_repositories": manifest,
                "cycles": entries,
            }
            state._earned_window_present = True
            if state._clean_state_sha256 is not None:
                state.clean_window_migration = {
                    "original_state_sha256": state._clean_state_sha256,
                    "selected_cycles": cycles,
                }
        return _validate_earned_state(
            state,
            cwd,
            diff_sha256,
            diff_files,
            receipt_records=records,
            hardened=True,
            diff_text=diff_text,
            reviewed_repositories=reviewed_repositories,
            _restore_pending=True,
        )
    except (CorruptedReceiptError, CorruptedStateError, ValueError) as exc:
        return VerifyResult(False, f"unavailable inherited clean proof: {exc}", 1, 0)


def _select_default_earned_cycles(
    cwd: Path,
    diff_sha256: str,
    diff_files: dict[str, list[int]],
    *,
    receipt_records: tuple[_ReceiptRecord, ...],
    required_cycles: int,
    hardened: bool,
    diff_text: str | None,
    reviewed_repositories: dict[str, str] | None,
) -> list[int] | None | VerifyResult:
    from .errors import CorruptedStateError, SchemaVersionMismatchError
    from .state import is_host_round, load_state, validate_round_history

    try:
        try:
            state = load_state(cwd / ".code-forge" / "state.json")
        except (AttributeError, TypeError) as exc:
            return VerifyResult(False, f"unavailable earned state: malformed state.json: {exc}", 1, 0)
        if state is None:
            return None
        validate_round_history(state.round_history)
        modern = any(is_host_round(row) for row in state.round_history)
        if not state.source_hash:
            if modern or state._earned_window_present:
                return VerifyResult(False, "modern state lacks source identity", 1, 0)
            return None
        if state.source_hash != diff_sha256:
            return None
        if state.earned_clean_window is None:
            if modern or state._earned_window_present:
                return VerifyResult(False, "modern history requires an earned clean window", 1, 0)
            return None
    except (CorruptedStateError, SchemaVersionMismatchError, ValueError, OSError, RecursionError) as exc:
        return VerifyResult(False, f"unavailable earned state: {exc}", 1, 0)
    result = _validate_earned_state(
        state,
        cwd,
        diff_sha256,
        diff_files,
        receipt_records=receipt_records,
        hardened=hardened,
        diff_text=diff_text,
        reviewed_repositories=reviewed_repositories,
    )
    if not result.passed:
        return result
    entries = state.earned_clean_window["cycles"]
    if not entries or len(entries) < required_cycles:
        return VerifyResult(
            False,
            f"earned window has {len(entries)} cycles; verifier floor demands {required_cycles}",
            1,
            0,
        )
    own_history = [
        row for row in state.round_history if not is_host_round(row) or row["source_hash"] == diff_sha256
    ]
    latest = own_history[-1] if own_history else None
    if (
        latest is None
        or (is_host_round(latest) and latest["clean_credit_action"] != "earned")
        or (latest["round"] + 1 != entries[-1]["cycle"])
    ):
        return VerifyResult(False, "latest host attempt did not earn clean proof", 1, 0)
    if max((record[2]["cycle"] for record in receipt_records), default=0) != entries[-1]["cycle"]:
        return VerifyResult(False, "earned receipt window stale against latest disk cycle", 1, 0)
    return [entry["cycle"] for entry in entries[-required_cycles:]]


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


def _excerpt_covered(
    receipt: dict,
    assessments: dict[int, ExcerptAssessment],
) -> set[tuple[str, int]]:
    return {
        (exc["file"], line)
        for exc in receipt.get("code_excerpts", [])
        for line in assessments[id(exc)].proven_lines
    }


def _cycle_excerpt_covered(
    receipts: list[dict],
    cycle: int,
    assessments: dict[int, ExcerptAssessment],
) -> set[tuple[str, int]]:
    covered = set()
    for receipt in receipts:
        if receipt["cycle"] == cycle:
            covered |= _excerpt_covered(receipt, assessments)
    return covered


def _coverage_failure_detail(
    cov: set[tuple[str, int]],
    all_diff: set[tuple[str, int]],
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


_CLOSED_DISPOSITIONS = frozenset(
    {
        Disposition.DISMISSED.value,
        Disposition.FIXED.value,
        Disposition.STYLE.value,
    }
)


def _open_findings(items: list) -> list:
    """Findings that still count as an open product defect.

    Missing or non-string disposition is treated as open: older
    receipts omit the field, and a list/dict value must not crash
    the gate.
    """
    open_items = []
    for item in items:
        if not isinstance(item, dict):
            open_items.append(item)
            continue
        disp = item.get("disposition")
        if isinstance(disp, str) and disp in _CLOSED_DISPOSITIONS:
            continue
        open_items.append(item)
    return open_items


def _constant_offset(
    excerpt_line_map: dict[int, str],
    file_lines: dict[int, str],
    lo: int,
    hi: int,
) -> int | None:
    """Return the shift that makes every excerpt line match the file,
    or None when no single offset explains the mismatch. Leading whitespace
    may differ, but the caller must keep that recovery untrusted.

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

    for delta in sorted(
        # Ties go to the negative side: a quote that sits one line above
        # and one line below equally well is far more often a reviewer
        # who counted the anchor line in than one who counted it out.
        (d for d in range(lo, hi + 1) if d != 0),
        key=lambda d: (abs(d), d),
    ):
        matches = 0
        compared = 0
        for claimed, content in excerpt_line_map.items():
            actual = file_lines.get(claimed + delta)
            if actual is None:
                continue
            compared += 1
            if _line_content_matches(content, actual, allow_indent=True):
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


def _line_content_matches(quoted: str, actual: str, *, allow_indent: bool = False) -> bool:
    """Compare line content without changing either line.

    Trailing whitespace is already ignored by excerpt validation. Leading
    whitespace may differ only in an explicitly untrusted alignment.
    """
    if allow_indent:
        return quoted.strip() == actual.strip()
    return quoted.rstrip() == actual.rstrip()


def _only_leading_ws_differs(quoted: str, actual: str) -> bool:
    """Tokens match, but leading spaces or tabs differ."""
    return not _line_content_matches(quoted, actual) and _line_content_matches(
        quoted, actual, allow_indent=True
    )


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


class ExcerptStatus(str, Enum):
    VALID = "VALID"
    UNTRUSTED = "UNTRUSTED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ExcerptAssessment:
    """Derived evidence; never rewrite the supplied quote or coordinates.

    The set excludes unknown halo lines and gaps in a sparse post-image.
    Its bounds alone are insufficient to compute coverage or hunk witnesses.
    Shape-only and exempt assessments have no source-proven coordinates.
    """

    status: ExcerptStatus
    diagnostic: str | None = None
    proven_lines: frozenset[int] = frozenset()
    repaired_tail: bool = False

    @property
    def proven_start(self) -> int | None:
        return min(self.proven_lines) if self.proven_lines else None

    @property
    def proven_end(self) -> int | None:
        return max(self.proven_lines) if self.proven_lines else None


def _single_gap_line(start, end, carried, file_lines):
    """The one source line whose removal aligns carried with the range.

    Linear prefix/suffix scan: p leading and s trailing carried lines
    match the range ends by content, ignoring leading whitespace. The
    caller always marks recovered gaps untrusted. p + s == len(carried)
    means exactly one dropped line, at start + p. A larger sum means
    duplicate neighbour lines make the gap ambiguous; a smaller sum means
    no single-gap alignment. Ambiguous and absent alignments stay invalid.
    """
    if end - start != len(carried):
        return None

    p = 0
    while p < len(carried):
        src = file_lines.get(start + p)
        if src is None or not _line_content_matches(carried[p], src, allow_indent=True):
            break
        p += 1
    s = 0
    while s < len(carried):
        src = file_lines.get(end - s)
        if src is None or not _line_content_matches(
            carried[len(carried) - 1 - s], src, allow_indent=True
        ):
            break
        s += 1
    if p + s != len(carried):
        return None
    missing = start + p
    return missing if missing in file_lines else None


def _anchored_assessment(status, diagnostic, proven, hunks, location, *, repaired_tail=False):
    """A demonstrated quote must witness a hunk at its actual coordinates."""
    proven = frozenset(proven)
    if not any(h["start"] <= n <= h["end"] for h in hunks for n in proven):
        return ExcerptAssessment(
            ExcerptStatus.INVALID,
            f"excerpt {location} is outside every hunk; unchanged context belongs in context_quotes",
        )
    return ExcerptAssessment(status, diagnostic, proven, repaired_tail)


def assess_excerpt_evidence(
    exc: dict,
    hunk_map: dict[str, list[dict]] | None = None,
    post_image: dict[str, dict[int, str]] | None = None,
    exempt_files: list[str] | None = None,
) -> ExcerptAssessment:
    """Assess model-controlled evidence against frozen source, not prose.

    Shape-only calls retain the parser's one-line count slack. With a source
    context, missing nonblank tails need an exact prefix and a known tail;
    offset recovery needs all lines to match within the bounded search.
    """
    invalid = ExcerptStatus.INVALID
    valid = ExcerptStatus.VALID
    untrusted = ExcerptStatus.UNTRUSTED
    if not isinstance(exc, dict):
        return ExcerptAssessment(invalid, "excerpt must be a dictionary")
    exc_file = exc.get("file", "<unknown>")
    exc_start = exc.get("start_line")
    exc_end = exc.get("end_line")
    if not isinstance(exc_file, str) or not exc_file.strip():
        return ExcerptAssessment(invalid, "excerpt file must be a non-empty string")
    if (
        not isinstance(exc_start, int)
        or isinstance(exc_start, bool)
        or not isinstance(exc_end, int)
        or isinstance(exc_end, bool)
    ):
        return ExcerptAssessment(invalid, f"excerpt {exc_file} coordinates must be integers")
    location = f"{exc_file}:{exc_start}-{exc_end}"
    if exc_start <= 0 or exc_end <= 0 or exc_start > exc_end:
        return ExcerptAssessment(invalid, f"excerpt {location} has nonpositive or unordered range")
    content = exc.get("content", "")
    if isinstance(content, list):
        if not all(isinstance(line, str) for line in content):
            return ExcerptAssessment(
                invalid, f"excerpt {location} content list must contain only strings"
            )
        text = "\n".join(content)
    elif isinstance(content, str):
        text = content
    else:
        return ExcerptAssessment(invalid, f"excerpt {location} content must be a string")
    if not text or not text.strip():
        return ExcerptAssessment(invalid, f"excerpt {exc_file}:{exc_start} has empty content")
    claimed = exc_end - exc_start + 1
    actual_lines = excerpt_lines(text)
    count_error = f"excerpt {location} declares {claimed} lines but carries {len(actual_lines)}"
    if not excerpt_line_count_matches(text, claimed):
        return ExcerptAssessment(invalid, count_error)
    if hunk_map is None:
        return ExcerptAssessment(valid)
    exempt = exempt_files or []
    if exc_file not in hunk_map and exc_file not in exempt:
        return ExcerptAssessment(invalid, f"excerpt {exc_file}:{exc_start} not in diff")
    hunks = hunk_map.get(exc_file, [])
    if post_image is None or exc_file in exempt:
        if claimed != len(actual_lines):
            return ExcerptAssessment(invalid, count_error)
        if exc_file not in exempt and not any(
            max(exc_start, h["start"]) <= min(exc_end, h["end"]) for h in hunks
        ):
            return ExcerptAssessment(invalid, f"excerpt {location} is outside every hunk")
        return ExcerptAssessment(valid)

    file_lines = post_image.get(exc_file, {})
    long_by_one = len(actual_lines) == claimed + 1
    if long_by_one and actual_lines and not actual_lines[0].strip():
        actual_lines = actual_lines[1:]
    body_start = exc_start
    short_by_one = claimed == len(actual_lines) + 1
    blank_spent = False
    if short_by_one:
        tail = file_lines.get(exc_end)
        head = file_lines.get(exc_start)
        tail_blank = tail is not None and not tail.strip()
        head_blank = head is not None and not head.strip()
        prefix = {exc_start + i: line for i, line in enumerate(actual_lines)}
        exact_prefix = all(
            n in file_lines and _line_content_matches(line, file_lines[n]) for n, line in prefix.items()
        )
        indent_prefix = not exact_prefix and all(
            n in file_lines and _line_content_matches(line, file_lines[n], allow_indent=True)
            for n, line in prefix.items()
        )
        # Keep carried blanks at their declared coordinates. Only a known
        # omitted tail can explain the count; indentation stays untrusted.
        if (exact_prefix or indent_prefix) and tail is not None:
            trusted_blank = tail_blank and exact_prefix
            missing = (
                None if tail_blank else _single_gap_line(exc_start, exc_end, actual_lines, file_lines)
            )
            diagnostic = (
                f"excerpt {location} is missing source line {missing}"
                if missing is not None
                else count_error
            )
            return _anchored_assessment(
                valid if trusted_blank else untrusted,
                None if trusted_blank else diagnostic,
                prefix,
                hunks,
                location,
            )
        if head_blank and not tail_blank:
            body_start += 1
            blank_spent = True
        elif not tail_blank and not (tail is None and head is None):
            missing = _single_gap_line(exc_start, exc_end, actual_lines, file_lines)
            if missing is not None:
                return _anchored_assessment(
                    untrusted,
                    f"excerpt {location} is missing source line {missing}",
                    (n for n in range(exc_start, exc_end + 1) if n != missing and n in file_lines),
                    hunks,
                    location,
                )
            return ExcerptAssessment(invalid, count_error)

    quoted = {body_start + i: line for i, line in enumerate(actual_lines)}
    overlap = quoted.keys() & file_lines.keys()
    mismatches = sorted(n for n in overlap if not _line_content_matches(quoted[n], file_lines[n]))
    unknown = quoted.keys() - file_lines.keys()
    if mismatches or unknown or not overlap:
        # Resolve indentation at the claimed position before searching for
        # a repeated block elsewhere. A shift needs a content mismatch.
        offset = None
        if unknown or any(
            not _line_content_matches(quoted[n], file_lines[n], allow_indent=True) for n in overlap
        ):
            offset = _constant_offset(quoted, file_lines, -64, 65)
        if offset is not None:
            exact_offset = all(
                _line_content_matches(content, file_lines[n + offset]) for n, content in quoted.items()
            )
            blank_slip = (
                exact_offset
                and not blank_spent
                and _blank_boundary_slip(
                    exc_start,
                    exc_end,
                    offset,
                    file_lines,
                )
            )
            n = mismatches[0] if mismatches else min(quoted)
            diagnostic = (
                None
                if blank_slip
                else (
                    f"excerpt misnumbered by {offset:+d} at {location} "
                    f"(claims {exc_file}:{n}, actually {exc_file}:{n + offset})"
                )
            )
            return _anchored_assessment(
                valid if blank_slip else untrusted,
                diagnostic,
                (n + offset for n in quoted),
                hunks,
                location,
            )
        if (
            unknown
            and not mismatches
            and not any(max(exc_start, h["start"]) <= min(exc_end, h["end"]) for h in hunks)
        ):
            return ExcerptAssessment(
                invalid,
                f"excerpt {location} is outside every hunk; it belongs in context_quotes",
            )
        if (
            unknown
            and all(_only_leading_ws_differs(quoted[n], file_lines[n]) for n in mismatches)
            and len(quoted) >= 10
            and len(unknown) * 10 < len(quoted)
        ):
            return ExcerptAssessment(
                untrusted,
                f"excerpt {location} line {min(unknown)} sits outside the diff; the rest matches",
            )
        if unknown:
            return ExcerptAssessment(
                invalid,
                f"excerpt {location} claims line {min(unknown)} outside the diff post-image; it cannot be verified",
            )
        if mismatches:
            bad = next(
                (n for n in mismatches if not _only_leading_ws_differs(quoted[n], file_lines[n])), None
            )
            if bad is not None and len(overlap) >= 10 and len(mismatches) * 10 < len(overlap):
                return ExcerptAssessment(
                    untrusted,
                    f"excerpt {location} rewrote line {bad}; the rest matches",
                )
            if bad is not None:
                last = max(quoted)
                cut = quoted.get(last, "")
                source = file_lines.get(last, "")
                if (
                    bad == last
                    and mismatches == [last]
                    and cut
                    and source.startswith(cut)
                    and cut != source
                ):
                    stored = exc["content"]
                    if isinstance(stored, list):
                        repaired = list(stored)
                        repaired[-1] = source
                        exc["content"] = repaired
                    else:
                        lines = excerpt_lines(stored)
                        if lines:
                            lines[-1] = source
                            exc["content"] = "\n".join(lines)
                    quoted[last] = source
                    return _anchored_assessment(
                        valid,
                        None,
                        overlap,
                        hunks,
                        location,
                        repaired_tail=True,
                    )
                return ExcerptAssessment(invalid, f"excerpt content mismatch at {location} (line {bad})")
            return _anchored_assessment(
                untrusted,
                f"excerpt indent-stripped at {location}",
                overlap,
                hunks,
                location,
            )
    return _anchored_assessment(valid, None, overlap, hunks, location)


def validate_excerpt_evidence(
    exc: dict,
    hunk_map: dict[str, list[dict]] | None = None,
    post_image: dict[str, dict[int, str]] | None = None,
    exempt_files: list[str] | None = None,
) -> str | None:
    """Compatibility diagnostic view; production gates consume the assessment."""
    return assess_excerpt_evidence(exc, hunk_map, post_image, exempt_files).diagnostic


def _diff_records(text: str) -> list[str]:
    """Split LF records, retaining content CR before Git's no-newline marker."""
    records = text.removesuffix("\n").split("\n")
    return [
        record
        if i + 1 < len(records) and records[i + 1].startswith("\\ No newline at end of file")
        else record.removesuffix("\r")
        for i, record in enumerate(records)
    ]


def _reconstruct_post_image(base: str, section: str) -> list[str] | None:
    """Apply a single text diff to an immutable base, checking every old line."""
    import unidiff
    from unidiff.errors import UnidiffParseError

    try:
        # Normalize diff line endings without changing carriage returns inside
        # source content; a bare CR in a file line is not a diff separator.
        patchset = unidiff.PatchSet([record + "\n" for record in _diff_records("diff --git " + section)])
        if len(patchset) != 1:
            return None
        before = base.replace("\r\n", "\n").split("\n")
        if before[-1] == "":
            before.pop()
        result: list[str] = []
        cursor = 0
        for hunk in patchset[0]:
            start = hunk.source_start if hunk.source_length == 0 else hunk.source_start - 1
            if start < cursor or start > len(before):
                return None
            result.extend(before[cursor:start])
            cursor = start
            for line in hunk:
                if line.line_type == "\\":
                    continue  # Git's no-final-newline marker is not file content.
                value = line.value.removesuffix("\n")
                if line.is_context or line.is_removed:
                    if cursor >= len(before) or before[cursor] != value:
                        return None
                    cursor += 1
                if line.is_context or line.is_added:
                    result.append(value)
                if not (line.is_context or line.is_added or line.is_removed):
                    return None
        result.extend(before[cursor:])
        return result
    except (ValueError, IndexError, TypeError, UnidiffParseError):
        return None


def _diff_validation_context(
    diff_text: str,
    *,
    cwd: Path | None = None,
) -> tuple[dict[str, dict[int, str]], dict[str, list[dict]], list[str]]:
    """Parse frozen diff text into (post_image, hunk_map, exempt_files).

    Same parsing run_verify's hardened excerpt checks use; exposing it
    lets the StateMachine validate in-memory excerpts with the same
    predicate as terminal attestation. When cwd is supplied, immutable
    blobs named in diff headers can supplement bounded context; live
    working files never supply evidence.
    """
    post_image: dict[str, dict[int, str]] = {}
    hunk_map: dict[str, list[dict]] = {}
    current_file: str | None = None
    line_no = 0
    in_hunk = False
    # Lines that introduce or describe a file rather than its content.
    # The context branch below is a catch-all, so anything not named here
    # would be stored as a content line of whichever file came before it.
    header_prefixes = (
        "diff --git ",
        "--- ",
        "index ",
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "similarity index ",
        "dissimilarity index ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "Binary files ",
        "GIT binary patch",
        "\\ No newline at end of file",
    )
    for raw in _diff_records(diff_text):
        if raw.startswith("diff --git "):
            # A new file starts here. Until its +++ header names it, any
            # line belongs to no file, so stop attributing to the last one.
            current_file = None
            line_no = 0
            in_hunk = False
            continue
        if raw.startswith(header_prefixes):
            continue
        if not in_hunk:
            plus = path_from_plus_header(raw)
            if plus is not None:
                current_file = plus
                line_no = 0
                post_image.setdefault(current_file, {})
                hunk_map.setdefault(current_file, [])
                continue
        if raw.startswith("@@") and current_file:
            in_hunk = True
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if m:
                line_no = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                hunk_map[current_file].append({"start": line_no, "end": line_no + count - 1})
        elif current_file and raw.startswith("+") and not raw.startswith("+++"):
            post_image[current_file][line_no] = raw[1:]
            line_no += 1
        elif current_file and raw.startswith("-") and not raw.startswith("---"):
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
    exempt_files = [f for f, lines in post_image.items() if not lines]
    if cwd is not None:
        from .git import read_diff_blob

        for section in re.split(r"(?m)^diff --git ", diff_text)[1:]:
            header, _, _body = section.partition("\n@@")
            plus = None
            for raw in header.splitlines():
                plus = path_from_plus_header(raw)
                if plus is not None:
                    break
            index = re.search(r"(?m)^index ([0-9a-f]+)\.\.([0-9a-f]+)(?:[ \t\r]|$)", header)
            if plus is None or index is None:
                continue
            file = plus
            frozen = post_image.get(file)
            if not frozen:
                continue
            text = read_diff_blob(index.group(2), cwd)
            if text is not None:
                blob_lines = text.replace("\r\n", "\n").split("\n")
                if blob_lines[-1] == "":
                    blob_lines.pop()
                lines = dict(enumerate(blob_lines, 1))
                # A patch may have been edited independently of its index header.
                # Only a blob agreeing with every frozen hunk can supply context.
                if not all(lines.get(n) == value for n, value in frozen.items()):
                    continue
            else:
                base = read_diff_blob(index.group(1), cwd)
                if base is None:
                    continue
                rebuilt = _reconstruct_post_image(base, section)
                if rebuilt is None:
                    continue
                lines = dict(enumerate(rebuilt, 1))
                if not all(lines.get(n) == value for n, value in frozen.items()):
                    continue
            # Match the offset search radius without retaining whole files.
            for hunk in hunk_map[file]:
                for n in range(max(1, hunk["start"] - 65), min(len(lines), hunk["end"] + 65) + 1):
                    post_image[file][n] = lines[n]
    return post_image, hunk_map, exempt_files


def is_one_line_misnumber(err: str) -> bool:
    """True when err names a constant +/-1 coordinate slip."""
    return bool(re.search(r"excerpt misnumbered by [+-]1 ", err))


def bound_excerpt_disposition(disposition, excerpt):
    """A CONFIRMED finding without its own excerpt is not confirmed.

    Envelope-level code_excerpts do not bind to a finding. Missing or
    blank binding demotes CONFIRMED to UNCERTAIN. Other dispositions
    stay as they are.
    """
    from .disposition import Disposition

    text = excerpt if isinstance(excerpt, str) else ""
    if disposition is Disposition.CONFIRMED and not text.strip():
        return Disposition.UNCERTAIN
    return disposition


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
    diff_text: str, excerpts: list[dict], *, cwd: Path | None = None
) -> list[str]:
    """Validate excerpts against frozen diff text; return error strings.

    Empty excerpts list is not an error here (coverage/witness policy is
    enforced by the producer's coverage guard and run_verify's cycle
    checks); this validates only the evidence actually offered, using the
    same predicate run_verify applies to receipts on disk.
    """
    if not diff_text or not excerpts:
        return []
    post_image, hunk_map, exempt_files = _diff_validation_context(diff_text, cwd=cwd)
    errors: list[str] = []
    for exc in excerpts:
        assessment = assess_excerpt_evidence(exc, hunk_map, post_image, exempt_files)
        if assessment.status is ExcerptStatus.INVALID:
            errors.append(assessment.diagnostic or "invalid excerpt evidence")
    return errors


def run_verify(
    cwd: Path,
    diff_sha256: str,
    diff_files: dict[str, list[int]],
    hardened: bool = True,
    diff_text: str | None = None,
    required_cycles: int | None = None,
    cycles: list[int] | None = None,
    respect_floor: bool = True,
    reviewed_repositories: dict[str, str] | None = None,
    require_convergence: bool = True,
) -> VerifyResult:
    return _run_verify_impl(
        cwd,
        diff_sha256,
        diff_files,
        hardened=hardened,
        diff_text=diff_text,
        required_cycles=required_cycles,
        cycles=cycles,
        respect_floor=respect_floor,
        reviewed_repositories=reviewed_repositories,
        require_convergence=require_convergence,
    )


def _run_verify_impl(
    cwd: Path,
    diff_sha256: str,
    diff_files: dict[str, list[int]],
    hardened: bool = True,
    diff_text: str | None = None,
    required_cycles: int | None = None,
    cycles: list[int] | None = None,
    respect_floor: bool = True,
    reviewed_repositories: dict[str, str] | None = None,
    require_convergence: bool = True,
    *,
    receipt_records: tuple[_ReceiptRecord, ...] | None = None,
) -> VerifyResult:
    cp = 0
    if not isinstance(require_convergence, bool):
        return VerifyResult(False, "require_convergence must be a boolean", 1, cp)
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
        not isinstance(required_cycles, int) or isinstance(required_cycles, bool) or required_cycles < 1
    ):
        return VerifyResult(
            False, f"required_cycles must be an integer >= 1, got {required_cycles!r}", 1, cp
        )
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
            or any(not isinstance(c, int) or isinstance(c, bool) or c < 1 for c in cycles)
            or len(set(cycles)) != len(cycles)
        ):
            return VerifyResult(
                False, f"cycles must be a list of distinct positive ints, got {cycles!r}", 1, cp
            )
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
        required_cycles = floor if required_cycles is None else max(required_cycles, floor)
    elif required_cycles is None:
        try:
            required_cycles = read_required_cycles(cwd)
        except UnreadableGateError as exc:
            return VerifyResult(False, f"unreadable gate: {exc}", 1, cp)
    required = required_cycles * PASSES_PER_CYCLE
    try:
        if receipt_records is None:
            receipt_records = _load_receipt_records(cwd / ".code-forge" / "receipts")
        receipts = [record[2] for record in receipt_records]
    except CorruptedReceiptError as exc:
        return VerifyResult(False, f"corrupt receipt: {exc}", 1, cp)

    earned_selection = False
    if cycles is None and require_convergence:
        selection = _select_default_earned_cycles(
            cwd,
            diff_sha256,
            diff_files,
            receipt_records=receipt_records,
            required_cycles=required_cycles,
            hardened=hardened,
            diff_text=diff_text,
            reviewed_repositories=reviewed_repositories,
        )
        if isinstance(selection, VerifyResult):
            return selection
        if selection is not None:
            cycles = selection
            earned_selection = True

    # 1. completeness: last N consecutive cycles
    # findings_count. Reviews that take more rounds write later cycle
    # numbers; the last N consecutive clean cycles are what matters,
    # regardless of what those numbers are.
    if len(receipts) < required:
        msg = f"missing receipts: {len(receipts)}/{required}"
        if len(receipts) == 0:
            msg += " -- no review receipts found. Run 'code-forge review' on your staged changes first"
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
                1,
                cp,
            )
    else:
        if len(all_cycle_vals) < required_cycles:
            return VerifyResult(
                False, f"fewer than {required_cycles} cycles: {len(all_cycle_vals)}", 1, cp
            )
        last_n = all_cycle_vals[-required_cycles:]
    for i in range(len(last_n) - 1):
        if not earned_selection and last_n[i + 1] - last_n[i] != 1:
            return VerifyResult(False, f"last {required_cycles} cycles not consecutive: {last_n}", 1, cp)
    attested = [r for r in receipts if r["cycle"] in last_n]
    if not require_convergence and len(last_n) != 1:
        return VerifyResult(False, "evidence-only attestation requires one cycle", 1, cp)
    if any(r.get("reviewed_repositories") != repository_manifest for r in attested):
        return VerifyResult(False, "INFRA: reviewed repository/source identity mismatch", 1, cp)

    seen_keys = set()
    for r in attested:
        key = (r["cycle"], r["pass"])
        if key in seen_keys:
            return VerifyResult(False, f"duplicate receipt c{key[0]}p{key[1]}", 1, cp)
        seen_keys.add(key)
        if r["findings_count"] != len(r["findings"]):
            return VerifyResult(False, f"findings_count mismatch c{key[0]}p{key[1]}", 1, cp)
        if repository_manifest is not None and any(
            not isinstance(f.get("file"), str) or f["file"] not in diff_files for f in r["findings"]
        ):
            return VerifyResult(False, "INFRA: finding repository/source identity mismatch", 1, cp)
    for c in last_n:
        passes = {p for (cyc, p) in seen_keys if cyc == c}
        # Exactly the three protocol passes, not merely at least them. Asking
        # only that 1-3 be present lets a cycle carry a pass 4 or 5, which the
        # protocol never produces -- three skills run per cycle -- so an extra
        # one is a receipt nobody wrote for a pass nobody ran.
        missing = {1, 2, 3} - passes
        if missing:
            return VerifyResult(False, f"missing cycle {c}/pass {min(missing)}", 1, cp)
        extra = passes - {1, 2, 3}
        if extra:
            return VerifyResult(
                False, f"cycle {c} has pass {min(extra)}, outside the three review passes", 1, cp
            )
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
        post_image, _, _ = _diff_validation_context(diff_text, cwd=cwd)

        if diff_text.strip() and not hunk_map and not exempt_files and _requires_l1_excerpts(diff_text):
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

        # Assess once. The same derived coordinates drive witness, coverage
        # and overlap checks; persisted model coordinates stay untouched.
        assessments = {}
        for exc in all_excerpts:
            assessment = assess_excerpt_evidence(copy.deepcopy(exc), hunk_map, post_image, exempt_files)
            if assessment.status is ExcerptStatus.INVALID:
                return VerifyResult(False, assessment.diagnostic or "invalid excerpt evidence", 5, cp)
            assessments[id(exc)] = assessment

        for file, hunks in hunk_map.items():
            for hunk in hunks:
                if hunk["is_deletion_only"]:
                    continue
                witnessed = any(
                    exc["file"] == file
                    and any(hunk["start"] <= n <= hunk["end"] for n in assessments[id(exc)].proven_lines)
                    for exc in all_excerpts
                )
                if not witnessed:
                    return VerifyResult(
                        False,
                        f"unwitnessed hunk {file}:{hunk['start']}-{hunk['end']}",
                        5,
                        cp,
                    )
        # 6. excerpt-derived coverage >= 60%
        # The floor deliberately counts test lines: tests do not test
        # themselves, so a test-heavy diff is exactly where a reviewer
        # must show they read the test body. This is review-evidence
        # coverage, not test-execution coverage (charter item 6,
        # 43.1-DECISIONS-20260815.md).
        # covered_line_ranges is self-reported, not measured -- audit-only. Ignored here.
        open_files = {
            item.get("file")
            for r in receipts
            for item in r.get("findings", [])
            if isinstance(item, dict)
            and isinstance(item.get("file"), str)
            and item.get("disposition") not in ("DISMISSED", "FIXED", "STYLE")
            and not (
                item.get("disposition") == "UNCERTAIN"
                and not (isinstance(item.get("line"), int) and item.get("line") > 0)
            )
        }
        scoped = {f: lns for f, lns in diff_files.items() if f in open_files}
        all_diff = {(f, ln) for f, lns in scoped.items() for ln in lns}
        if all_diff:
            for c in last_n:
                cov = _cycle_excerpt_covered(receipts, c, assessments) & all_diff
                cycle_items = [f for r in receipts if r.get("cycle") == c for f in r.get("findings", [])]
                if not _open_findings(cycle_items):
                    continue
                if len(cov) / len(all_diff) < 0.6:
                    # The prefix before ';' is the stable format a
                    # consumer may match; the suffix is human guidance.
                    return VerifyResult(
                        False,
                        f"coverage {100 * len(cov) / len(all_diff):.0f}% < 60% cycle {c}; largest uncovered: {_coverage_failure_detail(cov, all_diff)}",
                        6,
                        cp,
                    )
        cp += 1

        # 7. Jaccard overlap > 0.8 = rubber stamp.
        # NOTE: identical excerpts across cycles will cause Jaccard > 0.8.
        # This is CORRECT -- it detects rubber-stamping.
        # Known limitation: when neither cycle has an open finding
        # (empty list, or every finding DISMISSED/FIXED), the skip
        # below causes Jaccard to never trigger, so identical-excerpt
        # reviews still pass. Open findings are CONFIRMED, UNCERTAIN,
        # or a missing/non-string disposition. STYLE is closed.
        cycle_findings = {}
        for r in receipts:
            cyc = r.get("cycle", 0)
            if cyc not in cycle_findings:
                cycle_findings[cyc] = []
            cycle_findings[cyc].extend(r.get("findings", []))

        for a, b in combinations(last_n, 2):
            if not _open_findings(cycle_findings.get(a, [])) and not _open_findings(
                cycle_findings.get(b, [])
            ):
                continue
            cov_a = _cycle_excerpt_covered(receipts, a, assessments)
            cov_b = _cycle_excerpt_covered(receipts, b, assessments)
            if not cov_a and not cov_b:
                return VerifyResult(
                    False,
                    f"no excerpt coverage in cycles {a} and {b} (findings present but excerpts empty)",
                    7,
                    cp,
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
                        False, f"excerpt file missing: {exc['file']} (c{r['cycle']}p{r['pass']})", 5, cp
                    )
                try:
                    lines = read_source_lines(fp)
                    actual = "\n".join(lines[exc["start_line"] - 1 : exc["end_line"]]) + "\n"
                    claimed = exc["content"]
                    if not claimed.endswith("\n"):
                        claimed += "\n"
                    if actual != claimed:
                        return VerifyResult(
                            False,
                            f"excerpt mismatch {exc['file']}:{exc['start_line']}-{exc['end_line']} c{r['cycle']}p{r['pass']}",
                            5,
                            cp,
                        )
                except (IndexError, OSError) as e:
                    logging.warning("check 5 legacy: %s", e)
                    return VerifyResult(
                        False,
                        f"excerpt line range error {exc['file']}:{exc['start_line']}-{exc['end_line']}",
                        5,
                        cp,
                    )
        cp += 1

        # 6. legacy coverage >= 60% (self-reported covered_line_ranges)
        open_files = {
            item.get("file")
            for r in receipts
            for item in r.get("findings", [])
            if isinstance(item, dict)
            and isinstance(item.get("file"), str)
            and item.get("disposition") not in ("DISMISSED", "FIXED", "STYLE")
            and not (
                item.get("disposition") == "UNCERTAIN"
                and not (isinstance(item.get("line"), int) and item.get("line") > 0)
            )
        }
        scoped = {f: lns for f, lns in diff_files.items() if f in open_files}
        all_diff = {(f, ln) for f, lns in scoped.items() for ln in lns}
        if all_diff:
            for c in last_n:
                cov = _cycle_covered(receipts, c) & all_diff
                cycle_items = [f for r in receipts if r.get("cycle") == c for f in r.get("findings", [])]
                if not _open_findings(cycle_items):
                    continue
                if len(cov) / len(all_diff) < 0.6:
                    # Same format contract as the excerpt-derived check 6.
                    return VerifyResult(
                        False,
                        f"coverage {100 * len(cov) / len(all_diff):.0f}% < 60% cycle {c}; largest uncovered: {_coverage_failure_detail(cov, all_diff)}",
                        6,
                        cp,
                    )
        cp += 1

        # 7. legacy Jaccard
        cycle_findings = {}
        for r in receipts:
            cyc = r.get("cycle", 0)
            if cyc not in cycle_findings:
                cycle_findings[cyc] = []
            cycle_findings[cyc].extend(r.get("findings", []))

        for a, b in combinations(last_n, 2):
            if not _open_findings(cycle_findings.get(a, [])) and not _open_findings(
                cycle_findings.get(b, [])
            ):
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
    from .state import PassOutcome

    valid_statuses = {outcome.value for outcome in PassOutcome}
    for receipt in attested:
        status = receipt.get("pass_status")
        if status is not None and (type(status) is not str or status not in valid_statuses):
            return VerifyResult(
                False, f"invalid pass completion status: c{receipt['cycle']}p{receipt['pass']}", 8, cp
            )
    completion_statuses = tuple(
        (receipt["cycle"], receipt["pass"], receipt.get("pass_status")) for receipt in attested
    )
    incomplete = [r for r in attested if r.get("pass_status") not in (None, "completed")]
    if incomplete:
        # Legacy receipts may omit status when all passes otherwise completed.
        # An omitted sibling cannot establish capacity-only host causality.
        unspecified = next((r for r in attested if r.get("pass_status") is None), None)
        if unspecified is not None:
            return VerifyResult(
                False,
                f"pass completion status missing: c{unspecified['cycle']}p{unspecified['pass']} -- incomplete cycle cannot attest",
                8,
                cp,
            )
    unresolved = []
    # Healthy CI checks one invocation without claiming convergence. An
    # incomplete role cannot exempt independent findings in any role.
    if require_convergence or incomplete:
        for r in attested:
            for finding in r["findings"]:
                basis = finding.get("basis")
                if (
                    finding.get("disposition") in ("CONFIRMED", "UNCERTAIN")
                    and isinstance(basis, dict)
                    and basis.get("authority") == "infra-unavailable"
                ):
                    unresolved.append((
                        r["cycle"], r["pass"], r["diff_sha256"],
                        json.dumps(finding, sort_keys=True, separators=(",", ":")),
                    ))
    if unresolved:
        cycle, number, _, _ = unresolved[0]
        return VerifyResult(
            False,
            f"unresolved unverified product finding c{cycle}p{number} -- convergence not established",
            8,
            cp,
            incomplete_passes=tuple(sorted({r["pass"] for r in incomplete})),
            completion_statuses=completion_statuses,
            unresolved_findings=tuple(unresolved),
        )
    if incomplete:
        first = incomplete[0]
        return VerifyResult(
            False,
            f"pass did not complete: c{first['cycle']}p{first['pass']} status={first['pass_status']} -- that pass contributed no review, so the cycle cannot attest",
            8,
            cp,
            failure_kind=VerifyFailureKind.INCOMPLETE_PASS,
            incomplete_passes=tuple(sorted({r["pass"] for r in incomplete})),
            completion_statuses=completion_statuses,
        )
    cp += 1

    return VerifyResult(True, "all 8 checks passed", 8, 8, completion_statuses=completion_statuses)

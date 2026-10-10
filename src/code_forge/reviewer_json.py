# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared reviewer JSON validation and excerpt collection.

Used by both factories.py (Outlet A) and outlet_c.py (Outlet C).

Exception contract: every validation failure raises ValueError (or its
subclass ExcerptEvidenceError), never TypeError. Both factories call sites
catch ValueError to route malformed reviewer output into the salvage path;
raising TypeError for type violations would escape that net and crash the
review. The TRY004 suppressions below are deliberate and pinned by
tests/test_reviewer_json_contract.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = {"findings", "code_excerpts"}
_FINDING_REQUIRED = {"file", "line", "severity", "description"}
_EXCERPT_REQUIRED = {"file", "start_line", "end_line", "content"}
_VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}

# The half of every L1 review prompt that describes the JSON to return. It
# lives next to the validator that enforces that JSON so the two cannot
# drift: asking for a shape validate_reviewer_json rejects fails every pass
# at once. The three L1 sites used to carry byte-identical copies of this
# text, which gave that drift three places to start from. The test-assertion
# pass in cli.py deliberately keeps its own shorter variant -- it reads only
# findings from the response and never collects excerpts, so the excerpt
# instructions here would ask it for output nobody reads.
REVIEW_JSON_CONTRACT = (
    'Return JSON: {"findings": [{"file": "...", "line": N, '
    '"severity": "P0"|"P1"|"P2"|"P3", '
    '"description": "..."}], '
    '"code_excerpts": [{"file": "...", "start_line": N, '
    '"end_line": M, "content": "..."}]}\n'
    "Reply with the JSON object only -- no markdown code fences, no "
    "surrounding text. Raw JSON on the first line.\n"
    "Each diff hunk MUST have at least one code_excerpt.\n"
    "Even if findings is empty, provide code_excerpts "
    "covering each changed hunk.\n"
    "code_excerpts content must be actual source code lines, "
    "not diff format -- no +/- prefixes, no @@ headers.\n"
    "Content must carry exactly end_line - start_line + 1 source lines.\n"
    "Do not span diff gaps between hunks; split into separate excerpts per hunk.\n"
    "Across each review cycle, code_excerpts must cover at least 60% of changed diff lines.\n"
    "Every code_excerpt must fall inside a diff hunk. An excerpt is the "
    "evidence that you checked a changed line, and a line the diff never "
    "touched is not something that claim can be checked against. Code you "
    "read for orientation but did not verify -- a signature above the "
    "change, a caller in another file -- goes in an optional "
    '"context_quotes": [{"file": "...", "content": "..."}] instead. It '
    "carries no line numbers because it asserts nothing about them.\n"
    "start_line and end_line are post-image line numbers of the new file; "
    "the @@ header's old-side start is not a source line.\n"
)


class ExcerptEvidenceError(ValueError):
    """Raised when a well-formed response carries excerpt evidence that
    fails to check out.

    Distinct from the plain ValueError raised for a malformed response.
    Nothing was parsed in the malformed case, so nothing can be audited and
    the run has learned only that the backend is unusable. Here the reply
    parsed. Either it named files and the coordinates were wrong, or it
    claimed a clean pass with empty findings and empty excerpts -- a
    zero-cost envelope, not a dead backend. The two must not converge on
    the same CONFIRMED infrastructure finding, because that makes a
    reviewer with a coordinate habit indistinguishable from a dead backend
    and blocks the clean-round counter forever.
    """


class MissingExcerptEvidenceError(ExcerptEvidenceError):
    """A response did not provide required root excerpt evidence."""


@dataclass(frozen=True)
class ReviewGroupScope:
    """Producer-owned group attribution, separate from model payload fields."""

    name: str
    diff_sha256: str
    source_files: tuple[str, ...]


class GroupedReviewAttempt(dict):
    """An unchanged attempted payload with an out-of-band trusted scope."""

    def __init__(self, payload: dict, scope: ReviewGroupScope):
        super().__init__(payload)
        self.group_scope = scope


_GIT_PREFIX_PAIRS = (
    ("a/", "b/"),
    ("b/", "a/"),
    ("1/", "2/"),
    ("2/", "1/"),
    ("", ""),
    ("i/", "w/"),
    ("w/", "i/"),
    ("c/", "w/"),
    ("w/", "c/"),
    ("c/", "i/"),
    ("i/", "c/"),
    ("o/", "w/"),
    ("w/", "o/"),
)


def _prefixed_git_path(prefix: str, raw: str) -> str:
    return '"' + prefix + raw[1:] if raw.startswith('"') else prefix + raw


def _same_git_header_paths(header: str) -> tuple[str, str, str]:
    """Bind one repeated literal path without splitting on embedded spaces."""
    from .diff import normalize_diff_path

    if not header.startswith("diff --git "):
        return "", "", ""
    body = header[len("diff --git ") :]
    middle = len(body) // 2
    if len(body) % 2 != 1 or body[middle] != " ":
        return "", "", ""
    source, target = body[:middle], body[middle + 1 :]
    quoted = source.startswith('"')
    if quoted and not source.endswith('"'):
        return "", "", ""
    inner = source[1:-1] if quoted else source
    for old, new in _GIT_PREFIX_PAIRS:
        if not inner.startswith(old):
            continue
        raw = inner[len(old) :]
        raw = '"' + raw + '"' if quoted else raw
        if target == _prefixed_git_path(new, raw):
            path = normalize_diff_path(_prefixed_git_path("a/", raw), strip_git_prefix=True)
            if path not in ("", "/dev/null"):
                return source, target, path
    return "", "", ""


def _normalize_review_diff(diff_text: str) -> str:
    """Use the hunk verifier transport form for parsing and consumption."""
    return diff_text.replace("\r\n", "\n").replace("\r", "\n")


def _parse_review_patches(diff_text: str):
    """Retain explicit quoted headers when unidiff duplicates their metadata."""
    from unidiff import PatchSet

    diff_text = _normalize_review_diff(diff_text)
    patches = PatchSet(diff_text)
    for index in range(len(patches) - 1, 0, -1):
        previous, current = patches[index - 1 : index + 1]
        if (
            current
            and not previous
            and not previous.is_binary_file
            and current.patch_info is not None
            and previous.patch_info is current.patch_info
            and (
                str(current.patch_info).partition("\n")[0]
                == f"diff --git {current.source_file} {current.target_file}"
                or current.target_file == "/dev/null"
                and _same_git_header_paths(str(current.patch_info).partition("\n")[0])[0]
                == current.source_file
            )
        ):
            # A shared metadata object and complete literal header identify
            # one file, not a second metadata-only operation.
            del patches[index - 1]
    return patches


def _diff_literal_complete(diff_text: str, patches) -> bool:
    """Check text consumption while retaining opaque binary sections."""
    diff_text = _normalize_review_diff(diff_text)
    normalized = re.sub(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
        lambda m: "@@ -%s,%s +%s,%s @@" % (m[1], m[2] or "1", m[3], m[4] or "1"),
        diff_text,
        flags=re.MULTILINE,
    )
    literal_lines = re.findall(r"[^\n]*\n|[^\n]+$", normalized)
    for pf in patches:
        for hunk in pf:
            for line in hunk:
                if line.line_type == "":
                    return False
                if (
                    line.is_context
                    and line.value == "\n"
                    and line.diff_line_no is not None
                    and 0 < line.diff_line_no <= len(literal_lines)
                    and literal_lines[line.diff_line_no - 1] == "\n"
                ):
                    literal_lines[line.diff_line_no - 1] = " \n"
    normalized = "".join(literal_lines)
    if str(patches).splitlines() == normalized.splitlines():
        return True
    rendered = []
    for section in re.split(r"(?m)(?=^diff --git )", normalized):
        if not section:
            continue
        entries = _parse_review_patches(section)
        if not entries:
            return False
        pf = entries[0]
        if pf.is_binary_file:
            if entries[1:]:
                return False
            header, marker, payload = section.partition("\nGIT binary patch\n")
            if not section.startswith("diff --git ") or not marker or not payload.strip():
                return False
            if str(pf.patch_info).splitlines() != header.splitlines():
                return False
            index_lines = [line for line in header.splitlines() if line.startswith("index ")]
            if len(index_lines) != 1 or not re.fullmatch(
                r"index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: [0-7]{6})?", index_lines[0]
            ):
                return False
            # unidiff drops encoded binary payloads when rendering. The
            # existing binary policy treats them as opaque, not text.
            rendered.append(section)
        else:
            consumed = 0
            for pf in entries:
                text = str(pf)
                prefix = str(pf.patch_info) if pf.patch_info is not None else ""
                literal = section[consumed:]
                if pf and literal.startswith(prefix):
                    literal_headers = literal[len(prefix) :].split("\n", 2)
                    rendered_headers = text[len(prefix) :].split("\n", 2)
                    if len(literal_headers) < 2 or len(rendered_headers) < 2:
                        return False
                    for index in range(2):
                        # Restore only the two parsed file-header positions.
                        if literal_headers[index] == rendered_headers[index] + "\t":
                            rendered_headers[index] = literal_headers[index]
                    text = prefix + "\n".join(rendered_headers)
                consumed += len(text)
                rendered.append(text)
    return "".join(rendered).splitlines() == normalized.splitlines()


def _metadata_paths(header: str, raw_paths, *, hunk_paths=None) -> tuple[str, ...]:
    """Bind metadata names to the complete Git header before decoding."""
    from .diff import normalize_diff_path

    paths = []
    for prefix, raw in zip(("a/", "b/"), raw_paths, strict=True):
        if raw.startswith('"') and not raw.endswith('"'):
            return ()
        # Metadata has no a/b prefix; retain real directories named a/b.
        paths.append(normalize_diff_path(_prefixed_git_path(prefix, raw), strip_git_prefix=True))
    if all(path not in ("", "/dev/null") for path in paths):
        for prefixes in _GIT_PREFIX_PAIRS:
            pair = tuple(
                _prefixed_git_path(prefix, raw) for prefix, raw in zip(prefixes, raw_paths, strict=True)
            )
            if header == "diff --git " + " ".join(pair) and (hunk_paths is None or pair == hunk_paths):
                return tuple(paths)
    return ()


def _metadata_operation_paths(header: str, metadata, *, hunk_paths=None) -> tuple[str, ...]:
    """Bind a paired rename/copy operation to its complete literal header."""
    operation = "rename" if metadata[1].startswith("rename from ") else "copy"
    prefixes = (operation + " from ", operation + " to ")
    if not all(line.startswith(prefix) for line, prefix in zip(metadata[1:], prefixes, strict=True)):
        return ()
    raw_paths = [line[len(prefix) :] for line, prefix in zip(metadata[1:], prefixes, strict=True)]
    return _metadata_paths(header, raw_paths, hunk_paths=hunk_paths)


def _requires_l1_excerpts(
    diff_text: str, *, reviewed_repositories: dict[str, str] | None = None
) -> bool:
    """Prove narrow post-image exemptions; ambiguous diffs require evidence.

    This is an applicability decision, not a general diff validator. Text
    must be consumed completely; binary payloads retain their existing
    opaque acceptance policy independently of text rendering.
    """
    from unidiff import UnidiffParseError

    from .diff import normalize_diff_path

    if reviewed_repositories is not None:
        from .receipt_scope import repository_scope

        try:
            repository_scope(reviewed_repositories)
        except ValueError:
            return True
        return any(_requires_l1_excerpts(diff) for diff in reviewed_repositories.values())
    if not diff_text.strip():
        return False
    try:
        patches = _parse_review_patches(diff_text)
        if not patches or not _diff_literal_complete(diff_text, patches):
            return True
    except (UnidiffParseError, AttributeError, UnboundLocalError):
        return True
    for pf in patches:
        if pf.is_binary_file:
            continue
        if pf and (
            not all(h.removed > 0 and h.added == 0 for h in pf)
            or normalize_diff_path(pf.target_file, strip_git_prefix=True) == "/dev/null"
            and any(h.target_length != 0 for h in pf)
        ):
            return True
        if pf:
            source_path = normalize_diff_path(pf.source_file, strip_git_prefix=True)
            if source_path in ("", "/dev/null"):
                return True
        if pf and not pf.patch_info:
            # Traditional unified hunks still require a real source identity.
            continue
        header, *info = excerpt_lines(str(pf.patch_info))
        modes = info[:2] if len(info) >= 2 else []
        mode_change = bool(
            modes
            and re.fullmatch(r"old mode (?:100644|100755|120000|160000|040000)", modes[0])
            and re.fullmatch(r"new mode (?:100644|100755|120000|160000|040000)", modes[1])
            and modes[0][9:] != modes[1][9:]
        )
        rename = info[2:] if mode_change else info
        if (
            not pf
            and (len(info) == 3 or mode_change)
            and len(rename) == 3
            and rename[0] == "similarity index 100%"
        ):
            # unidiff's greedy header split is not an identity witness for
            # paths containing " b/". Bind the complete literal instead.
            paths = _metadata_operation_paths(header, rename)
            if paths and paths[0] != paths[1]:
                continue
        source, target, header_path = _same_git_header_paths(header)
        same_path_header = bool(header_path)
        if pf:
            # unidiff retains unknown metadata verbatim. A round trip proves
            # consumption, so prove the narrow Git metadata shape as well.
            same_hunk_paths = bool(
                same_path_header
                and pf.source_file == source
                and (pf.target_file == target or pf.target_file == "/dev/null")
            )
            if not info and same_hunk_paths:
                # Retain synthetic unified packets with only a Git header.
                continue
            index = (
                re.fullmatch(
                    r"index ([0-9a-f]{4,64})\.\.([0-9a-f]{4,64})(?: (100644|100755|120000|160000|040000))?",
                    info[-1],
                )
                if info
                else None
            )
            if not index or not index[1].strip("0"):
                return True
            metadata = info[:-1]
            if (
                same_hunk_paths
                and pf.target_file == "/dev/null"
                and not index[2].strip("0")
                and not index[3]
                and len(metadata) == 1
                and re.fullmatch(
                    r"deleted file mode (?:100644|100755|120000|160000|040000)", metadata[0]
                )
            ):
                continue
            if not index[2].strip("0") or pf.target_file == "/dev/null":
                return True
            if mode_change:
                if index[3]:
                    return True
                metadata = metadata[2:]
            if same_hunk_paths and (
                not metadata
                or len(metadata) == 1
                and re.fullmatch(r"dissimilarity index (?:100|[1-9]?[0-9])%", metadata[0])
            ):
                continue
            if len(metadata) == 3 and re.fullmatch(
                r"similarity index (?:100|[1-9]?[0-9])%", metadata[0]
            ):
                paths = _metadata_operation_paths(
                    header, metadata, hunk_paths=(pf.source_file, pf.target_file)
                )
                if paths and paths[0] != paths[1]:
                    continue
            return True
        if len(info) == 2 and mode_change and same_path_header:
            continue
        empty_index = None
        if (
            pf.is_added_file
            and len(info) == 2
            and same_path_header
            and re.fullmatch(r"new file mode (?:100644|100755)", info[0])
        ):
            empty_index = info[1]
        elif (
            pf.is_removed_file
            and len(info) == 2
            and same_path_header
            and re.fullmatch(r"deleted file mode (?:100644|100755)", info[0])
        ):
            deleted_index = re.fullmatch(r"index ([0-9a-f]{4,64})\.\.(0{4,64})", info[1])
            if deleted_index:
                empty_index = f"index {deleted_index[2]}..{deleted_index[1]}"
        if empty_index:
            index = re.fullmatch(r"index (0{4,64})\.\.([0-9a-f]{4,64})", empty_index)
            empty_blobs = (
                "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
                hashlib.sha256(b"blob 0\0").hexdigest(),
            )
            if index and any(
                len(index[1]) <= len(blob) and blob.startswith(index[2]) for blob in empty_blobs
            ):
                continue
        return True
    return False


def require_l1_excerpt_evidence(
    data: dict, diff_text: str, *, reviewed_repositories: dict[str, str] | None = None
) -> None:
    """Trusted L1 publishers require root evidence independently of findings.

    Generic JSON validation and non-receipt advisory consumers retain their
    existing acceptance contract. This guard uses only the publisher scope.
    """
    if not data["code_excerpts"] and _requires_l1_excerpts(
        diff_text, reviewed_repositories=reviewed_repositories
    ):
        raise MissingExcerptEvidenceError("required L1 pass has no root code_excerpts")


def _strip_fence(raw: str) -> str:
    """Strip a complete markdown fence envelope if one wraps the reply.

    Models routed through some gateways wrap their JSON in ```json
    fences. Only a full envelope is stripped -- an opening fence line
    and a closing fence line with nothing outside -- because that is
    the only shape that is unambiguously formatting rather than
    content. Anything else is returned unchanged so validation still
    fails closed on it.
    """
    if not isinstance(raw, str):
        # Non-string input reaches json.loads as before, whose
        # TypeError the caller converts to ValueError -- this helper
        # must not raise an AttributeError ahead of that contract.
        return raw
    text = raw.strip()
    if not text.startswith("```"):
        return text
    first_nl = text.find("\n")
    if first_nl == -1:
        # A lone fence line is not an envelope.
        return text
    opener = text[:first_nl].strip().lower()
    if opener not in ("```", "```json"):
        return text
    if not text.endswith("```"):
        return text
    tail = text[:-3]
    last_nl = tail.rfind("\n")
    if last_nl == -1 or tail[last_nl + 1 :].strip():
        # The closing fence must sit on its own line; only indentation
        # may precede it. A ``` that terminates a JSON string is
        # content, not a fence -- cutting it would amputate the string
        # and the parse fails anyway, but "the fence line" is only
        # ever a fence when it is one.
        return text
    return text[first_nl + 1 : -3].strip()


def excerpt_lines(text: str) -> list[str]:
    """Split excerpt content into the lines it quotes.

    A single trailing newline is treated as terminating the last line rather
    than introducing an empty one, which is the common case.
    """
    return text.removesuffix("\n").split("\n")


def read_source_lines(path: Path) -> list[str]:
    """Read physical LF source lines, accepting CRLF without splitting content CR."""
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return excerpt_lines(text) if text else []


def _hoist_nested_excerpts(data: dict) -> None:
    """Lift per-finding code_excerpts onto the envelope root.

    Some backends (Agnes expert among them) return a parseable object
    whose excerpts sit inside each finding rather than at the required
    root key. That is the same class of envelope mismatch as a markdown
    fence: the evidence is present, the wrapping is wrong. Repairing
    the wrapping is not fabricating excerpts.

    Only runs when the root key is absent. A present key, including an
    empty list, is a claimed envelope and is left alone so coverage is
    not double-counted and a silent empty root is not papered over.
    Mutates ``data``; copies every finding dict so the caller's objects
    stay intact. Non-dict entries are kept as-is and do not stop the
    walk; schema checks after hoist still reject them.
    """
    if "code_excerpts" in data:
        return
    findings = data.get("findings")
    if not isinstance(findings, list):
        return
    hoisted: list = []
    rewritten: list = []
    for item in findings:
        if not isinstance(item, dict):
            rewritten.append(item)
            continue
        nested = item.get("code_excerpts")
        if isinstance(nested, list) and nested:
            hoisted.extend(nested)
            fresh = dict(item)
            del fresh["code_excerpts"]
            rewritten.append(fresh)
        else:
            rewritten.append(dict(item))
    if hoisted:
        data["findings"] = rewritten
        data["code_excerpts"] = hoisted


def _normalize_excerpt_aliases(data: dict) -> None:
    """Recognize known excerpt spellings without overriding canonical fields."""
    excerpts = data.get("code_excerpts")
    if not isinstance(excerpts, list):
        return
    for excerpt in excerpts:
        if not isinstance(excerpt, dict):
            continue
        for alias, canonical in (
            ("line_start", "start_line"),
            ("line_end", "end_line"),
            ("code", "content"),
        ):
            if canonical not in excerpt and alias in excerpt:
                excerpt[canonical] = excerpt[alias]


def excerpt_line_count_matches(text: str, claimed: int) -> bool:
    """Report whether an excerpt carries as many lines as it declares.

    Both directions of a one-line mismatch are backend coordinate jitter,
    not evidence fabrication:

    - A quote ending on a blank source line cannot be represented
      faithfully: joining ["a", "b", ""] yields "a\\nb\\n", the same
      string as two newline-terminated lines, and backends routinely
      drop the empty entry altogether (declared N+1, carries N).
    - An off-by-one end_line makes the model paste one line more than
      its range declares (declared N, carries N+1) -- the mirror image,
      observed routinely on real backends.

    excerpt_lines() (trailing newline stripped, then split on \\n)
    picks one reading, so every such excerpt was rejected as a schema
    violation and took the whole review pass with it. One line of slack
    in either direction is therefore accepted here.
    The tolerance is safe because it only widens a counting heuristic:
    validate_excerpt_evidence re-checks the count whenever it cannot
    confirm the content against a frozen post-image, and anchors the
    content when it can.
    """
    actual = len(excerpt_lines(text))
    return abs(claimed - actual) <= 1


def validate_reviewer_json(raw: str | dict) -> dict:
    """Validate reviewer output against the receipt schema.

    Accepts either a JSON string or an already-parsed dict.
    A markdown fence envelope around a JSON string is stripped before
    parsing. Raises ValueError on any schema violation (fail-closed).
    """
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(_strip_fence(raw))
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("not a JSON object")  # noqa: TRY004 - salvage routing catches ValueError

    _hoist_nested_excerpts(data)
    _normalize_excerpt_aliases(data)

    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(f"missing required field: {field}")

    if not isinstance(data["findings"], list):
        raise ValueError("findings must be a list")  # noqa: TRY004 - salvage routing catches ValueError
    if not isinstance(data["code_excerpts"], list):
        raise ValueError("code_excerpts must be a list")  # noqa: TRY004 - salvage routing catches ValueError

    for i, f in enumerate(data["findings"]):
        if not isinstance(f, dict):
            raise ValueError("finding[%d] is not a dict" % i)  # noqa: TRY004 - salvage routing catches ValueError
        for key in _FINDING_REQUIRED:
            if key not in f:
                raise ValueError("finding[%d] missing: %s" % (i, key))
        if f.get("severity") not in _VALID_SEVERITIES:
            raise ValueError("finding[%d] invalid severity: %s" % (i, f.get("severity")))

    kept = []
    for i, exc in enumerate(data["code_excerpts"]):
        if not isinstance(exc, dict):
            raise ValueError("code_excerpt[%d] is not a dict" % i)  # noqa: TRY004 - salvage routing catches ValueError
        for key in _EXCERPT_REQUIRED:
            if key not in exc:
                raise ValueError("code_excerpt[%d] missing: %s" % (i, key))
        exc_file = exc.get("file")
        if not isinstance(exc_file, str) or not exc_file.strip():
            raise ValueError("code_excerpt[%d] file must be a non-empty string" % i)
        for coord in ("start_line", "end_line"):
            v = exc.get(coord)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("code_excerpt[%d] %s must be int" % (i, coord))  # noqa: TRY004 - salvage routing catches ValueError
        s = exc["start_line"]
        e = exc["end_line"]
        if s <= 0 or e <= 0:
            raise ValueError(
                "code_excerpt[%d] start_line and end_line must be positive, got %r and %r" % (i, s, e)
            )
        if s > e:
            raise ValueError("code_excerpt[%d] start_line %d > end_line %d" % (i, s, e))
        content = exc.get("content")
        if isinstance(content, list):
            if not all(isinstance(ln, str) for ln in content):
                raise ValueError("code_excerpt[%d] content list must contain only strings" % i)
            text = "\n".join(content)
        elif isinstance(content, str):
            text = content
        else:
            raise ValueError("code_excerpt[%d] content must be str" % i)  # noqa: TRY004 - salvage routing catches ValueError
        if not text.strip():
            logger.warning("code_excerpt[%d] content is empty; skipping it", i)
            continue
        claimed = e - s + 1
        if not excerpt_line_count_matches(text, claimed):
            raise ExcerptEvidenceError(
                "code_excerpt[%d] %s:%d-%d declares %d lines but carries %d"
                % (i, exc_file, s, e, claimed, len(excerpt_lines(text)))
            )
        kept.append(exc)

    data["code_excerpts"] = kept
    if len(data["findings"]) == 0 and len(kept) == 0:
        raise MissingExcerptEvidenceError(
            "findings=0 but code_excerpts empty -- reviewer must provide "
            "per-hunk excerpts even for clean passes"
        )

    return data


_VALID_PASS_NAMES = frozenset({"qodo", "expert", "adversarial"})


def _collect_excerpts(data: dict, *, pass_name: str) -> list[dict]:
    """Extract code_excerpts from validated reviewer JSON with trusted pass attribution."""
    if pass_name not in _VALID_PASS_NAMES:
        raise ValueError(f"invalid pass_name {pass_name!r}, expected one of {sorted(_VALID_PASS_NAMES)}")
    out = []
    for exc in data.get("code_excerpts", []):
        if not isinstance(exc, dict):
            continue
        fresh = dict(exc)
        fresh["pass_name"] = pass_name
        out.append(fresh)
    return out


_LINE_BUCKET_SIZE: int = 10
"""Quantisation step for location-stable fingerprints.

Lines rounded to the nearest multiple of this value are treated as the
same location.  The value 10 absorbs the +/-3 line jitter measured on
real LLM outputs (e.g. 979/981/982 all round to 980) while keeping
genuinely distant lines distinct.
"""


def _location_fingerprint(
    file_path: str,
    line: int,
    pass_name: str,
) -> str:
    """Compute a location-stable fingerprint for an L1 finding.

    The fingerprint is determined by (file, line_bucket, pass_name) only,
    NOT by description text.  This means a model that restates the same
    issue in different words across rounds produces the same fingerprint,
    so the convergence state machine treats it as the same finding rather
    than a new one that resets the clean-round counter.

    Two genuinely different findings at the same file+line_bucket from the
    same pass collapse into one fingerprint.  That is acceptable: a single
    pass rarely reports two semantically distinct issues at the exact same
    line, and when it does the dedup layer keeps whichever it encounters
    first (insertion order, not severity).  This is an explicit trade-off:
    location stability for convergence is worth losing a rare same-bucket
    duplicate.

    Bucketing rounds to the NEAREST multiple rather than flooring, and
    that is load-bearing.  Floor division puts a hard edge every ten
    lines, so the measured 979/981/982 jitter straddles it and splits
    into two fingerprints -- the exact failure this function exists to
    prevent.  Rounding centres the bucket on the reported line instead,
    so jitter of +/-5 stays together wherever it falls.

    Python's round() is round-half-to-even, so a line landing exactly on
    a .5 boundary (975, 985) picks the even neighbour.  That makes the
    boundary case look arbitrary, but it is stable: the same line always
    produces the same bucket, which is all the fingerprint requires.
    """
    bucket = round(line / _LINE_BUCKET_SIZE) * _LINE_BUCKET_SIZE
    fp_src = "%s:%d:%s" % (file_path, bucket, pass_name)
    return hashlib.sha256(fp_src.encode()).hexdigest()[:16]


def _dedup_by_fingerprint(
    findings: list,
    seen=None,
) -> list:
    """Fold findings to one per fingerprint, first-in-wins.

    A finding whose fingerprint was already seen is dropped regardless
    of severity; the first occurrence in insertion order survives.  The
    `seen` set may carry state from earlier folds (the provider loops
    pass the same set across passes) so the dedup holds across an entire
    fold, not just within one batch.

    Shared by every L1 fold site -- build_l1_provider and
    build_grouped_l1_provider in factories.py, plus the L1 chunk fold
    in outlet_c.py -- so the "first-in-wins" claim is true on every
    path, not just one.
    """
    if seen is None:
        seen = set()
    kept = []
    for f in findings:
        if f.fingerprint in seen:
            continue
        seen.add(f.fingerprint)
        kept.append(f)
    return kept


_DEFECT = ("but", "however", "leak", "bug")


_PRAISE = ("正确实现", "逻辑是健全", "is correct", "looks correct", "no issue")


def _is_praise(description: str) -> bool:
    """A comment that only says the code is fine is not a defect."""
    text = description.strip().lower()
    if not text or any(word in text for word in _DEFECT):
        return False
    return any(mark in text or mark in description for mark in _PRAISE)


def _json_to_state_findings(
    data: dict,
    pass_name: str,
    backend: str | None = None,
) -> list:
    """Convert validated reviewer JSON findings to StateFinding list.

    Shared by outlet_c.py (Outlet C) and factories.py (Outlet A) to
    ensure identical fingerprint computation across both code paths.

    backend names the model that produced these findings, so the ledger
    can attribute them later. It stays optional: a caller with nothing
    truthful to name leaves it None rather than guessing.
    """
    from .disposition import Disposition
    from .state import StateFinding

    findings = []
    for f_raw in data.get("findings", []):
        # Callers on the untrusted-downgrade path reach here with JSON that
        # validate_reviewer_json already rejected, so the per-element dict
        # check never ran. Skip what is not a finding object rather than
        # aborting the review on it.
        if not isinstance(f_raw, dict):
            continue
        file_path = f_raw.get("file") or "unknown"
        try:
            line = int(f_raw.get("line") or 0)
        except (ValueError, TypeError):
            line = 0
        desc = f_raw.get("description") or ""
        if _is_praise(desc):
            continue
        fp = _location_fingerprint(file_path, line, pass_name)
        findings.append(
            StateFinding(
                id=f"l1-{pass_name}-{fp}",
                fingerprint=fp,
                source="L1",
                disposition=Disposition.UNCERTAIN,
                file=file_path,
                line_range=[line, line],
                description=f"[{pass_name}] {desc}",
                backend=backend,
                # Validated against _VALID_SEVERITIES above; carried through
                # rather than dropped, so the convergence gate can tell a P0
                # from a P3 instead of defaulting every L1 finding to P1.
                #
                # Re-checked here rather than trusting validate_reviewer_json:
                # this function is importable and gets called directly (the
                # tests do it), so the validation upstream is a convention
                # rather than a guarantee. An unrecognised value becomes None,
                # which _severity_tier treats as "no reviewer opinion" and
                # falls back for -- the same position it was in before this
                # field existed.
                severity=(f_raw.get("severity") if f_raw.get("severity") in _VALID_SEVERITIES else None),
                excerpt=_finding_excerpt(f_raw),
            )
        )
    return findings


def _finding_excerpt(raw: dict) -> str | None:
    text = raw.get("excerpt")
    if isinstance(text, str) and text.strip():
        return text
    return None

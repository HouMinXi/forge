"""Load, mutate and print a forge config without losing what a human wrote.

Every edit is a named mutation over the parsed document. A write is refused when
it would reach outside the entry it names, when it would move a comment to a
different entry, and when the file mixes CRLF and LF line endings. A comment
written inside a removed entry goes with it, and a comment on its key line is
reported in `notes`. Formatting the file already carries (indent, line endings, quotes) is kept where
the dumper can reproduce it. A file that steps its indentation differently at
each level, or wraps a long line by hand, comes back normalised: that is
reported in `notes` rather than refused, because re-indentation changes no
setting.
"""

from __future__ import annotations

import difflib
import functools
import io
import re
import textwrap
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

import yaml as _pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError  # noqa: F401 - re-exported for the script
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

ROUTING_FIELDS = (
    "base_url",
    "api_key_env",
    "model",
    "format",
    "max_tokens",
    "timeout_s",
    "stream",
    "headers",
)


def _string(value: str):
    """A string that reads back as itself.

    YAML turns `off`, `on`, `yes`, `null` and numeric-looking text into other
    types, so a header written plainly comes back as a boolean or a float.
    Quoting the value keeps what the caller asked for.
    """
    try:
        plain = _pyyaml.safe_load(value)
    except Exception:  # noqa: BLE001 - anything unparseable must be quoted
        plain = object()
    if isinstance(plain, str) and plain == value:
        return value
    return DoubleQuotedScalarString(value)


def _equivalent(existing, value) -> bool:
    """True when writing `value` would not change what readers resolve.

    `1.0` written as a string and the float it parses to are the same setting;
    without this an idempotent `set` rewrote the file, took a backup and
    re-sealed trust on every run.
    """
    if existing is None:
        return value is None
    if isinstance(existing, bool) or isinstance(value, bool):
        return existing is value
    if isinstance(value, int):
        # `timeout_s`/`max_tokens` readers take a real int: `"2400"` and `2400.0`
        # both fail to load, so writing the int is a repair, not a no-op.
        return isinstance(existing, int) and not isinstance(existing, bool) and existing == value
    if existing == value:
        return True
    if isinstance(value, str) and isinstance(existing, (int, float)):
        return str(existing) == value.strip() and _parses_as_int(existing) == _parses_as_int(value)
    if isinstance(existing, str) and isinstance(value, (int, float)):
        # `2400.0` and "2400" are the same number but the timeout reader takes
        # an exact int and refuses the float, so they are not the same setting.
        return existing.strip() == str(value) and _parses_as_int(existing) is not None
    return False


def _parses_as_int(value) -> int | None:
    """The int a config reader would parse out of `value`, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class Refused(ValueError):
    """A mutation that would do something the caller did not ask for."""


class NotLocatable(Refused):
    """The file declares the backend but its text cannot be addressed.

    A one-line mapping (`backends: {alpha: {...}}`) parses fine and is still
    not something this editor can splice, so saying "not declared" would be a
    lie and would let a sweep report success while skipping the file.
    """


class NotDeclared(Refused):
    """The file does not declare that backend. Its own class, because callers
    branch on it and text comparison breaks the moment a message is reworded."""


@dataclass
class Guards:
    """What the caller knows about layers and resolution; the module asks."""

    resolve: Callable[[dict, Mapping], tuple | None] | None = None
    user_backends: Mapping = field(default_factory=dict)
    fields: tuple[str, ...] = ROUTING_FIELDS


def _loader(line_ending: str, mapping_indent: int = 2) -> YAML:
    yaml = YAML(typ="rt")  # round-trip: comments, order, anchors, quoting
    yaml.preserve_quotes = True
    yaml.indent(mapping=mapping_indent, sequence=mapping_indent * 2, offset=mapping_indent)
    yaml.line_break = line_ending
    # The default width is 80 columns, so dumping rewraps any longer scalar and
    # pushes the change outside the edited entry; with a long User-Agent in a
    # neighbour, every edit to that file was refused as "not local".
    yaml.width = 1_000_000
    return yaml


def _detect_indent(text: str) -> int:
    """The indent the file already uses under `backends:`.

    A file written at four spaces must not be re-indented by a one-field edit:
    that rewrites every line of the block and makes a local change look global.
    """
    lines = text.replace("\r\n", "\n").splitlines()
    try:
        base_index, base = _mapping_start(lines, "backends")
    except KeyError:
        return 2
    for line in lines[base_index + 1 :]:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        depth = len(line) - len(stripped)
        if depth <= base:
            return 2
        delta = depth - base
        return delta if 1 <= delta <= 8 else 2
    return 2


class Document:
    def __init__(self, text: str) -> None:
        self.source = text
        crlf = text.count("\r\n")
        bare_lf = text.count("\n") - crlf
        # A file with both endings is left on LF: translating it would rewrite
        # every line, and locality would then be comparing two different
        # documents. Mixed files are rare and the refusal that follows says so.
        self.line_ending = "\r\n" if crlf and not bare_lf else "\n"
        self.mixed_endings = bool(crlf and bare_lf)
        # Parse with LF only: a carriage return left inside a scalar or a
        # comment token is emitted again on the way out, which turned every
        # line of a CRLF file into "\r\r\n".
        self.yaml = _loader("\n", _detect_indent(text))
        self.notes: list[str] = []
        self.changed = False
        self.reload()
        step = self._block_step()
        if step and step != _detect_indent(text):
            self.yaml = _loader("\n", step)
            self.reload()

    def reload(self) -> None:
        # `typ="rt"` builds mappings, sequences and scalars only; an unknown tag
        # raises instead of constructing an object, which is why it is not
        # `typ="unsafe"`.
        self.data = self.yaml.load(self.source.replace("\r\n", "\n")) or {}

    def dumped(self) -> str:
        buffer = io.StringIO()
        self.yaml.dump(self.data, buffer)
        text = buffer.getvalue()
        return text if self.line_ending == "\n" else text.replace("\n", self.line_ending)

    def _block_step(self, key: str = "backends") -> int | None:
        """The indent step the parsed block actually uses, 2 when unknown."""
        try:
            position = self.data.lc.data.get(key)
            block = self.data[key]
            first = next(iter(block))
            child = block.lc.data.get(first)
        except (AttributeError, KeyError, TypeError, StopIteration):
            return None
        if not position or not child:
            return None
        step = int(child[1]) - int(position[1])
        return step if step > 0 else None

    def block_start(self, key: str = "backends") -> tuple[int, int]:
        """Where the `backends:` key really is, from the parsed document.

        Text scanning cannot tell a `backends:` at column 0 from the same text
        inside a multi-line quoted scalar; the parse can, and it already carries
        the line and column of every key.
        """
        try:
            position = self.data.lc.data.get(key)
        except (AttributeError, KeyError):
            position = None
        if position:
            return int(position[0]), int(position[1])
        lines = self.source.replace("\r\n", "\n").splitlines()
        return _mapping_start(lines, key)

    def entry_line(self, name: str, key: str = "backends") -> int | None:
        """The line of this entry's own key, from the parsed document.

        A flow mapping (`backends: {alpha: {...}}`) has no per-entry lines to
        address; it is reported as unaddressable rather than given a span.
        """
        try:
            block = self.data[key]
            if getattr(getattr(block, "fa", None), "flow_style", None) and block.fa.flow_style():
                return None
            return int(block.lc.data[name][0])
        except (AttributeError, KeyError, TypeError, IndexError):
            return None

    def span(self, name: str) -> tuple[int, int]:
        """The entry's lines, found inside the `backends:` mapping.

        Searching the whole file for the first `name:` picks the wrong key when
        a top-level policy block happens to use the same word, which made every
        rename of such a name look like an edit outside its own entry.
        """
        line = self.entry_line(name)
        if line is not None:
            lines = self.source.replace("\r\n", "\n").splitlines()
            if 0 <= line < len(lines):
                indent = len(lines[line]) - len(lines[line].lstrip())
                return _span_from(lines, line, indent)
        try:
            return entry_span(self.source.splitlines(), name)
        except KeyError as exc:
            raise NotLocatable(
                f"{name!r} is declared, but this file keeps backends in a form "
                f"this editor cannot address (one line, or a block alias)"
            ) from exc


def load_document(text: str) -> Document:
    """Parse one config into a Document; the only way the script builds one."""
    return Document(text)


def _mapping_start(lines: list[str], key: str) -> tuple[int, int]:
    """Line index of `key:` and its indent."""
    pattern = re.compile(rf"^(?P<indent>[ \t]*)(?P<quote>['\"]?){key}(?P=quote)[ \t]*:")
    fallback = None
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        if not match.group("indent"):
            # The backend map is a top-level key. A nested section or a line
            # inside a block scalar that happens to read `backends:` is not it,
            # and taking that one puts every span in the wrong place.
            return index, 0
        if fallback is None:
            fallback = (index, len(match.group("indent")))
    if fallback is not None:
        return fallback
    raise KeyError(key)


def entry_span(lines: list[str], name: str) -> tuple[int, int]:
    """First and last line of one backend entry, in half-open text coordinates."""
    base, base_indent = _mapping_start(lines, "backends")
    key_indent = None
    for line in lines[base + 1 :]:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        depth = len(line) - len(stripped)
        if depth <= base_indent:
            break
        key_indent = depth
        break
    key = re.compile(
        rf"^(?P<indent>[ \t]*)(?P<quote>['\"]?)"
        rf"{re.escape(name)}(?P=quote)[ \t]*:"
    )
    start = indent = None
    for index in range(base + 1, len(lines)):
        line = lines[index]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        depth = len(line) - len(stripped)
        if depth <= base_indent:
            break
        if key_indent is not None and depth != key_indent:
            continue
        match = key.match(line)
        if match:
            start, indent = index, depth
            break
    if start is None:
        raise KeyError(name)
    return _span_from(lines, start, indent)


def _span_from(lines: list[str], start: int, indent: int) -> tuple[int, int]:
    """Last line of the block opening at `start` with this indent."""
    end = start
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            # A blank line belongs to no entry, so counting it into the
            # preceding one keeps a deletion of "the entry and its separator"
            # inside the declared region without ever reaching a neighbour's
            # content.
            end = index
            continue
        stripped = line.lstrip()
        depth = len(line) - len(stripped)
        if stripped.startswith("#"):
            if depth <= indent and not _comment_belongs_here(lines, index, indent):
                break
            end = index
            continue
        if depth <= indent:
            break
        end = index
    return start, end


def _comment_belongs_here(lines: list[str], index: int, indent: int) -> bool:
    """A comment at the entry's own indent still belongs to it when the next
    thing we meet is deeper: that is how ruamel reads it, and it keeps the
    comment inside the entry's mapping when it prints the file again."""
    for line in lines[index + 1 :]:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        return len(line) - len(stripped) > indent
    return False


def opcodes(before: str, after: str) -> list[tuple[str, int, int, int, int]]:
    """(tag, i1, i2, j1, j2) with i in input coordinates, j in output."""
    return difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines()).get_opcodes()


def _outside_regions(before: str, after: str, spans, insertion_after):
    """The changed regions that fall outside every declared span."""
    outside = []
    for tag, i1, i2, j1, j2 in opcodes(before, after):
        if tag == "equal":
            continue
        if i1 == i2:  # pure insertion
            ok = any(start <= i1 <= end + 1 for start, end in spans)
            if insertion_after is not None:
                ok = ok or i1 >= insertion_after
        else:
            ok = any(start <= i1 and i2 <= end + 1 for start, end in spans)
        if not ok:
            outside.append((i1, i2, j1, j2))
    return outside


def _outside_message(i1: int, i2: int, spans) -> str:
    described = ", ".join(f"{s + 1}-{e + 1}" for s, e in spans)
    return f"the edit touches input lines {i1 + 1}-{i2 + 1}, outside {described}"


def locality(
    before: str,
    after: str,
    spans: list[tuple[int, int]],
    insertion_after: int | None = None,
    tolerate=None,
) -> list[str]:
    """Problems, in words, when an edit reaches outside the spans it declares.

    Coordinates: `i1`/`i2` index the *input*, which is what the spans describe.
    An insertion (i1 == i2) is local when its position sits inside a span or at
    the declared insertion point.
    """
    return [
        _outside_message(i1, i2, spans)
        for i1, i2, j1, j2 in _outside_regions(before, after, spans, insertion_after)
        if tolerate is None or not tolerate(i1, i2, j1, j2)
    ]


# --- names -----------------------------------------------------------------


def addressable(name: str) -> bool:
    """True when the reader forge uses would read this name back as a key."""
    try:
        parsed = _pyyaml.safe_load(f"{name}: 1\n")
    except Exception:  # noqa: BLE001 - a NUL byte raises something else again
        return False
    return (
        isinstance(parsed, dict)
        and len(parsed) == 1
        and isinstance(next(iter(parsed)), str)
        and next(iter(parsed)) == name
    )


# --- internals -------------------------------------------------------------


def _backends(doc: Document) -> dict:
    if not isinstance(doc.data, dict):
        raise Refused("the YAML root is not a mapping")
    backends = doc.data.get("backends")
    if not isinstance(backends, dict):
        raise Refused("the file has no backends mapping")
    return backends


def _entry(doc: Document, name: str) -> dict:
    backends = _backends(doc)
    if name not in backends:
        raise NotDeclared(f"{name!r} is not declared")
    entry = backends[name]
    if entry is None:
        # `beta:` with no body is a declared backend with nothing in it yet;
        # giving it a field is the request, not a shape problem.
        backends[name] = {}
        entry = backends[name]
    if not isinstance(entry, dict):
        raise Refused(f"{name!r} is not a mapping")
    return entry


def _opens_block_scalar(line: str) -> bool:
    """Whether this line starts a `|`/`>` block whose body follows."""
    stripped = line.split("#", 1)[0].rstrip()
    if not stripped.endswith(("|", ">", "|-", ">-", "|+", ">+")):
        return False
    return ":" in stripped


def _comments_in_text(text: str) -> list[str]:
    """Comment text visible in the file, line by line.

    Text, not the parsed graph: the same comment is reachable through both
    `ca.comment` and `ca.items`, and those storages swap between a load and a
    load of the dump, which would make every edit look lossy. A `#` only starts
    a comment at the start of a line or after whitespace, so a `#` inside a URL
    is not one.
    """
    found: list[str] = []
    block_indent = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if block_indent is not None:
            if line.strip() == "" or indent > block_indent:
                # Inside a literal/folded scalar: `#` is data here.
                continue
            block_indent = None
        if _opens_block_scalar(line):
            block_indent = indent
            continue
        quote = None
        for index, char in enumerate(line):
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
                continue
            if char == "#" and (index == 0 or line[index - 1] in " \t"):
                found.append(line[index:].strip())
                break
    return found


def _comments_in_span(text: str, span: tuple[int, int]) -> Counter:
    """Comments written inside the entry's own lines; they go with the entry.

    Counted: an identical comment also written outside the entry is a second
    comment, and only the inside one is forgiven.
    """
    lines = text.splitlines()[span[0] : span[1] + 1]
    return Counter(_comments_in_text("\n".join(lines)))


def _only_backends_moved(before: str, after: str, spans) -> bool:
    """Whether the only parsed difference between the two texts is inside the
    entries this mutation declared."""
    try:
        left = _pyyaml.safe_load(before)
        right = _pyyaml.safe_load(after)
    except Exception:  # noqa: BLE001 - unparseable text is not vouched for
        return False
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_backends = left.get("backends")
    right_backends = right.get("backends")
    if not isinstance(left_backends, dict) or not isinstance(right_backends, dict):
        return False
    outside = {key: value for key, value in left.items() if key != "backends"}
    if _changed_paths(outside, {k: v for k, v in right.items() if k != "backends"}):
        return False
    lines = before.splitlines()
    try:
        base_index, base_indent = _mapping_start(lines, "backends")
    except KeyError:
        return False
    key_column = None
    for following in lines[base_index + 1 :]:
        if following.strip() and not following.lstrip().startswith("#"):
            key_column = len(following) - len(following.lstrip())
            break
    declared = set()
    for start, end in spans:
        for index in range(max(start, 0), min(end + 1, len(lines))):
            line = lines[index]
            indent = len(line) - len(line.lstrip())
            if key_column is None or indent != key_column:
                continue
            for name in left_backends:
                if re.match(rf"^[ \t]*['\"]?{re.escape(name)}['\"]?[ \t]*:", line):
                    declared.add(name)
    changed = _changed_paths(left_backends, right_backends)
    for path in changed:
        if not path or path[0] not in declared:
            return False
    return True


def _is_merge_source(backends, name: str, entry) -> bool:
    """Whether another entry merges from this one.

    ruamel keeps merge indexes into the source mapping, so deleting a key that a
    sibling merges through raises `KeyError` and leaves the source partly edited.
    The anchor name is the only handle the parsed document keeps.
    """
    anchor = getattr(entry, "anchor", None)
    if not getattr(anchor, "value", None):
        return False
    own = {key for key in entry if key != "<<"}
    for other_name, other in backends.items():
        if other_name == name or not isinstance(other, dict):
            continue
        for merged in getattr(other, "_yaml_merge", None) or ():
            if isinstance(merged, dict) and set(merged) <= own:
                return True
    return False


def _parsed(text: str):
    """The data a fresh load of this text holds."""
    return Document(text).data


def _own_line(doc, name: str, field_name: str) -> bool:
    """Whether this entry's own text has a line for the field.

    ruamel's ``keys()`` shows merged keys as if they were the entry's own, so the
    parsed mapping cannot tell an inherited `default` from a written one. The
    entry's source can, and that is what decides whether deleting is meaningful.
    """
    try:
        start, end = doc.span(name)
    except (NotLocatable, KeyError):
        return False
    lines = doc.source.splitlines()[start : end + 1]
    pattern = re.compile(rf"^[ \t]*(?:['\"]?){re.escape(field_name)}(?:['\"]?)[ \t]*:")
    return any(pattern.match(line) for line in lines)


def _same_data(left, right, _seen=None) -> bool:
    """Type-strict comparison of two parsed documents.

    Text equality is not enough: a block scalar's indentation, a quoted number
    and a plain one all read back differently while looking similar. This is the
    check that the dump still means what the mutation put in the document.
    """
    if _seen is None:
        _seen = {}
    key = (id(left), id(right))
    if key in _seen:
        # An alias graph reaches the same pair many times; without this the
        # comparison is exponential in the alias depth.
        return _seen[key]
    _seen[key] = True  # a cycle that comes back here compares equal by premise
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        result = set(left) == set(right) and all(
            _same_data(left[name], right[name], _seen) for name in left
        )
        _seen[key] = result
        return result
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        result = len(left) == len(right) and all(
            _same_data(a, b, _seen) for a, b in zip(left, right, strict=False)
        )
        _seen[key] = result
        return result
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    left_tag = getattr(left, "tag", None)
    right_tag = getattr(right, "tag", None)
    if left_tag is not None or right_tag is not None:
        # ruamel wraps an explicitly tagged scalar in TaggedScalar, whose __eq__
        # is identity; the tag and the text are what a reader sees.
        left_value = getattr(left, "value", left)
        right_value = getattr(right, "value", right)
        return str(left_tag) == str(right_tag) and _same_data(left_value, right_value, _seen)
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and str(left) == str(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, float) != isinstance(right, float):
            return False
        return left == right
    return left == right


def _mixed_steps(doc) -> bool:
    """Whether the text indents its mappings by more than one step."""
    steps = set()
    for key, value in getattr(doc.data, "items", lambda: [])():
        if not isinstance(value, dict):
            continue
        position = getattr(getattr(doc.data, "lc", None), "data", {}).get(key)
        if not position or not value:
            continue
        first = next(iter(value))
        child = getattr(getattr(getattr(value, "lc", None), "data", None), "get", lambda _k: None)(first)
        if child:
            steps.add(int(child[1]) - int(position[1]))
    return len(steps) > 1


def _same_indents(left, right) -> bool:
    """Whether two line ranges start in the same columns."""
    return [_indent_of(line) for line in left] == [_indent_of(line) for line in right]


def _indent_of(line: str) -> int:
    """How far this line starts in: a re-indented line is not the same line."""
    return len(line) - len(line.lstrip(" \t"))


def _collapsed(text: str) -> str:
    """The text with runs of spaces inside each line reduced to one.

    Leading whitespace is kept: a region that only moved sideways is a rewrite
    of lines the mutation did not declare, and calling that "the same words
    re-spaced" let a decoy section be re-indented under a note claiming the
    write matched the file's style.
    """
    out = []
    for line in text.splitlines():
        body = line.lstrip(" \t")
        if not body:
            out.append("")
            continue
        out.append(" " * (len(line) - len(body)) + " ".join(body.split()))
    return "\n".join(out)


class _TooManyPaths(Exception):
    """Raised when enumerating every differing path would take exponential time."""


def _relative_changes(left, right, stack=None, results=None, work=None, depth: int = 0) -> tuple:
    """Where two parsed values differ, as tuples of keys/indices relative to them.

    A shared sub-object reached by many keys yields one path per key, so the walk
    produces them exponentially. `work` bounds how many are ever put in a list,
    and it is charged as each one is appended rather than after the list is
    built: charging late let a fan-out of aliases allocate gigabytes first.
    """
    if stack is None:
        stack = set()
    if results is None:
        results = {}
    if work is None:
        work = [200_000]

    def charge(count: int) -> None:
        work[0] -= count
        if work[0] < 0:
            raise _TooManyPaths

    if depth > 64:
        # Deeper than any configuration; the walk would recurse to the limit.
        raise _TooManyPaths
    key = (id(left), id(right))
    if key in results:
        cached = results[key]
        charge(len(cached))
        return cached
    if key in stack:
        return ()  # reached the pair we are already comparing: nothing new
    stack.add(key)
    found: list[tuple] = []
    if isinstance(left, dict) and isinstance(right, dict):
        for name in sorted(set(left) | set(right), key=str):
            if name not in left or name not in right:
                found.append((name,))
                charge(1)
            else:
                for suffix in _relative_changes(
                    left[name], right[name], stack, results, work, depth + 1
                ):
                    found.append((name,) + suffix)
                    charge(1)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            found = [()]
            charge(1)
        else:
            for index, (one, two) in enumerate(zip(left, right, strict=False)):
                for suffix in _relative_changes(one, two, stack, results, work, depth + 1):
                    found.append((index,) + suffix)
                    charge(1)
    elif not _reading_equal(left, right):
        found = [()]
        charge(1)
    stack.discard(key)
    results[key] = tuple(found)
    return results[key]


def _changed_paths(left, right, path: tuple = (), limit: int = 10_000) -> list[tuple]:
    """The paths, from the root given here, where two parsed values differ.

    Reporting every path of a shared structure is not a useful answer, and the
    count is exponential in the sharing depth; past `limit` paths the caller is
    told the question cannot be answered instead of being given a few of them.
    The limit counts reported paths, so it does not move with depth.
    """
    suffixes = _relative_changes(left, right)
    if len(suffixes) > limit:
        raise _TooManyPaths
    return [path + suffix for suffix in suffixes]


def _reading_equal(left, right, _seen=None, _depth: int = 0) -> bool:
    """Whether two parsed values mean the same thing, type-strictly.

    `True == 1` and `1 == 1.0` in Python, and ruamel's nodes carry tags that
    equality ignores, so the comparison a reader would make is written out. The
    memo matters: an alias graph reaches the same pair many times, and without
    it the walk is exponential in the alias depth.
    """
    if _depth > 200:  # deeper than any config; refuse instead of crashing
        return False
    if type(left) is not type(right):
        return False
    if _seen is None:
        _seen = {}
    key = (id(left), id(right))
    if key in _seen:
        return _seen[key]
    _seen[key] = True  # a cycle that comes back here compares equal by premise
    if isinstance(left, dict):
        result = set(left) == set(right) and all(
            _reading_equal(left[name], right[name], _seen, _depth + 1) for name in left
        )
    elif isinstance(left, list):
        result = len(left) == len(right) and all(
            _reading_equal(a, b, _seen, _depth + 1) for a, b in zip(left, right, strict=False)
        )
    else:
        result = bool(left == right)
    _seen[key] = result
    return result


def _region_same_reading(before: str, after: str) -> bool:
    """Whether the two texts read as the same data for anyone loading this file.

    The oracle is the reader (PyYAML), not the writer's model: a construct the
    model drops but the reader interprets - `!!float 1e3` re-emitted as `1e3`
    reads as a string - must not pass as "only re-spaced". A fragment that
    cannot stand alone as a document (it ends at a shallower indent than it
    starts) cannot be vouched for, so it is not tolerated.
    """
    try:
        left = _pyyaml.safe_load(textwrap.dedent(before))
        right = _pyyaml.safe_load(textwrap.dedent(after))
    except Exception:  # noqa: BLE001 - an unparseable fragment is not vouched for
        return False
    try:
        return _reading_equal(left, right)
    except RecursionError:  # a self-referential fragment cannot be vouched for
        return False


def _backend_names(text: str) -> list[str]:
    try:
        document = Document(text)
    except Exception:  # noqa: BLE001 - unparseable text has no names
        return []
    block = document.data.get("backends") if isinstance(document.data, dict) else None
    return list(block) if isinstance(block, dict) else []


def _comment_owners(text: str) -> dict[tuple[str, int], str | None]:
    """Which entry each comment reads as belonging to.

    A comment sits in the gap above an entry as often as inside one, and that is
    exactly the comment a deletion can hand to the next entry, so ownership is
    the next entry key below it (falling back to the entry that contains it).
    """
    lines = text.splitlines()
    try:
        document = Document(text)
    except Exception:  # noqa: BLE001 - a file we cannot parse keeps no promises
        return {}
    names = list(document.data.get("backends") or {}) if isinstance(document.data, dict) else []
    spans = {}
    for name in names:
        try:
            spans[name] = document.span(name)
        except Refused:
            continue
    owners: dict[str, str | None] = {}
    for index, line in enumerate(lines):
        for comment in _comments_in_text(line):
            # The same text can appear above two different entries; the
            # occurrence number is what tells those two comments apart.
            key = (comment, sum(1 for existing in owners if existing[0] == comment))
            containing = next(
                (name for name, (start, end) in spans.items() if start <= index <= end),
                None,
            )
            if containing is not None:
                owners[key] = containing
                continue
            below = next(
                ((name, span[0]) for name, span in spans.items() if span[0] > index),
                None,
            )
            owners[key] = below[0] if below else None
    return owners


def _reassigned_comments(before_text: str, after_text: str) -> list[str]:
    """Comments that survived but now read as a different entry's.

    Text-level presence is not enough: deleting an entry can leave its comment
    above the next entry, where it silently becomes that entry's note.
    """

    def by_comment(text: str) -> dict[str, Counter]:
        owners: dict[str, Counter] = {}
        for (comment, _occurrence), owner in _comment_owners(text).items():
            owners.setdefault(comment, Counter())[owner] += 1
        return owners

    before = by_comment(before_text)
    after = by_comment(after_text)
    moved = {
        comment
        for comment, owners in before.items()
        # A comment whose count fell was deleted, and `_lost_comments` reports
        # that; calling the survivor a move as well would double-report it.
        if comment in after
        and sum(owners.values()) == sum(after[comment].values())
        and after[comment] != owners
    }
    return sorted(moved)


def _lost_comments(before_text: str, after_text: str) -> list[str]:
    """Comments that a write would delete outright.

    ruamel attaches a comment that follows a block to that block's last node,
    so deleting the block takes the comment with it even when the comment reads
    as the next entry's. Refusing is the only non-lossy answer.

    Counted, not deduplicated: two entries carrying the same comment text are
    two comments, and losing one of them is still a loss.
    """
    before = Counter(_comments_in_text(before_text))
    after = Counter(_comments_in_text(after_text))
    lost = [text for text, count in (before - after).items() for _ in range(count)]
    return sorted(lost)


def _has_merge_keys(node) -> bool:
    """Whether any mapping in the parsed document pulls keys in through `<<`.

    Read from the model, not from the text: a comment, a URL or a quoted scalar
    that happens to contain `<<` is not a merge, and treating it as one would
    switch the read-back guard off for the whole file.
    """
    if isinstance(node, Mapping):
        if getattr(node, "merge", None):
            return True
        return any(_has_merge_keys(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_merge_keys(value) for value in node)
    return False


def _transactional(func):
    """Run a mutator so that a refusal leaves the document as it was."""

    @functools.wraps(func)
    def wrapper(doc, *args, **kwargs):
        with _atomic(doc):
            return func(doc, *args, **kwargs)

    return wrapper


@contextmanager
def _atomic(doc: Document):
    """Undo the in-memory mutation when the command refuses.

    A refusal raises before anything is written, but callers such as `sync`
    keep using the same document afterwards; a half-applied mutation would then
    be written by the next successful command. Reloading the data is not enough:
    the notes and the changed flag are part of the view a caller reads next.
    """
    notes = list(doc.notes)
    changed = doc.changed
    try:
        yield
    except Exception:
        doc.reload()
        doc.notes = notes
        doc.changed = changed
        raise


def _finish(
    doc: Document, before: str, spans, insertion_after=None, allow_lost: set[str] | None = None
) -> bool:
    """True when the data changed and the write stays where it was asked to.

    `before` is the dump taken before the mutation, so a command that found
    nothing to do writes nothing even when the file's formatting differs from
    what the loader would print. `doc.source` is what the locality check must
    compare against: it is the text on disk, so a re-indent of a neighbour shows
    up as a change outside the declared span instead of hiding behind the
    normalised dump.
    """
    after = doc.dumped()
    if after == before:
        return False
    # The writer emits one indent for the whole file. When the text uses more
    # than one step, the edit reflows the others; saying so is the difference
    # between a reported side effect and a note claiming the file's own style
    # was matched.
    if (
        _mixed_steps(doc)
        and difflib.SequenceMatcher(a=doc.source.splitlines(), b=before.splitlines()).ratio() < 1.0
    ):
        doc.notes.append(
            "this file indents its mappings by more than one step, so the rest "
            "of it was re-indented to a single step as well"
        )
    before_lines = doc.source.splitlines()
    after_lines = after.splitlines()
    tolerated = []

    def _inside_span_any(index: int) -> bool:
        return any(start <= index <= end for start, end in spans)

    def _inside_span(index: int) -> bool:
        """Whether this input line is one the mutation was allowed to change.

        `entry_span` returns the last line of the entry, so the range is closed
        at both ends; matching it here keeps the two conventions agreeing.
        """
        return any(start <= index <= end for start, end in spans)

    def _tolerated(i1: int, i2: int, j1: int, j2: int) -> bool:
        before_text = "\n".join(before_lines[i1:i2])
        after_text = "\n".join(after_lines[j1:j2])
        if (i2 - i1) == (j2 - j1) and any(_inside_span(i) for i in range(i1, i2)):
            # The diff can pair an intended change with a neighbour that only
            # moved a comma, so each line is judged on its own here.
            pairs = zip(before_lines[i1:i2], after_lines[j1:j2], strict=False)
            unchanged = all(
                _inside_span(i1 + offset)
                or _collapsed(left) == _collapsed(right)
                or (_indent_of(left) == _indent_of(right) and _region_same_reading(left, right))
                for offset, (left, right) in enumerate(pairs)
            )
            if unchanged:
                tolerated.append((i1, i2))
            return unchanged
        if _collapsed(before_text) == _collapsed(after_text):
            tolerated.append((i1, i2))
            return True
        if (i2 - i1) != (j2 - j1):
            # A deletion brings a differing line count. What it may take with it
            # is the lines the mutation declared; everything else has to read
            # the same as the text that replaces them.
            kept = "\n".join(
                line
                for index, line in enumerate(before_lines[i1:i2], start=i1)
                if not _inside_span(index)
            )
            if (kept.strip() == "" and after_text.strip() == "") or _region_same_reading(
                kept, after_text
            ):
                tolerated.append((i1, i2))
                return True
            return False
        # A flow collection can be re-spaced (`[a,b]` -> `[a, b]`) without any
        # value changing; compare what the region reads as, not its characters.
        # A line that moved sideways is a rewrite of a line nobody declared,
        # even when the values are identical.
        if _same_indents(before_lines[i1:i2], after_lines[j1:j2]) and _region_same_reading(
            before_text, after_text
        ):
            tolerated.append((i1, i2))
            return True
        return False

    problems = locality(doc.source, after, spans, insertion_after, tolerate=_tolerated)
    if problems:
        try:
            fallback = _only_backends_moved(doc.source, after, spans)
        except _TooManyPaths:
            # Two different refusals used to share one sentence: a resource
            # limit (too many differing paths to enumerate) read as "the write
            # left the entry", which hid the real cause from the operator.
            problems.append(
                "this document differs in more places than the comparison walk can "
                "enumerate, so it is refused before a partial answer"
            )
            fallback = False
        if fallback:
            # A deletion re-indents the neighbours ruamel keeps in the same block,
            # which the hunk-level view cannot see as one move. If nothing but the
            # backends map differs, the regions are that re-indent.
            problems = []
            tolerated.append((-1, -1))
    reindented = len(tolerated)
    if reindented:
        doc.notes.append(
            f"the write re-formatted {reindented} region(s) outside the entry "
            f"(indentation or spacing) to match the file's style; the settings "
            f"and comments are unchanged"
        )

    # A document that merges through `<<` keeps the sibling views as a snapshot
    # taken at load time, so a write into an anchor's own entry makes the live
    # model look different from any re-parse. There the guard checks structure
    # instead of value identity; the locality checks above still hold.
    merges = _has_merge_keys(doc.data)
    if merges:
        try:
            fresh = _parsed(after)
        except Exception:  # noqa: BLE001 - unparseable output is not vouched for
            fresh = None
        keys_match = isinstance(fresh, dict) and set(fresh) == set(doc.data)
        if not keys_match:
            doc.reload()
            raise Refused(
                "the write would not read back as the data it holds (the merged "
                "document loses a top-level key), so the file is left alone"
            )
    elif not _same_data(doc.data, Document(after).data):
        # The dump no longer reads back as the data this document holds: a block
        # scalar lost its indentation, a tag or a quoted value changed type. The
        # text checks cannot see that. Both sides are parsed by the same loader,
        # so a `on:`/`yes:` key that ruamel keeps as text is not counted.
        # This branch only runs when `merges` is False, so the merge wording
        # cannot apply here; a document that merges was answered above.
        problems.append(
            "the write would not read back as the data it holds (a value's "
            "type or text would change), so the file is left alone"
        )
    forgiven = Counter(allow_lost or ())
    lost = []
    for text in _lost_comments(doc.source, after):
        if forgiven[text] > 0:
            forgiven[text] -= 1
            continue
        lost.append(text)
    # Only a removal can hand a comment to a neighbour; a rename legitimately
    # changes which name a comment sits above.
    before_names = _backend_names(doc.source)
    removal = len(_backend_names(after)) < len(before_names)
    moved = _reassigned_comments(doc.source, after) if removal else []
    if lost:
        problems.append(
            "the write would delete comment(s) " + ", ".join(repr(text.strip()) for text in lost)
        )
    if doc.mixed_endings and after != doc.source:
        problems.append(
            "the file mixes CRLF and LF line endings; rewriting it would convert "
            "the whole file, so it is refused"
        )
    if moved:
        problems.append(
            "the write would move comment(s) to another entry: "
            + ", ".join(repr(text) for text in moved)
        )
    if problems:
        doc.reload()
        raise Refused("; ".join(problems))
    # The document is now the text that mutation produced, so a caller that
    # applies several mutations before writing once gets correct spans.
    doc.source = after
    doc.changed = True
    return True


def _aliased(doc: Document, name: str) -> str | None:
    """The other entry this one shares its mapping with, when YAML aliases it.

    `beta: *shared` is the same object as the anchor's owner, so writing a
    field into one writes it into both. That is never what a caller asking for
    one backend meant.
    """
    backends = _backends(doc)
    entry = backends.get(name)
    for other_name, other in backends.items():
        if other_name != name and other is entry:
            return other_name
    return None


def _flag_comment(entry, field_name: str) -> str | None:
    ca = getattr(entry, "ca", None)
    if ca is None or not ca.items:
        return None
    for item in ca.items.get(field_name, []) or []:
        if item is not None:
            return item.value.strip()
    return None


def _tokens(seq) -> list[str]:
    out = []
    for item in seq or []:
        if isinstance(item, list):
            out.extend(t.value for t in item if t is not None)
        elif item is not None:
            out.append(item.value)
    return out


def _shared_anchor_hosts(doc: Document, name: str) -> set[str]:
    # Through `doc.span`, so a file this editor cannot address raises the same
    # refusal the rest of the module raises instead of a bare KeyError.
    lines = doc.source.splitlines()
    start, end = doc.span(name)
    body = "\n".join(lines[start : end + 1])
    outside = "\n".join(lines[:start] + lines[end + 1 :])
    return {
        anchor
        for anchor in re.findall(r"&(\w+)", body)
        if re.search(rf"\*{re.escape(anchor)}\b", outside)
    }


def _key_line_comment(doc: Document, name: str) -> str | None:
    entry = doc.data["backends"][name]
    ca = getattr(entry, "ca", None)
    if ca is None:
        return None
    found = [text for text in _tokens(ca.comment) if text]
    return found[0].strip() if found else None


# --- mutations -------------------------------------------------------------


@_transactional
def add_backend(doc: Document, name: str, spec: dict) -> bool:
    if not addressable(name):
        raise Refused(f"{name!r} would not read back as a string key")
    backends = _backends(doc)
    if name in backends:
        raise Refused(f"{name!r} is already declared")
    before = doc.dumped()
    entry = {
        "type": "api",
        "format": spec["format"],
        "base_url": _string(spec["base_url"]),
        "api_key_env": spec["key_env"],
        "model": _string(spec["model"]),
        "max_tokens": spec["max_tokens"],
        "timeout_s": spec["timeout_s"],
        "stream": False,
    }
    if spec.get("headers"):
        entry["headers"] = {key: _string(str(value)) for key, value in spec["headers"].items()}
    # The column of an existing key is the indent the new one should use; the
    # new key has no position of its own until the document is dumped.
    first = next(iter(backends), None)
    column = backends.lc.data[first][1] if first is not None else 0
    backends[name] = entry
    backends.yaml_set_comment_before_after_key(
        name,
        before=f"added by forge-provider.py {__import__('datetime').date.today().isoformat()}",
        indent=column,
    )
    last = max((doc.span(existing)[1] for existing in backends if existing != name), default=0)
    return _finish(doc, before, [(last, last)], insertion_after=last + 1)


def set_field(doc: Document, name: str, field_name: str, value) -> bool:
    """Assign one field. A value that is already there writes nothing: the
    dump comparison in `_finish` decides that, not a second check here."""
    entry = _entry(doc, name)
    other = _aliased(doc, name)
    if other is not None:
        raise Refused(
            f"{name!r} shares its mapping with {other!r} through an alias; "
            f"writing {field_name!r} would change both"
        )
    with _atomic(doc):
        before = doc.dumped()
        # Locating the entry can raise for a shape the writer cannot address
        # (a flow mapping). Do it before the assignment so a refusal cannot
        # leave the value behind in the model.
        span = doc.span(name)
        entry[field_name] = _string(value) if isinstance(value, str) else value
        return _finish(doc, before, [span])


def _stage_fields(doc: Document, name: str, values: dict, add_missing: bool = False) -> dict:
    """Render the fields that actually change, refusing an aliased entry first."""
    entry = _entry(doc, name)
    other = _aliased(doc, name)
    if other is not None:
        raise Refused(
            f"{name!r} shares its mapping with {other!r} through an alias; "
            f"writing fields would change both"
        )
    staged = {}
    for field_name, value in values.items():
        if value is None:
            continue
        if field_name not in entry and not add_missing:
            continue
        if _equivalent(entry.get(field_name), value):
            continue
        staged[field_name] = _string(value) if isinstance(value, str) else value
    return staged


def _stage_headers(doc: Document, name: str, headers: dict) -> dict | None:
    entry = _entry(doc, name)
    existing = entry.get("headers")
    if existing is not None and not isinstance(existing, Mapping):
        raise Refused(
            f"{name!r} has a `headers:` value that is not a mapping "
            f"({type(existing).__name__}); fix it by hand first"
        )
    other = _aliased(doc, name)
    if other is not None:
        raise Refused(
            f"{name!r} shares its mapping with {other!r} through an alias; "
            f"writing headers would change both"
        )
    if dict(existing or {}) == dict(headers):
        return None
    return {key: _string(str(value)) for key, value in headers.items()}


def dropped_headers(doc: Document, name: str, headers: dict) -> list[str]:
    """Header names the new mapping would remove. Names only, never values:
    a header often carries a credential, and the old value used to be printed."""
    entry = doc.data["backends"][name] if isinstance(doc.data, dict) else {}
    existing = entry.get("headers") if isinstance(entry, Mapping) else None
    if not isinstance(existing, Mapping):
        return []
    return [str(key) for key in existing if key not in headers]


@_transactional
def set_fields(doc: Document, name: str, values: dict, add_missing: bool = False) -> list[str]:
    """Align several fields in one write. Returns the fields that changed.

    `sync` used to call the single-field mutation once per field, so a refusal
    halfway through left the backend half-aligned on disk.
    """
    staged = _stage_fields(doc, name, values, add_missing=add_missing)
    if not staged:
        return []
    entry = _entry(doc, name)
    before = doc.dumped()
    span = doc.span(name)
    for field_name, rendered in staged.items():
        entry[field_name] = rendered
    _finish(doc, before, [span])
    return list(staged)


@_transactional
def set_headers(doc: Document, name: str, headers: dict) -> bool:
    staged = _stage_headers(doc, name, headers)
    if staged is None:
        return False
    entry = _entry(doc, name)
    before = doc.dumped()
    span = doc.span(name)
    entry["headers"] = staged
    return _finish(doc, before, [span])


def set_fields_and_headers(
    doc: Document, name: str, values: dict, headers: dict | None, add_missing: bool = False
) -> tuple[list[str], bool]:
    """Fields and headers in one document write: `set --header` does both."""
    fields = _stage_fields(doc, name, values, add_missing=add_missing)
    staged_headers = _stage_headers(doc, name, headers) if headers is not None else None
    if not fields and staged_headers is None:
        return [], False
    entry = _entry(doc, name)
    before = doc.dumped()
    span = doc.span(name)
    for field_name, rendered in fields.items():
        entry[field_name] = rendered
    if staged_headers is not None:
        entry["headers"] = staged_headers
    _finish(doc, before, [span])
    return list(fields), staged_headers is not None


def set_default(doc: Document, name: str) -> bool:
    """Set the default flag, leaving the document untouched when refused."""
    with _atomic(doc):
        return _set_default(doc, name)


def _set_default(doc: Document, name: str) -> bool:
    entry = _entry(doc, name)
    other = _aliased(doc, name)
    if other is not None:
        raise Refused(
            f"{name!r} shares its mapping with {other!r} through an alias, "
            f"so setting the default would change both"
        )
    backends = _backends(doc)
    before = doc.dumped()
    cleared = []
    dropped_comments = set()
    for other_name, other in backends.items():
        if other_name == name or not isinstance(other, dict):
            continue
        if "default" in other:
            dropped = _flag_comment(other, "default")
            if _is_merge_source(backends, other_name, other):
                raise Refused(
                    f"{other_name!r} is the anchor another entry merges (`<<`), so "
                    f"removing its `default` would rewrite that entry; edit the anchor "
                    f"instead"
                )
            if not _own_line(doc, other_name, "default"):
                raise Refused(
                    "'default' on that entry comes from a merge key (`<<`); edit the anchor instead"
                )
            try:
                del other["default"]
            except KeyError as exc:
                # ruamel tracks merge indexes into this mapping; removing a key
                # another entry merges through raises instead of renumbering.
                raise Refused(
                    "'default' on that entry is shared through an anchor (`&`); edit the anchor instead"
                ) from exc
            cleared.append(other_name)
            doc.notes.append(f"cleared the default flag on {other_name!r}")
            if dropped:
                dropped_comments.add(dropped)
                doc.notes.append(f"its comment ({dropped!r}) went with the flag")
    entry["default"] = True
    spans = [doc.span(name)] + [doc.span(other) for other in cleared]
    return _finish(doc, before, spans, allow_lost=dropped_comments)


@_transactional
def rename_backend(doc: Document, old: str, new: str) -> bool:
    if not addressable(new):
        raise Refused(f"{new!r} would not read back as a string key")
    backends = _backends(doc)
    if old not in backends:
        raise NotDeclared(f"{old!r} is not declared")
    if new in backends:
        raise Refused(f"{new!r} is already declared")
    before = doc.dumped()
    values = {key: backends[key] for key in backends}
    keys = list(backends)
    for key in keys:
        del backends[key]
    for key in keys:
        backends[new if key == old else key] = values[key]
    return _finish(doc, before, [doc.span(old)])


@_transactional
def remove_backend(doc: Document, name: str, guards: Guards | None = None) -> bool:
    guards = guards or Guards()
    backends = _backends(doc)
    _entry(doc, name)
    if len(backends) == 1:
        raise Refused("would leave no backends; delete the file's block by hand if intended")
    if backends[name].get("default", False):
        raise Refused("is the review default; move the default first")
    shared = _shared_anchor_hosts(doc, name)
    if shared:
        raise Refused(f"{name!r} hosts anchor(s) {sorted(shared)} a survivor aliases")

    expected = {key: value for key, value in doc.data.items() if key != "backends"}
    expected["backends"] = {key: value for key, value in backends.items() if key != name}
    if guards.resolve is not None:
        before_id, before_error = guards.resolve(dict(backends), guards.user_backends)
        after_id, after_error = guards.resolve(expected["backends"], guards.user_backends)
        if after_error:
            raise Refused(f"cannot determine the effective backend ({after_error})")
        if before_error and not after_error:
            # The entry being removed is what made the resolution fail; taking
            # it out is the repair, so this is the one direction allowed.
            doc.notes.append(
                f"the effective backend could not be resolved before the removal "
                f"({before_error}); it resolves afterwards"
            )
        if before_id is not None and after_id is not None and before_id != after_id:
            if before_id[0] != after_id[0]:
                raise Refused(
                    f"would change the effective backend from "
                    f"{before_id[0]!r} to {after_id[0]!r}; "
                    f"mark the intended default first"
                )
            moved = [
                f"{field} {old!r} -> {new!r}"
                for field, old, new in zip(guards.fields, before_id, after_id, strict=False)
                if old != new
            ]
            if not moved:
                # A caller that passes no field list still gets a sentence.
                raise Refused(
                    f"would change the effective backend from "
                    f"{before_id[0]!r} to {after_id[0]!r}; "
                    f"mark the intended default first"
                )
            raise Refused(
                "would change the effective backend "
                + ", ".join(moved)
                + "; mark the intended default first"
            )

    comment = _key_line_comment(doc, name)
    span = doc.span(name)
    # A comment written inside the entry goes with it; one that sits outside
    # the entry is a loss the caller has to be told about, so it stays refused.
    inside = _comments_in_span(doc.source, span)
    before = doc.dumped()
    del backends[name]
    changed = _finish(doc, before, [span], allow_lost=inside)
    if comment:
        doc.notes.append(f"the comment on {name!r} ({comment!r}) did not survive")
    return changed

#!/usr/bin/env python3
"""Manage forge review backends across every config on this machine.

A new model shows up, it is cheap, and trying it means adding the same nine
lines to the user-level config plus one gate.yaml per repo per worktree --
thirty files here. The one you miss keeps answering with the old backend
until some unrelated review fails.

    forge-provider.py list
    forge-provider.py add glm-flash \
        --base-url https://open.bigmodel.cn/api/paas/v4 \
        --model glm-4.7-flash --format openai \
        --key-pass personal/api/glm --key-env GLM_API_KEY \
 --header x-omniroute-no-memory:1
    forge-provider.py default glm-flash
    forge-provider.py set review-default --base-url https://new.host/anthropic
    forge-provider.py rename old-name new-name
    forge-provider.py remove glm-flash        # refuses if it is still the default
    forge-provider.py trust [--fix]
    forge-provider.py rollback

Add --dry-run to any writing command to see the plan first. Every touched file
is backed up as <name>.bak-provider-<timestamp>; rollback restores the newest.

No command writes until the endpoint has answered a real request, so a wrong
URL or a dead key fails once instead of thirty times.

Backend names describe the role, not the vendor -- `review-default` rather
than the model behind it -- so switching suppliers changes a base_url and
leaves every key name alone.

Trust: forge hashes the credential fields of a backends block and drops a
block whose hash moved, printing one line to stderr and quietly reviewing on
whatever else resolves. The writing commands re-seal the files they wrote, and
only those -- re-sealing a file this run did not touch would authorize an edit
nobody read. Two commands do not seal what they touch: `rollback` restores bytes that came
from a file named like a backup and compares them against the record instead of
authorizing them, and `trust --fix` authorizes the files an operator asked it
to repair without rewriting them. A file that was not already authorized is not
edited at all: the re-seal covers every field, so a routine change would
otherwise bless credentials nobody looked at.

Known limits, all reproduced rather than assumed:

  - A crash partway through the fleet leaves earlier files written and later
    ones not. Values are validated before the first write, so the realistic
    trigger is a permission error, not a bad argument.
  - `rollback` restores the newest backup per file. Two separate runs mean
    two stamps; rolling back the second does not undo the first, and mixing
    them yields a combination that never existed. Check `list` afterwards.
  - The probe uses one file's credentials. Configs that disagree about which
    env var holds the key get a warning, not thirty probes.
  - Edits go through `code_forge.config_edit`, which parses the file, mutates
    the mapping and prints it again, so quoted keys, inline mappings, anchors,
    long scalars and CRLF files are all edited in place. What is refused, with
    the reason named: a file that mixes CRLF and LF line endings (any write
    would convert the whole file); a whole `backends:` block written on one line, or a
    backend behind a block alias, which this editor cannot address; a write that would reach outside
    the entry it names; a removal that would hand a comment to another entry; a
    write into an entry that shares its mapping through an alias; and a
    `headers:` value that is not a mapping.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

USER_CONFIG = pathlib.Path.home() / ".config/code-forge/config.yaml"
SEARCH_ROOT = pathlib.Path.home()
_USER_LAYER_CACHE: dict = {}


def _ensure_src_on_path():
    """Put forge's package ahead of anything else on sys.path, once per run.

    Every helper that needs `code_forge` used to insert the path again, so one
    sweep grew sys.path by one entry per file and re-resolved imports each
    time.
    """
    target = str(pathlib.Path(__file__).resolve().parents[1] / "src")
    if target not in sys.path:
        sys.path.insert(0, target)


def user_layer_paths():
    """Every path forge itself reads as the user-level layer.

    `USER_CONFIG` is where this tool writes. forge also still reads the
    pre-XDG `~/.code-forge/gate.yaml`, so "is this the user layer" has two
    oracles and they disagree on a host that never migrated. Comparing against
    the constant alone made such a file demand repository trust, while the
    review path treated it as implicitly trusted.
    """
    key = (
        os.environ.get("FORGE_CONFIG_DIR", ""),
        os.environ.get("XDG_CONFIG_HOME", ""),
        str(pathlib.Path.home()),
    )
    cached = _USER_LAYER_CACHE.get(key)
    if cached is not None:
        return cached
    paths = {USER_CONFIG}
    try:
        _ensure_src_on_path()
        from code_forge.user_config import user_config_path  # noqa: PLC0415

        found = user_config_path()
    except ImportError:
        found = None
    if found is not None:
        paths.add(pathlib.Path(found))
    _USER_LAYER_CACHE[key] = paths
    return paths


def is_user_config(path) -> bool:
    """True when forge would load `path` as the user-level layer."""
    try:
        resolved = pathlib.Path(path).resolve()
    except OSError:
        resolved = pathlib.Path(path)
    for candidate in user_layer_paths():
        try:
            if candidate.resolve() == resolved:
                return True
        except OSError:
            continue
    return False


# Worktrees do not all live in one place: some sit under a repo's
# .worktrees/, some are parked by agent sessions under .claude/worktrees/.
# A gate.yaml missed here is a checkout that silently keeps reviewing on the
# old backend, so the patterns name each shape rather than guessing a depth.
GATE_GLOBS = [
    ".code-forge/gate.yaml",
    ".claude/.code-forge/gate.yaml",
    "code/*/.code-forge/gate.yaml",
    "code/*/.worktrees/*/.code-forge/gate.yaml",
    "code/*/.claude/worktrees/*/.code-forge/gate.yaml",
]
# Paths that contain a gate.yaml but are not a checkout anyone reviews from:
# container layers, archived review output, scratch dirs from finished jobs.
SKIP_PARTS = {
    ".local",
    ".cache",
    ".venv",
    "node_modules",
    "site-packages",
    ".planning",
    ".git",
    "__pycache__",
}
NO_BACKENDS = "no backends: block"
BACKUP_FMT = ".bak-provider-%Y%m%d-%H%M%S-%f"
# Reasoning models spend output budget on hidden thinking before the first
# visible token. A cap tight enough for a "ping" starves that and comes back
# empty or truncated, which reads as a dead endpoint. Big enough to clear
# thinking, small enough to stay cheap.
PROBE_MAX_TOKENS = 1024

# Reviews send a large prompt and read a long answer back; a small cap
# truncates the verdict JSON and forge then parses zero findings out of a
# response that had plenty. See the max_tokens note in the fleet memory.
DEFAULT_MAX_TOKENS = 131072
DEFAULT_TIMEOUT_S = 2400


def find_configs():
    """Every gate.yaml a review could resolve, plus the user-level config.

    Backups, container layers, and archived review output all contain files
    named gate.yaml; none of them is a config any review reads. Missing a
    real one is the worse error -- it leaves a checkout pointing at the old
    backend with nothing to show that it was skipped.
    """
    # Both oracles: `USER_CONFIG` is the historical constant, while forge reads
    # whatever `user_config_path()` resolves (FORGE_CONFIG_DIR, XDG_CONFIG_HOME).
    # Sweeping only the constant leaves the file forge really loads untouched
    # on a host that moved its user layer.
    found = [path for path in user_layer_paths() if path.is_file()]
    if SEARCH_ROOT.is_dir():
        for pattern in GATE_GLOBS:
            for p in SEARCH_ROOT.glob(pattern):
                if not p.is_file():
                    continue
                if SKIP_PARTS & set(p.parts):
                    continue
                found.append(p)
    return sorted(set(found))


def yaml_scalar(value: str, field: str) -> str:
    """Render a value as a YAML scalar, refusing what would change structure.

    The values here come from a command line and land in a file forge parses
    to decide where a review's credentials go. A model name carrying a newline
    and two spaces writes a second key; one carrying a quote breaks the string
    and the rest of the block reads as whatever follows. Neither is a value
    anyone types by accident, so this rejects rather than escapes -- an
    endpoint or model with a control character in it is a mistake upstream.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field}: empty value")
    if any(c in value for c in "\n\r\t"):
        raise ValueError(f"{field}: contains a newline or tab")
    if "'" in value or '"' in value:
        raise ValueError(f"{field}: contains a quote character")
    if not re.fullmatch(r"[A-Za-z0-9 ._:/@+()\[\]-]+", value):
        raise ValueError(
            f"{field}: {value!r} has characters that are not safe to write unquoted into YAML"
        )
    # Anything left is safe bare EXCEPT the words and shapes YAML resolves to
    # something other than a string. A model literally named `no` parses as
    # False, and `0755` as 493 -- both silently, both far from the edit.
    reserved = {"true", "false", "yes", "no", "on", "off", "null", "none", "~"}
    numeric = re.fullmatch(r"[+-]?(\d[\d_]*(\.\d*)?|\.\d+)([eE][+-]?\d+)?", value)
    if re.fullmatch(r"[A-Za-z0-9._-]+", value) and value.lower() not in reserved and not numeric:
        return value
    return '"' + value + '"'


# RFC 7230 field-name / field-value, same narrowing as
# src/code_forge/backend.py. Duplicated so this script still runs as a
# file, without importing the package, on a host that only has the
# script. Grammar here must stay a subset of what forge will load.
_HEADER_NAME_RE = re.compile(r"[-!#$%&'*+.^_`|~0-9A-Za-z]+")
_HEADER_VALUE_RE = re.compile(r"(?:[\x21-\x7e]+(?:[ \t]+[\x21-\x7e]+)*)?")
_PROTECTED_HEADER_KEYS = frozenset(
    {
        "authorization",
        "x-api-key",
        "content-type",
        "anthropic-version",
        "accept-charset",
        "accept-encoding",
        "access-control-request-headers",
        "access-control-request-method",
        "connection",
        "content-length",
        "cookie",
        "cookie2",
        "date",
        "dnt",
        "expect",
        "host",
        "keep-alive",
        "origin",
        "referer",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "via",
        "x-http-method",
        "x-http-method-override",
        "x-method-override",
    }
)
_PROTECTED_HEADER_PREFIXES = ("proxy-", "sec-")


def parse_headers(pairs):
    """Turn --header NAME:VALUE repeats into a mapping. None if omitted.

    Raises ValueError before any file is touched: a colon-less token, a
    name forge refuses to send, two spellings of the same name, or a
    value yaml_scalar will not write.
    """
    if not pairs:
        return None
    out = {}
    seen = {}
    for raw in pairs:
        if ":" not in raw:
            raise ValueError("header: missing colon (expected NAME:VALUE)")
        name, value = raw.split(":", 1)
        name, value = name.strip(), value.strip()
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"header: {name!r} is not a valid HTTP field name")
        if not _HEADER_VALUE_RE.fullmatch(value):
            raise ValueError(f"header: value for {name!r} is not a valid HTTP field value")
        folded = name.lower()
        if folded in _PROTECTED_HEADER_KEYS or folded.startswith(_PROTECTED_HEADER_PREFIXES):
            raise ValueError(f"header: {name!r} is reserved (credential, framing, or method)")
        if folded in seen:
            raise ValueError(f"header: {name!r} repeats {seen[folded]!r} (names are case-insensitive)")
        # Refuse here, not at write time, so a bad pair cannot leave the
        # earlier files in a fleet half-applied.
        yaml_scalar(name, "header-name")
        yaml_scalar(value, "header-value")
        seen[folded] = name
        out[name] = value
    return out


def write_headers(path, name, headers, stamp, dry_run, require_trust=False):
    """Replace or insert the headers: mapping on one backend."""
    document, why = _load_for_edit(path, require_trust)
    if document is None:
        return False, None, why
    entry = document.data["backends"].get(name)
    old = dict(entry.get("headers") or {}) if isinstance(entry, dict) else None
    module = _config_edit()
    try:
        module.set_headers(document, name, headers)
    except module.Refused as exc:
        return False, None, str(exc)
    problem = _finish_write(path, document, stamp, dry_run)
    if problem:
        return False, old, problem
    return document.changed, old, None


def parse_yaml(text: str):
    """Parse a config, turning every parse failure into a reason.

    One place decides what "cannot parse" means. A repo-supplied file can nest
    deeper than the parser's recursion limit, and a `RecursionError` escaping a
    caller aborted sweeps after earlier files had already been edited. Returns
    ``(data, problem)``: exactly one of them is None.
    """
    import yaml  # noqa: PLC0415

    try:
        return yaml.safe_load(text), None
    except yaml.YAMLError as exc:
        return None, f"invalid YAML ({exc})"
    except RecursionError:
        return None, "nests too deeply to parse"


def parse_yaml_node(text: str):
    """The composed node, or None when the text cannot be parsed."""
    import yaml  # noqa: PLC0415

    try:
        return yaml.compose(text)
    except (yaml.YAMLError, RecursionError):
        return None


def declares(text: str, name: str) -> bool:
    """True when the parsed file holds this backend, whatever its spelling."""
    parsed, _problem = parse_yaml(text)
    block = parsed.get("backends") if isinstance(parsed, dict) else None
    return isinstance(block, dict) and name in block


def read_field(text: str, name: str, field: str):
    """Read one field through the parser; `list` depends on this reader."""
    parsed, _problem = parse_yaml(text)
    if not isinstance(parsed, dict):
        return None
    entry = (parsed.get("backends") or {}).get(name)
    return entry.get(field) if isinstance(entry, dict) else None


def list_backends(text: str):
    """Names of the backends declared in this file, whatever their spelling."""
    parsed, _problem = parse_yaml(text)
    block = parsed.get("backends") if isinstance(parsed, dict) else None
    return list(block) if isinstance(block, dict) else []


def read_config(path: pathlib.Path) -> str:
    """Read a config verbatim, keeping whatever line endings it uses.

    pathlib's read_text translates CRLF to LF, and the matching write_text
    then writes LF back -- rewriting every line of a CRLF file as a side
    effect of changing one field.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_config(path: pathlib.Path, text: str) -> None:
    """Write a config verbatim, without translating line endings.

    The digest is kept so the seal can name the bytes this run wrote: a fresh
    read at seal time would authorize whatever the file says by then, which is
    the case the trust gate exists to catch.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    _WRITTEN[str(path)] = digest
    # This run wrote these bytes, so they are the lineage the next step of the
    # same command is allowed to build on.
    _CHECKED[str(path)] = digest


def backup(path: pathlib.Path, stamp: str):
    """Snapshot a file once per run, before this run's first edit to it.

    One command can write a file several times -- `set` with two fields does
    exactly that -- and copying on every write would leave the backup holding
    a half-applied state that never existed on disk. Restoring that is worse
    than not restoring at all, so the first copy of a given stamp wins.
    """
    dest = path.with_name(path.name + stamp)
    if dest.exists():
        return
    shutil.copy2(path, dest)


_NAME_RE = re.compile(r"^[\w.-]+$")

_CONFIG_EDIT = None


def _config_edit():
    """The editing module, imported once the source tree is on the path."""
    global _CONFIG_EDIT  # noqa: PLW0603 - one handle for the process
    if _CONFIG_EDIT is not None:
        return _CONFIG_EDIT
    _ensure_src_on_path()
    try:
        import code_forge.config_edit as module  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("ruamel"):
            raise SystemExit(
                "this tool needs its round-trip YAML loader: pip install 'code-review-forge[provider]'"
            ) from exc
        raise
    _CONFIG_EDIT = module
    return module


def _load_for_edit(path, require_trust=False, require_record=False):
    """The document this command may edit, or (None, reason).

    `_checked_text` hands back the bytes the gate approved; reading the file
    again here would let a concurrent writer's content into a file the seal
    then authorizes.
    """
    text, why = _checked_text(path)
    if text is None:
        return None, why
    if require_trust and str(path) not in _CHECKED:
        why = trust_refusal(path, text)
        if why:
            return None, why
    module = _config_edit()
    try:
        document = module.load_document(text)
    except (module.YAMLError, RecursionError) as exc:
        return None, f"does not parse as YAML ({exc})"
    if not isinstance(document.data, dict):
        return None, "the YAML root is not a mapping"
    if not isinstance(document.data.get("backends"), dict):
        return None, NO_BACKENDS
    if require_record and not is_user_config(path):
        # `remove` re-seals the whole file after editing, so it is the one
        # command that must also refuse a record that no longer covers the
        # content: otherwise the seal authorizes an endpoint that moved after
        # the file was approved. The other passes re-seal only what they wrote.
        why = trust_problem(path, document.data)
        if why:
            # trust_problem already ends with its own remedy; appending a second
            # one produced "...authorize it first...; authorize it before removal".
            return None, why if "authorize" in why else f"{why}; authorize it before removal"
    return document, None


class WriteFailed(Exception):
    """The file could not be written, and its backup already exists.

    Distinct from a refusal: a refusal leaves the file untouched, this one may
    have left it part-written, so the operator is told to restore the backup.
    """


def _finish_write(path, document, stamp, dry_run):
    """Write when the document changed. Returns a refusal reason or None."""
    if not document.changed:
        return None
    if dry_run:
        return None
    try:
        backup(path, stamp)
        write_config(path, document.dumped())
    except OSError as exc:
        raise WriteFailed(f"could not be written ({exc})") from exc
    return None


def write_field(path, name, field, value, stamp, dry_run, require_trust=False):
    """Replace one field in one backend. Returns (changed, old_value, problem).

    A field the caller named but the backend does not carry is reported as no
    change only when the backend genuinely lacks it; anything the parser would
    have to rewrite to satisfy the request is refused by the module.
    """
    document, why = _load_for_edit(path, require_trust)
    if document is None:
        return False, None, why
    entry = document.data["backends"].get(name)
    if not isinstance(entry, dict):
        return False, None, "not declared"
    if field not in entry:
        # Aligning a field the backend does not carry would add it, which is a
        # different request; the sweep only brings existing fields in line.
        return False, None, None
    old = entry.get(field)
    old_text = None if old is None else str(old)
    try:
        _config_edit().set_field(document, name, field, value)
    except _config_edit().Refused as exc:
        return False, None, str(exc)
    problem = _finish_write(path, document, stamp, dry_run)
    if problem:
        return False, old_text, problem
    return document.changed, old_text, None


def insert_backend(path, spec, stamp, dry_run, require_trust=False):
    """Add one backend through the parsed document."""
    document, why = _load_for_edit(path, require_trust)
    if document is None:
        return False, why
    module = _config_edit()
    try:
        module.add_backend(document, spec["name"], spec)
    except module.Refused as exc:
        reason = str(exc)
        return False, "already declared" if "already declared" in reason else reason
    problem = _finish_write(path, document, stamp, dry_run)
    if problem:
        return False, problem
    return document.changed, None if document.changed else "already declared"


def rename_backend(path, old, new, stamp, dry_run, require_trust=False):
    """Rename one backend's key, keeping its body and its position."""
    document, why = _load_for_edit(path, require_trust)
    if document is None:
        return False, why
    module = _config_edit()
    try:
        module.rename_backend(document, old, new)
    except module.NotDeclared:
        return False, "not declared"
    except module.Refused as exc:
        return False, str(exc)
    problem = _finish_write(path, document, stamp, dry_run)
    if problem:
        return False, problem
    return document.changed, None


def _truthy(raw) -> bool:
    """Forge's test for a `default:` value: Python truthiness of the parsed one.

    Not YAML 1.1's spelling table. `backend.py` asks `if cfg.default` on the
    parsed value, so a quoted `default: "false"` is a default to forge; judging
    it false here made `default` leave a second truthy entry behind and the
    re-parse guard agreed with the same wrong predicate.
    """
    return bool(raw)


def _defaults(text: str):
    """Names whose entry carries a truthy `default`, read from the parsed file."""

    data, _problem = parse_yaml(text)
    data = data if isinstance(data, dict) else {}
    backends = data.get("backends") if isinstance(data, dict) else None
    if not isinstance(backends, dict):
        return []
    return [
        name
        for name, entry in backends.items()
        if isinstance(entry, dict) and _truthy(entry.get("default"))
    ]


def set_default(path, name, stamp, dry_run, require_trust=False):
    """Make one backend the default and clear the flag from the rest.

    Both entries the change touches are declared to the module's locality
    check, which is why this is one mutation rather than a loop of field
    writes.
    """
    document, why = _load_for_edit(path, require_trust)
    if document is None:
        return False, why
    module = _config_edit()
    try:
        module.set_default(document, name)
    except module.NotDeclared:
        return False, "not declared"
    except module.Refused as exc:
        return False, str(exc)
    for note in document.notes:
        print(f"  {path}: {note}")
    problem = _finish_write(path, document, stamp, dry_run)
    if problem:
        return False, problem
    return document.changed, None


def trust_problem(path, data):
    """Why the record does not cover this file's content, or None.

    One predicate for the editing gate, `remove`, the audit and `rollback`:
    they used to spell the three scopes out separately, and two of them asked
    only about the backends hash.
    """
    import code_forge.trust as trust  # noqa: PLC0415

    if is_user_config(path):
        return None
    if not trust.is_trusted(path, data, migrate=False):
        return "is not trusted; authorize it first with `code-forge trust`"
    if not trust.is_trusted_focus(path, data):
        return "its review_focus text changed after it was authorized"
    if not trust.is_trusted_lenses(path, data):
        return "its review_lenses switch changed after it was authorized"
    return None


def restored_trust_status(paths):
    """Report which restored files are not the state that was authorized.

    `rollback` adopts whatever a file named like a backup contains, and a
    repository can hold such a file. Sealing the result would authorize
    repository-supplied endpoints and keys, so the restored bytes are checked
    against the record instead -- never the other way round.
    """

    _ensure_src_on_path()
    unapproved = []
    for path in paths:
        if is_user_config(path):
            continue
        try:
            text = read_config(path)
        except (OSError, UnicodeDecodeError):
            unapproved.append((path, "cannot be read after restore"))
            continue
        data, problem = parse_yaml(text)
        if problem:
            unapproved.append((path, "does not parse after restore"))
            continue
        why = trust_problem(path, data)
        if why:
            unapproved.append((path, f"is not the state that was authorized ({why})"))
    for path, why in unapproved:
        print(f"  {path}: {why}", file=sys.stderr)
    if unapproved:
        # Not a failure: restoring is what this command is for, and the newest
        # backup is usually the state that was authorized before a drift. The
        # operator still has to authorize it, and nothing here does that.
        print(f"{len(unapproved)} restored file(s) need authorization", file=sys.stderr)
    return 0


def trust_refusal(path, text):
    """Why this file may not be edited, or None when it may.

    Editing a file used to re-seal it, and the seal covers every field: a
    routine change to `default:` authorized whatever credential and prompt
    text the file carried. So a file whose record does not cover its current
    content is not edited at all, and a file that does not parse is refused
    too -- it could never be authorized either.
    """

    _ensure_src_on_path()
    data, problem = parse_yaml(text)
    if problem:
        # The exemption is about authorization, not about whether the file can
        # be read: an unparseable file is refused whoever owns it.
        return f"does not parse; repair the YAML first ({problem})"
    if is_user_config(path):
        return None
    if not isinstance(data, dict) or "backends" not in data:
        # Nothing to authorize: this is a gate config without backends, the
        # shape docs/configuration.md documents for `test.*`. Refusing it named
        # `code-forge trust` as the remedy, and that command skips it too.
        return None
    return trust_problem(path, data)


def _checked_text(path):
    """The bytes the gate approved for this file, or a refusal reason.

    The editor used to read the file again after the gate had judged it, so a
    concurrent writer's change entered the file and the seal then authorized
    bytes nobody read. This returns the text only if it is still the text the
    gate saw.
    """
    expected = _CHECKED.get(str(path))
    try:
        text = read_config(path)
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not be read ({exc})"
    if expected is None:
        # No command started this edit: the library functions are also used
        # directly, and only a command owns the gate.
        return text, None
    if hashlib.sha256(text.encode()).hexdigest() != expected:
        return None, "changed while this command was running, so it was left alone"
    return text, None


def editable(paths):
    """Split configs into (may edit, refused with reason)."""
    allowed, refused = [], []
    for path in paths:
        try:
            text = read_config(path)
        except (OSError, UnicodeDecodeError) as exc:
            refused.append((path, f"could not be read ({exc})"))
            continue
        why = trust_refusal(path, text)
        if why:
            refused.append((path, why))
        else:
            _CHECKED[str(path)] = hashlib.sha256(text.encode()).hexdigest()
            allowed.append(path)
    return allowed, refused


def report_refused(refused):
    for path, why in refused:
        print(f"skipped {path}: {why}", file=sys.stderr)


_WRITTEN = {}
_CHECKED = {}


def reseal_paths(paths, dry_run, require_written=True):
    """Record trust for each file this run edited.

    Returns ``(sealed, failures)`` where failures is a list of
    ``(path, message)``. Splitting the result per file is what lets a caller
    report a file it edited but could not authorize; a later file's error
    must not leave an earlier edit silently untrusted.

    Callers must pass only the files they edited. Re-sealing a file this run
    did not touch would authorize whatever it already said -- including an
    edit made by someone else, which is the case forge's trust gate exists to
    catch.

    forge hashes the credential-bearing fields of the backends block and
    refuses a config whose hash moved (`code_forge/trust.py`, the
    "trust invalidated" line in `is_trusted`). Editing base_url or api_key_env
    therefore silently un-trusts the file: the backend is dropped and the
    review falls back to whatever else resolves, with one line on stderr as
    the only clue.

    A file with no trust record of its own -- the user-level config -- is
    skipped before anything can be reported about it.
    """
    failures = []
    project_paths = [path for path in paths if not is_user_config(path)]
    try:
        _ensure_src_on_path()
        from code_forge.trust import record_trust  # noqa: PLC0415
    except ImportError as exc:
        return 0, [(path, f"could not re-seal trust ({exc})") for path in project_paths]

    sealed = 0
    for path in project_paths:
        try:
            current = read_config(path)
        except (OSError, UnicodeDecodeError) as exc:
            failures.append((path, f"could not read it back to re-seal trust ({exc})"))
            continue
        expected = _WRITTEN.get(str(path))
        if dry_run:
            # The preview promises the same outcome as the run, and the run
            # parses before it seals: an unparseable file is a failure here too.
            data, problem = parse_yaml(current)
            if problem:
                failures.append((path, f"would not be sealed: it does not parse ({problem})"))
                continue
            if not isinstance(data, dict) or "backends" not in data:
                failures.append((path, "would not be sealed: it has no backends block to authorize"))
                continue
            sealed += 1
            continue
        if require_written and expected is None:
            failures.append(
                (path, "was not written by this run, so sealing it would authorize unknown bytes")
            )
            continue
        if require_written and hashlib.sha256(current.encode()).hexdigest() != expected:
            failures.append((path, "changed after this run wrote it, so it was not authorized"))
            continue
        data, problem = parse_yaml(current)
        if problem:
            failures.append((path, f"could not re-seal trust ({problem})"))
            continue
        if not isinstance(data, dict) or "backends" not in data:
            failures.append((path, "edited file no longer has a backends block"))
            continue
        if not dry_run:
            # A failure here leaves the file edited but untrusted, which is
            # exactly the silent state this function exists to prevent.
            try:
                record_trust(path, data)
            except OSError as exc:
                failures.append((path, f"could not re-seal trust ({exc})"))
                continue
        sealed += 1
    return sealed, failures


def finish_edits(paths, dry_run, require_written=True):
    """Seal the files this run edited and report the command status for them.

    Every editing command ends here: a file left unauthorized is a non-zero
    exit, not a line on stderr next to a success message.
    """
    sealed, failures = reseal_paths(paths, dry_run, require_written=require_written)
    report_trust(sealed, dry_run)
    for path, why in failures:
        print(f"  {path}: {why}; authorize it with `code-forge trust`", file=sys.stderr)
    if failures:
        print(f"{len(failures)} edited file(s) still need authorization", file=sys.stderr)
    return 1 if failures else 0


def report_trust(sealed, dry_run):
    if not sealed:
        return
    verb = "would re-seal" if dry_run else "re-sealed"
    print(f"{verb} trust on {sealed} gate.yaml file(s)")


def probe(base_url, model, key, fmt):
    """Send one real request. A 401 here beats a careful diff."""
    if urllib.parse.urlsplit(base_url).scheme not in {"http", "https"}:
        return False, "provider probe requires an http or https URL"
    base = base_url.rstrip("/")
    if fmt == "anthropic":
        url, headers = (
            base + "/v1/messages",
            {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
    else:
        url, headers = (
            base + "/chat/completions",
            {
                "Authorization": f"Bearer {key}",
                "content-type": "application/json",
            },
        )
    payload = {
        "model": model,
        "max_tokens": PROBE_MAX_TOKENS,
        "messages": [{"role": "user", "content": "ping"}],
    }
    req = urllib.request.Request(  # noqa: S310 -- scheme restricted to HTTP(S) above
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310 -- validated HTTP(S) Request
            raw = resp.read()[:2000].decode(errors="replace")
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            # Some gateways answer 200 with an error object rather than a
            # completion; a status code alone would call that a working key.
            try:
                doc = json.loads(raw)
            except ValueError:
                return False, "HTTP 200 but the body is not JSON"
            if isinstance(doc, dict) and doc.get("error"):
                err = doc["error"]
                msg = err.get("message") if isinstance(err, dict) else err
                return False, f"HTTP 200 with an error body: {str(msg)[:120]}"
            has_text = ("content" in doc or "choices" in doc) if isinstance(doc, dict) else False
            if not has_text:
                return False, "HTTP 200 but no completion in the body"
            return True, "HTTP 200"
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode(errors="replace")
        if "<html" in body.lower():
            body = "(HTML error page)"
        else:
            body = " ".join(body.split())[:160]
        return False, f"HTTP {exc.code}: {body}"
    except Exception as exc:  # noqa: BLE001 - network shapes vary
        return False, f"{type(exc).__name__}: {exc}"


def resolve_key(key_pass, key_env):
    """Resolve the API key, preferring the password store over the env var.

    A --key-pass that does not resolve is reported rather than swallowed: the
    failure otherwise surfaces as a generic "no key available", which reads
    like the operator forgot to pass one instead of mistyped the entry name.
    """
    if key_pass:
        try:
            out = subprocess.run(["pass", key_pass], capture_output=True, text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.splitlines()[0].strip()
            reason = (out.stderr or "").strip().splitlines()
            detail = reason[0] if reason else "entry is empty"
        except (OSError, subprocess.SubprocessError) as exc:
            detail = str(exc)
        print(f"pass '{key_pass}' did not resolve: {detail}", file=sys.stderr)
        if key_env and os.environ.get(key_env):
            print(f"falling back to ${key_env}", file=sys.stderr)
            return os.environ[key_env]
        return None
    return os.environ.get(key_env) if key_env else None


def cmd_list(args):
    configs = find_configs()
    print(f"{len(configs)} config file(s)\n")
    seen = {}
    for path in configs:
        text = read_config(path)

        parsed, _problem = parse_yaml(text)
        parsed = parsed if isinstance(parsed, dict) else {}
        declared_map = parsed.get("backends") if isinstance(parsed, dict) else {}
        for name in list_backends(text):
            entry = declared_map.get(name) if isinstance(declared_map, dict) else None
            if not isinstance(entry, dict):
                entry = {}
            url = entry.get("base_url") or read_field(text, name, "base_url") or "?"
            model = entry.get("model") or read_field(text, name, "model") or "?"
            is_def = _truthy(entry.get("default"))
            seen.setdefault(name, {"urls": {}, "model": model, "n": 0, "default_in": 0})
            seen[name]["urls"].setdefault(url, 0)
            seen[name]["urls"][url] += 1
            seen[name]["n"] += 1
            seen[name]["default_in"] += int(is_def)

    for name, info in sorted(seen.items()):
        mark = " [default]" if info["default_in"] else ""
        print(f"  {name}{mark}   {info['model']}   in {info['n']} file(s)")
        for url, count in sorted(info["urls"].items()):
            note = "  <-- disagrees" if len(info["urls"]) > 1 else ""
            print(f"      {url}  ({count}){note}")
        if 0 < info["default_in"] < info["n"]:
            print(
                f"      default flag set in only {info['default_in']}"
                f" of {info['n']} -- a review's backend depends on the repo"
            )
    return 0


def cmd_add(args):
    key = resolve_key(args.key_pass, args.key_env)
    if not key:
        print(f"could not resolve a key from {args.key_pass or args.key_env}", file=sys.stderr)
        return 2

    spec = {
        "name": args.name,
        "base_url": args.base_url,
        "model": args.model,
        "format": args.format,
        "key_env": args.key_env or (args.name.upper().replace("-", "_") + "_API_KEY"),
        "max_tokens": args.max_tokens,
        "timeout_s": args.timeout_s,
    }
    try:
        headers = parse_headers(getattr(args, "header", None))
    except ValueError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    if headers:
        spec["headers"] = headers

    # Reject unwritable values before the probe, not on the first file. An
    # add that dies partway leaves some configs carrying the backend and the
    # rest not, which is harder to notice than a refusal up front.
    if not _NAME_RE.match(spec["name"]):
        print(
            "refusing to write: a backend name may contain letters, digits, dot, dash "
            "and underscore -- anything else is a shape this tool cannot read back",
            file=sys.stderr,
        )
        return 2
    for field in ("name", "base_url", "model", "format", "key_env"):
        try:
            yaml_scalar(str(spec[field]), field)
        except ValueError as exc:
            print(f"refusing to write: {exc}", file=sys.stderr)
            return 2

    print(f"probing {spec['base_url']} ({spec['format']}, {spec['model']}) ... ", end="", flush=True)
    ok, detail = probe(spec["base_url"], spec["model"], key, spec["format"])
    print(detail)
    if not ok:
        print("\nendpoint did not answer; nothing written", file=sys.stderr)
        return 3

    stamp = dt.datetime.now().astimezone().strftime(BACKUP_FMT)
    configs, refused = editable(find_configs())
    report_refused(refused)
    added = skipped = refused_here = 0
    written = []
    for path in configs:
        did, why = insert_backend(path, spec, stamp, args.dry_run, require_trust=True)
        if did:
            added += 1
            written.append(path)
        else:
            skipped += 1
            if why not in ("already declared", NO_BACKENDS):
                refused_here += 1
                print(f"  skipped {path}: {why}")

    verb = "would add" if args.dry_run else "added"
    print(f"\n{verb} '{spec['name']}' to {added} file(s), {skipped} skipped")
    status = finish_edits(written, args.dry_run) if added else 0
    status = max(status, 1 if refused or refused_here else 0)
    if added and not args.dry_run:
        print(f"backups: <file>{stamp}")
        print(
            f"\nexport {spec['key_env']} in your shell rc "
            + (f"(pass {args.key_pass})" if args.key_pass else "")
        )
        print(f"make it the review default: {pathlib.Path(sys.argv[0]).name} default {spec['name']}")
    return status


def cmd_set(args):
    configs, refused = editable(find_configs())
    report_refused(refused)
    addressable = []
    for path in configs:
        text = read_config(path)
        if declares(text, args.name):
            addressable.append(path)
    configs = addressable
    if not configs:
        if refused:
            return 1
        print(f"no config declares '{args.name}'", file=sys.stderr)
        return 1

    text = read_config(configs[0])
    model = args.model or read_field(text, args.name, "model")
    fmt = args.format or read_field(text, args.name, "format") or "anthropic"
    url = args.base_url or read_field(text, args.name, "base_url")

    try:
        headers = parse_headers(getattr(args, "header", None))
    except ValueError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    edits = [
        (f, v)
        for f, v in (
            ("base_url", args.base_url),
            ("model", args.model),
            ("max_tokens", args.max_tokens),
            ("timeout_s", args.timeout_s),
            ("api_key_env", args.key_env),
            ("format", args.format),
        )
        if v is not None
    ]
    if not edits and headers is None:
        print("nothing to change", file=sys.stderr)
        return 2

    key = resolve_key(args.key_pass, args.key_env or read_field(text, args.name, "api_key_env"))

    # The probe uses one file's credentials. If the configs disagree about
    # which env var holds the key, a pass here does not vouch for the rest.
    envs = {read_field(read_config(p), args.name, "api_key_env") or "(unset)" for p in configs}
    if len(envs) > 1:
        print(
            f"note: '{args.name}' reads its key from {sorted(envs)} across "
            "these configs; the probe covers only the first",
            file=sys.stderr,
        )
    routing = any(f in ("base_url", "model", "format") for f, _ in edits)
    if routing and key and url and model:
        print(f"probing {url} ({fmt}, {model}) ... ", end="", flush=True)
        ok, detail = probe(url, model, key, fmt)
        print(detail)
        if not ok:
            print("\nendpoint did not answer; nothing written", file=sys.stderr)
            return 3
    elif routing:
        # Retargeting without a probe is how a backend ends up pointing at an
        # endpoint nobody checked. Fields like timeout_s cannot break routing,
        # so those still go through.
        print(
            "cannot probe: no key resolved. Pass --key-pass or --key-env, "
            "or change only non-routing fields.",
            file=sys.stderr,
        )
        return 3

    stamp = dt.datetime.now().astimezone().strftime(BACKUP_FMT)

    # Validate every value before touching the first file. yaml_scalar raises
    # on a value it will not write, and discovering that on the second field
    # of the second file leaves both the file and the fleet half-applied.
    for field, value in edits:
        if isinstance(value, int):
            continue
        try:
            yaml_scalar(str(value), field)
        except ValueError as exc:
            print(f"refusing to write: {exc}", file=sys.stderr)
            return 2

    changed = 0
    written = []
    rejected = []
    module = _config_edit()
    for path in configs:
        document, why = _load_for_edit(path, require_trust=True)
        if document is None:
            if why == NO_BACKENDS:
                # A file with no backends block is not a failure of this command.
                continue
            rejected.append((path, why))
            print(f"  {path}: {why}", file=sys.stderr)
            continue
        olds = {field: read_field(read_config(path), args.name, field) for field, _ in edits}
        try:
            dropped = module.dropped_headers(document, args.name, headers) if headers else []
            fields, headers_changed = module.set_fields_and_headers(
                document, args.name, dict(edits), headers, add_missing=True
            )
        except module.NotLocatable as exc:
            rejected.append((path, str(exc)))
            print(f"  {path}: {exc}", file=sys.stderr)
            continue
        except module.NotDeclared:
            continue
        except module.Refused as exc:
            rejected.append((path, str(exc)))
            print(f"  {path}: {exc}", file=sys.stderr)
            continue
        problem = _finish_write(path, document, stamp, args.dry_run)
        if problem:
            rejected.append((path, problem))
            print(f"  {path}: {problem}", file=sys.stderr)
            continue
        if fields or headers_changed:
            changed += 1
            written.append(path)
            print(f"  {path}")
            for field in fields:
                print(f"      {field}: {olds.get(field)} -> {dict(edits)[field]}")
            if headers_changed:
                print(f"      headers: {len(headers)} written")
                if dropped:
                    # Names only: the values are usually credentials.
                    print(f"      headers removed: {', '.join(sorted(dropped))}")
        for note in document.notes:
            print(f"  {path}: {note}")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {changed}/{len(configs)} file(s)")
    status = finish_edits(written, args.dry_run) if changed else 0
    if changed and not args.dry_run:
        print(f"backups: <file>{stamp}")
    return max(status, 1 if refused or rejected else 0)


def cmd_sync(args):
    """Bring every config on this machine to one backend, in one command.

    Setting up a new machine took four invocations -- rename the old key,
    add the backend where it was missing, set the default, check trust --
    and each one probes the endpoint again and re-seals trust again. Worse,
    getting the order wrong leaves a repo whose gate.yaml still names a
    backend the fleet retired, and a repo-level gate.yaml wins over the
    user-level config by name, so that repo keeps reviewing on the old one
    with nothing on screen to say so.

    Order matters and is fixed here: rename first (so an existing
    declaration is recognised rather than duplicated), then add where it is
    still absent, then make it the default.
    """
    key = resolve_key(args.key_pass, args.key_env)
    if not key:
        print(f"could not resolve a key from {args.key_pass or args.key_env}", file=sys.stderr)
        return 2

    key_env = args.key_env or (args.name.upper().replace("-", "_") + "_API_KEY")
    spec = {
        "name": args.name,
        "base_url": args.base_url,
        "model": args.model,
        "format": args.format,
        "key_env": key_env,
        "max_tokens": args.max_tokens,
        "timeout_s": args.timeout_s,
    }
    try:
        headers = parse_headers(getattr(args, "header", None))
    except ValueError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    if headers:
        spec["headers"] = headers
    if not _NAME_RE.match(spec["name"]):
        print(
            "refusing to write: a backend name may contain letters, digits, dot, dash "
            "and underscore -- anything else is a shape this tool cannot read back",
            file=sys.stderr,
        )
        return 2
    for field in ("name", "base_url", "model", "format", "key_env"):
        try:
            yaml_scalar(str(spec[field]), field)
        except ValueError as exc:
            print(f"refusing to write: {exc}", file=sys.stderr)
            return 2

    # One probe for the whole run. The individual subcommands each probe,
    # which on a four-step setup means four identical round trips.
    print(f"probing {spec['base_url']} ({spec['format']}, {spec['model']}) ... ", end="", flush=True)
    ok, detail = probe(spec["base_url"], spec["model"], key, spec["format"])
    print(detail)
    if not ok:
        print("\nendpoint did not answer; nothing written", file=sys.stderr)
        return 3

    stamp = dt.datetime.now().astimezone().strftime(BACKUP_FMT)
    dry = args.dry_run
    # The five passes below only edit files, they never add or remove a
    # gate.yaml, so the directory walk happens once. A file that is not
    # authorized is dropped here, before the first pass, because the re-seal
    # at the end would otherwise authorize every field in it.
    configs, refused = editable(find_configs())
    report_refused(refused)
    touched = []
    rejected = []

    renamed = []
    added = []
    updated = []
    defaulted = []
    module = _config_edit()
    edits = [
        ("base_url", args.base_url),
        ("model", args.model),
        ("format", args.format),
        ("api_key_env", key_env),
        ("max_tokens", args.max_tokens),
        ("timeout_s", args.timeout_s),
    ]
    values = {field: value for field, value in edits if value is not None}
    # One document per file: a refusal on any step leaves the file as it was,
    # instead of keeping the steps that came before it.
    for path in configs:
        document, why = _load_for_edit(path, require_trust=True)
        if document is None:
            if why == NO_BACKENDS:
                # A file with no backends block is not a failure of this command.
                continue
            rejected.append((path, why))
            print(f"  {path}: {why}", file=sys.stderr)
            continue
        doc_notes = []
        did_rename = did_add = False
        old_name = None
        fields_changed = []
        headers_written = defaulted_here = False
        dropped = []
        try:
            for old in args.from_name or []:
                if old == args.name or old not in (document.data.get("backends") or {}):
                    continue
                if args.name in (document.data.get("backends") or {}):
                    # The new name is already here: nothing to rename, and the
                    # rest of the command still has work to do.
                    doc_notes.append(f"'{args.name}' is already declared; '{old}' left as it is")
                    break
                did_rename = module.rename_backend(document, old, args.name)
                old_name = old
                break
            if args.name not in (document.data.get("backends") or {}):
                did_add = module.add_backend(document, args.name, spec)
            fields_changed = module.set_fields(document, args.name, values)
            if headers:
                dropped = module.dropped_headers(document, args.name, headers)
                headers_written = module.set_headers(document, args.name, headers)
            defaulted_here = module.set_default(document, args.name)
            # Every step appends to document.notes and nothing clears it, so
            # reading it once here keeps the notes from the whole command.
            doc_notes.extend(document.notes)
        except module.NotLocatable as exc:
            rejected.append((path, str(exc)))
            print(f"  {path}: {exc}", file=sys.stderr)
            continue
        except module.NotDeclared:
            continue
        except module.Refused as exc:
            rejected.append((path, str(exc)))
            print(f"  {path}: {exc}", file=sys.stderr)
            continue
        problem = _finish_write(path, document, stamp, dry)
        if problem:
            rejected.append((path, problem))
            print(f"  {path}: {problem}", file=sys.stderr)
            continue
        for note in doc_notes:
            print(f"  {path}: {note}")
        if not document.changed:
            continue
        touched.append(path)
        if did_rename:
            renamed.append((path, old_name))
        if did_add:
            added.append(path)
        if fields_changed or headers_written:
            updated.append((path, fields_changed + (["headers"] if headers_written else [])))
        if defaulted_here:
            defaulted.append(path)
        if dropped:
            print(
                f"  {path}: headers removed: {', '.join(str(key) for key in sorted(dropped))}",
                file=sys.stderr,
            )

    for path, old_name in renamed:
        print(f"  {'would rename' if dry else 'renamed'} '{old_name}' -> '{args.name}' in {path}")
    for path in added:
        print(f"  {'would add' if dry else 'added'} '{args.name}' to {path}")
    for path, fields in updated:
        print(f"  {'would update' if dry else 'updated'} {path}: {', '.join(fields)}")
    for path in defaulted:
        print(f"  {'would make' if dry else 'made'} '{args.name}' the default in {path}")

    print(
        f"\n{'would touch' if dry else 'touched'} "
        f"{len(set(touched))} file(s): "
        f"{len(renamed)} renamed, {len(added)} added, "
        f"{len(updated)} updated, {len(defaulted)} defaulted"
    )

    status = finish_edits(sorted(set(touched)), dry) if touched else 0
    status = max(status, 1 if refused or rejected else 0)
    if touched and not dry:
        print(f"backups: <file>{stamp}")
        print(
            f"\nexport {key_env} in your shell rc "
            + (f"(pass {args.key_pass})" if args.key_pass else "")
        )
    return status


def cmd_default(args):
    stamp = dt.datetime.now().astimezone().strftime(BACKUP_FMT)
    configs, refused = editable(find_configs())
    report_refused(refused)
    changed = missing = 0
    written = []
    rejected = []
    for path in configs:
        did, why = set_default(path, args.name, stamp, args.dry_run, require_trust=True)
        if did:
            changed += 1
            written.append(path)
        elif why == NO_BACKENDS:
            pass
        elif why == "not declared":
            missing += 1
        elif why:
            # A refusal is not a no-op: the file still has the default the
            # operator asked to move, and saying nothing would read as success.
            rejected.append((path, why))
            print(f"  {path}: {why}", file=sys.stderr)
    verb = "would set" if args.dry_run else "set"
    print(f"{verb} '{args.name}' default in {changed} file(s)")
    if missing:
        print(f"{missing} file(s) do not declare it -- run `add {args.name}` there first")
    status = finish_edits(written, args.dry_run) if changed else 0
    if changed and not args.dry_run:
        print(f"backups: <file>{stamp}")
    return max(status, 1 if refused or rejected else 0)


def cmd_rollback(args):
    """Put the newest backup back, then say what needs authorizing.

    Deliberately not behind `editable`: that gate refuses a file whose content a
    record does not cover and a file that does not parse, which are exactly the
    two states rollback exists to fix.
    """
    restored = 0
    written = []
    skipped = []
    for path in find_configs():
        backups = sorted(path.parent.glob(path.name + ".bak-provider-*"))
        if not backups:
            continue
        if not path.is_file():
            skipped.append((path, "not a regular file"))
            continue
        print(f"  {path}\n      <- {backups[-1].name}")
        if not args.dry_run:
            shutil.copy2(backups[-1], path)
        restored += 1
        written.append(path)
    verb = "would restore" if args.dry_run else "restored"
    print(f"\n{verb} {restored} file(s)")
    status = restored_trust_status(written) if restored and not args.dry_run else 0
    for path, why in skipped:
        print(f"  skipped {path}: {why}", file=sys.stderr)
    return max(status, 1 if skipped else 0)


def cmd_trust(args):
    """Report which gate.yaml files forge currently refuses, and optionally fix."""
    try:
        _ensure_src_on_path()
        import io  # noqa: PLC0415
        import contextlib  # noqa: PLC0415
        import code_forge.trust as _trust  # noqa: PLC0415, F401
    except ImportError as exc:
        print(f"cannot import forge's trust module ({exc})", file=sys.stderr)
        return 2

    untrusted = []
    skipped = []
    checked = 0
    for path in find_configs():
        if is_user_config(path):
            continue
        try:
            text = read_config(path)
        except (OSError, UnicodeDecodeError):
            # forge cannot load it either; reporting it as absent from the
            # sweep is the false clean this command exists to prevent.
            checked += 1
            untrusted.append(path)
            continue
        data, problem = parse_yaml(text)
        if problem:
            checked += 1
            untrusted.append(path)
            continue
        if not isinstance(data, dict) or "backends" not in data:
            # Not a config this tool knows how to judge. Counting it as clean
            # would be the false sweep this command exists to prevent.
            skipped.append(path)
            continue
        checked += 1
        # is_trusted prints its own advice to stderr; we render our own.
        with contextlib.redirect_stderr(io.StringIO()):
            # --dry-run must not migrate a legacy record: a preview that
            # writes is the same defect as a removal preview that writes. The
            # predicate is the one the editing gate and `remove` apply, so the
            # audit cannot call clean a file whose prompt text or lens switch
            # forge is ignoring.
            if trust_problem(path, data):
                untrusted.append(path)

    print(
        f"{checked} gate.yaml file(s) checked, {len(untrusted)} not trusted"
        + (f", {len(skipped)} without a backends block" if skipped else "")
    )
    for path in skipped:
        print(f"  {path}: no backends block to check")
    for path in untrusted:
        print(f"  {path}")
    if untrusted:
        print(
            "\nforge drops the backends block of an untrusted file and "
            "falls back to whatever else resolves."
        )
        if args.fix:
            # `trust --fix` is the command a human runs to accept the bytes on
            # disk, so it is the one caller that does not require this run to
            # have written them.
            return finish_edits(untrusted, args.dry_run, require_written=False)
        print(f"re-seal them: {pathlib.Path(sys.argv[0]).name} trust --fix")
        return 1
    return 0


_ROUTING_FIELDS = (
    "name",
    "type",
    "format",
    "base_url",
    "model",
    "api_key_env",
    "api_key_file",
    "command",
    "credentials_path",
    "project_id",
    "region",
    "headers",
)


def _backend_identity(config):
    """The parts of a backend that decide where a request and its key go."""
    return tuple(getattr(config, field, None) for field in _ROUTING_FIELDS)


def _resolved_backend(project, user_backends, env):
    """Resolve the backend a review would use, the way the review path does.

    Returns ``(identity, error)``. The layers are parsed separately because
    forge parses them separately: one `default: true` per layer is legal,
    while a single parse of the merged mapping rejects that configuration.
    ``env`` is the same mapping the review passes, so `FORGE_BACKEND` counts.
    """
    try:
        _ensure_src_on_path()
        from code_forge.backend import load_backend_configs, resolve_backend  # noqa: PLC0415
        from code_forge.errors import CliError  # noqa: PLC0415
        from code_forge.user_config import merge_backends  # noqa: PLC0415
    except ImportError as exc:
        return None, f"cannot determine the effective backend ({exc})"
    project_map = project if isinstance(project, dict) else {}
    merged = merge_backends(project_map, user_backends or {})
    user_only = {name: value for name, value in merged.items() if name not in project_map}
    try:
        configs = load_backend_configs({"backends": project_map})
        if user_only:
            try:
                configs = configs + load_backend_configs({"backends": user_only})
            except (CliError, TypeError, ValueError, AttributeError):
                # The review path warns and keeps the project layer alone.
                pass
        return _backend_identity(resolve_backend(env, configs)), None
    except (CliError, TypeError, ValueError, AttributeError) as exc:
        return None, f"cannot determine the effective backend ({exc})"


def _user_backends():
    """The user-level layer a project config is merged with."""
    try:
        _ensure_src_on_path()
        from code_forge.user_config import load_user_backends  # noqa: PLC0415

        return load_user_backends() or {}
    except ImportError:
        return None


def remove_backend(path, name, stamp, dry_run, *, require_trust=False):
    """Delete one backend through the parsed document.

    The guards live in the module: the last entry, the default entry, a name a
    user-layer file shadows, a removal that would change the resolved backend,
    an entry hosting an anchor a survivor aliases, and a removal that would
    hand the block's leading comment to the next entry.
    """
    document, why = _load_for_edit(path, require_trust, require_record=require_trust)
    if document is None:
        return False, "not declared" if why == "no backends: block" else why
    user_layer = is_user_config(path)
    user_backends = {} if user_layer else _user_backends()
    if user_layer:

        def resolve(backends, _user, env=os.environ):
            return _resolved_backend({}, backends, env)
    else:

        def resolve(backends, user, env=os.environ):
            return _resolved_backend(backends, user, env)

    module = _config_edit()
    guards = module.Guards(
        resolve=resolve,
        # When the user layer is being removed by the same run, its entry is
        # not a shadow the module should honour; passing it made the module
        # refuse the very removal the command had just decided was safe.
        user_backends=user_backends,
        fields=_ROUTING_FIELDS,
    )
    try:
        module.remove_backend(document, name, guards)
    except module.NotDeclared:
        # The command's wording has always been "not declared"; keep it, so a
        # second run reads the same as it did before the rewrite.
        return False, "not declared"
    except module.Refused as exc:
        return False, str(exc)
    for note in document.notes:
        print(f"  {path}: {note}")
    problem = _finish_write(path, document, stamp, dry_run)
    if problem:
        return False, problem
    return document.changed, None


def cmd_remove(args):
    """Delete a backend from every config that declares it."""
    stamp = dt.datetime.now().astimezone().strftime(BACKUP_FMT)
    configs = find_configs()
    removed = []
    refused = []
    damaged = []
    unsealed = []
    declared = 0

    # The user layer first: `_user_backends` reads the file, so a project entry
    # only sees that the shadow is gone once the user layer has been written. If
    # that removal fails, the shadow is still there and the project entry is
    # refused, which is the answer the operator needs.
    configs = sorted(configs, key=lambda path: 0 if is_user_config(path) else 1)
    for path in configs:
        try:
            text = read_config(path)
        except (OSError, UnicodeDecodeError) as exc:
            # A file that is not UTF-8 fails the same way a missing one does;
            # letting the decode error escape would abort the sweep before the
            # files already edited had their trust finished.
            refused.append((path, f"unreadable ({exc})"))
            print(f"  skipped {path}: unreadable ({exc})", file=sys.stderr)
            continue
        if not declares(text, args.name):
            continue
        declared += 1
        shadowed = None if is_user_config(path) else _user_layer_same_name(args.name)
        if shadowed:
            refused.append((path, f"the user layer declares '{args.name}' too"))
            print(
                f"  skipped {path}: the user layer declares '{args.name}' too, so removing the "
                f"project entry hands the name to {shadowed}",
                file=sys.stderr,
            )
            continue
        _CHECKED[str(path)] = hashlib.sha256(text.encode()).hexdigest()
        try:
            did, why = remove_backend(path, args.name, stamp, args.dry_run, require_trust=True)
        except WriteFailed as exc:
            # The backup exists and the file may be part-written; that is not
            # the same event as "kept it" and must not be summarised as one.
            damaged.append((path, str(exc)))
            print(f"  {path}: {exc}", file=sys.stderr)
            continue
        except (OSError, UnicodeDecodeError) as exc:
            refused.append((path, f"I/O error while editing ({exc})"))
            print(f"  skipped {path}: I/O error while editing ({exc})", file=sys.stderr)
            continue
        if not did:
            refused.append((path, why))
            print(f"  skipped {path}: {why}", file=sys.stderr)
            continue
        removed.append(path)
        print(f"  {path}")
        # Finish this file's authorization now: a later failure must not
        # leave an edit already on disk describing itself as trusted.
        sealed, failures = reseal_paths([path], args.dry_run)
        if sealed:
            report_trust(sealed, args.dry_run)
        for failed_path, failure in failures:
            unsealed.append((failed_path, failure))
            print(f"  {failed_path}: {failure}; authorize it with `code-forge trust`", file=sys.stderr)

    if declared == 0:
        if refused:
            return 1
        print(f"no config declares '{args.name}'", file=sys.stderr)
        return 1

    verb = "would remove" if args.dry_run else "removed"
    print(f"\n{verb} '{args.name}' from {len(removed)} of {declared} declaring file(s)")
    if not args.dry_run and removed:
        print(f"backups: <file>{stamp}")
    if refused:
        print(f"{len(refused)} file(s) kept it; see the reasons above", file=sys.stderr)
    if damaged:
        print(f"{len(damaged)} file(s) could not be written; restore from the backup", file=sys.stderr)
    if unsealed:
        print(f"{len(unsealed)} edited file(s) still need authorization", file=sys.stderr)
    for path, fallback in _probe_fallbacks(removed, args.name):
        print(
            f"  {path}: the review resolves a user-layer backend, so a failed probe "
            f"would move it to '{fallback}' instead",
            file=sys.stderr,
        )
    if refused or damaged or unsealed:
        return 1
    return 0


def _readable(path):
    try:
        read_config(path)
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _user_layer_same_name(name):
    """The user-layer config that declares `name`, if any.

    Deleting the project entry would un-shadow it: forge merges the user layer
    under the project's, so the name keeps resolving to a different endpoint,
    key and prompt text than the operator just removed.
    """
    layer = _user_backends()
    if not layer or name not in layer:
        return None
    try:
        _ensure_src_on_path()
        from code_forge.user_config import user_config_path  # noqa: PLC0415

        resolved = user_config_path()
    except (ImportError, RuntimeError):
        resolved = None
    # One oracle: FORGE_CONFIG_DIR, XDG_CONFIG_HOME and the legacy
    # ~/.code-forge/gate.yaml all resolve through this helper.
    return str(resolved) if resolved is not None else "the user layer"


def _probe_fallbacks(paths, removed_name=None):
    """Name the backend a failed probe could move the review to.

    `resolve_backend_with_fallback` keeps the resolved backend when its probe
    passes and otherwise takes the first project entry that does. Probing here
    would need the network and a key, so this reports the deterministic part:
    which surviving entry that fallback would pick.
    """

    for path in paths:
        try:
            text = read_config(path)
        except (OSError, UnicodeDecodeError):
            continue
        data, _problem = parse_yaml(text)
        data = data if isinstance(data, dict) else {}
        project = data.get("backends") if isinstance(data, dict) else None
        if not isinstance(project, dict) or not project:
            continue
        if removed_name is not None and removed_name in project:
            # A dry run leaves the file alone, so the entry being removed is
            # still in the map; the run would not see it.
            project = {key: value for key, value in project.items() if key != removed_name}
            if not project:
                continue
        identity, _err = _resolved_backend(project, _user_backends(), os.environ)
        if identity is None:
            continue
        chosen = identity[0] if identity else None
        if chosen in project:
            continue
        surviving = next(iter(project), None)
        if surviving:
            yield path, surviving


def cmd_rename(args):
    """Rename a backend everywhere, carrying its default flag and trust."""
    if not _NAME_RE.match(args.new):
        print(
            "refusing to write: a backend name may contain letters, digits, dot, dash "
            "and underscore -- anything else is a shape this tool cannot read back",
            file=sys.stderr,
        )
        return 1
    stamp = dt.datetime.now().astimezone().strftime(BACKUP_FMT)
    configs, refused = editable(find_configs())
    report_refused(refused)
    touched = []
    rejected = []
    clashes = []

    for path in configs:
        text = read_config(path)
        if not declares(text, args.old):
            continue
        if declares(text, args.new):
            clashes.append(path)
            continue
        touched.append(path)

    if clashes:
        print(
            f"'{args.new}' already exists in {len(clashes)} file(s); resolve those by hand first:",
            file=sys.stderr,
        )
        for path in clashes:
            print(f"  {path}", file=sys.stderr)
        return 1
    if not touched:
        if refused:
            return 1
        print(f"no config declares '{args.old}'", file=sys.stderr)
        return 1

    renamed = []
    for path in touched:
        did, why = rename_backend(path, args.old, args.new, stamp, args.dry_run, require_trust=True)
        if did:
            renamed.append(path)
            print(f"  {path}")
        elif why:
            print(f"  skipped {path}: {why}", file=sys.stderr)
            rejected.append((path, why))

    verb = "would rename" if args.dry_run else "renamed"
    print(f"\n{verb} '{args.old}' -> '{args.new}' in {len(renamed)} file(s)")
    # Only the files this run actually rewrote: sealing a file whose rename was
    # refused would authorize whatever it already said, which is the trust
    # decision rename was never asked to make.
    status = finish_edits(renamed, args.dry_run)
    if not args.dry_run:
        print(f"backups: <file>{stamp}")
    return max(status, 1 if refused or rejected else 0)


def _token_cap(text: str) -> int:
    """A positive output cap: the loader refuses zero and negatives."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"{value} is not positive")
    return value


def _timeout_seconds(text: str) -> int:
    """The timeout the loader will accept; refuse anything else before a write."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from exc
    if not 0 <= value <= 86400:
        raise argparse.ArgumentTypeError(f"{value} is outside 0..86400")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The command line, so the flag contract is testable without a run."""
    ap = argparse.ArgumentParser(
        description="Manage forge review backends across every config",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(__doc__ or "").split("\n\n", 1)[-1],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show every backend and where it points")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("add", help="declare a new backend everywhere")
    p.add_argument("name")
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--format", choices=["anthropic", "openai"], default="openai")
    p.add_argument("--key-pass", help="pass(1) entry holding the key")
    p.add_argument("--key-env", help="env var name (default: NAME_API_KEY)")
    p.add_argument("--max-tokens", type=_token_cap, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--timeout-s", type=_timeout_seconds, default=DEFAULT_TIMEOUT_S)
    p.add_argument(
        "--header", action="append", metavar="NAME:VALUE", help="extra request header; repeatable"
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("set", help="change fields of an existing backend")
    p.add_argument("name")
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--format", choices=["anthropic", "openai"])
    p.add_argument("--key-pass")
    p.add_argument("--key-env")
    p.add_argument("--max-tokens", type=_token_cap)
    p.add_argument("--timeout-s", type=_timeout_seconds)
    p.add_argument(
        "--header", action="append", metavar="NAME:VALUE", help="extra request header; repeatable"
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser(
        "sync",
        help="one command: point this whole machine at one backend",
        description="Rename any older names onto NAME, declare it where it "
        "is missing, align its fields, and make it the review "
        "default -- with a single probe and a single trust "
        "re-seal. This is the command for setting up a machine.",
    )
    p.add_argument("name")
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--format", choices=["anthropic", "openai"], default="anthropic")
    p.add_argument(
        "--from-name",
        action="append",
        metavar="OLD",
        help="an older backend name to rename onto NAME; repeatable",
    )
    p.add_argument("--key-pass", help="pass(1) entry holding the key")
    p.add_argument("--key-env", help="env var name (default: NAME_API_KEY)")
    p.add_argument("--max-tokens", type=_token_cap, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--timeout-s", type=_timeout_seconds, default=DEFAULT_TIMEOUT_S)
    p.add_argument(
        "--header", action="append", metavar="NAME:VALUE", help="extra request header; repeatable"
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("default", help="make one backend the review default")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_default)

    p = sub.add_parser("remove", help="delete a backend everywhere")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("rename", help="rename a backend everywhere")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_rename)

    p = sub.add_parser("trust", help="show which gate.yaml files forge refuses")
    p.add_argument("--fix", action="store_true", help="re-seal every untrusted file")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_trust)

    p = sub.add_parser("rollback", help="restore the newest backup everywhere")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_rollback)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

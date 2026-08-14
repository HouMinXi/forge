# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pluggable review-backend abstraction.

Provides:
  - BackendConfig: frozen dataclass for a backend entry
  - ProbeResult: frozen dataclass for reachability result
  - load_backend_configs(): parse config entries into BackendConfig list
  - resolve_backend(): FORGE_BACKEND > config default > session default
  - resolve_auth_timeout(): FORGE_AUTH_TIMEOUT resolution
  - probe_backend(): backend-agnostic reachability probe
  - invalidate_probe_cache(): remove cached probe result
  - DEFAULT_BACKEND: session-model cli backend with no model pin

Locks abstraction + config schema + resolution + probe ONLY.
HTTP clients and cli review wrappers land later.

NON-GOAL: resolve_backend NEVER inspects diff, complexity,
or change size.  The user configures the model; forge follows the session
model by default.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple

from .errors import CliError

# -- Constants --------------------------------------------------------

VALID_BACKEND_TYPES = {"api", "cli"}
VALID_API_FORMATS = {"openai", "anthropic", "vertex"}
VALID_THINKING_TYPES = {"enabled", "adaptive", "disabled"}
VALID_OUTCAP_KEYS = {"max_tokens", "max_completion_tokens"}

# Keys managed by typed BackendConfig fields or protocol structure.
# Users must not override these through the generic params dict.
PROTECTED_PARAM_KEYS = frozenset({
    "model", "messages", "stream", "anthropic_version",
    "temperature", "thinking", "reasoning_effort",
    "max_completion_tokens", "max_tokens", "output_ceiling",
    # The wire key reasoning_effort takes on the formats that nest it:
    # _apply_params writes body["output_config"]["effort"], and the
    # generic params copy that follows replaces the whole dict. Without
    # this, a config could set the very field the typed one above it
    # just wrote, and the typed field would appear to be ignored.
    "output_config",
})

# A configured header is checked for two different things, and keeping them
# apart is what makes either one checkable for completeness. Whether the
# string is a header at all is grammar, and RFC 7230 answers it with no
# judgement left over. Whether forge will let a config own it is a
# permission question, and the answer is a list, which means it can be
# wrong -- so it is a list somebody else publishes and we can be held to.

# RFC 7230: field-name is a token. The '+' refuses the empty name and the
# character class refuses everything else in one clause, which matters
# because urllib is only a partial backstop here. Measured 2026-08-07 on
# CPython 3.14: http.client raises on an empty name and on CR/LF in a
# name, but a name with a SPACE in it is sent verbatim, so "X Opt: v"
# leaves on the socket for the far side to interpret however it likes.
# That one is refused here or nowhere.
_HEADER_NAME_RE = re.compile(r"[-!#$%&'*+.^_`|~0-9A-Za-z]+")

# RFC 7230 field-value, narrowed to printable ASCII with runs of space or
# tab between words but never at either end. Same measurement: urllib
# does raise on CR/LF in a value, so injection has a backstop -- but it
# passes control bytes and obs-text (0x80-0xFF) through untouched, and
# anything above U+00FF dies in its latin-1 encode as a UnicodeEncodeError
# with no backend name in it. The RFC permits obs-text and we do not:
# these carry gateway options, which are ASCII.
_HEADER_VALUE_RE = re.compile(r"(?:[\x21-\x7e]+(?:[ \t]+[\x21-\x7e]+)*)?")

# Names a config may not set. Two sources, deliberately one set:
#
#   - The credential and framing our own three wire formats send. If a
#     config could name one, it would sit in the same dict as the real one
#     and which the far side honoured would stop being forge's decision.
#   - The WHATWG Fetch forbidden request-header list, for the reason that
#     standard gives: "these are forbidden so the user agent remains in
#     full control over them". Here forge is the user agent. Measured
#     2026-08-07: a configured Content-Length of 3 replaced urllib's own
#     13, leaving ten body bytes in the connection for the next request to
#     be parsed out of -- a smuggling primitive, not a theoretical one.
#     The X-*-Method-Override family is in the same list and rewrites the
#     method at any gateway that honours it.
#
# Compared case-folded because HTTP header names are case-insensitive
# while a dict is not.
PROTECTED_HEADER_KEYS = frozenset({
    # forge's own credential and framing
    "authorization", "x-api-key", "content-type", "anthropic-version",
    # WHATWG Fetch forbidden request-header names
    "accept-charset", "accept-encoding", "access-control-request-headers",
    "access-control-request-method", "connection", "content-length",
    "cookie", "cookie2", "date", "dnt", "expect", "host", "keep-alive",
    "origin", "referer", "set-cookie", "te", "trailer",
    "transfer-encoding", "upgrade", "via",
    # method override, same list
    "x-http-method", "x-http-method-override", "x-method-override",
})

# Also from that list. Proxy-Authorization is a credential and Sec- is
# reserved so new headers can be minted that config cannot reach.
PROTECTED_HEADER_PREFIXES = ("proxy-", "sec-")

# Fields that describe an HTTP request and so mean nothing to a cli
# backend, which spawns a subprocess and sends none. Configured on one,
# they would be silently ignored, so they are refused instead.
_API_ONLY_FIELDS = (
    "temperature", "max_completion_tokens", "thinking_type",
    "thinking_budget", "reasoning_effort", "stream",
    "outcap_key", "output_ceiling", "params", "headers",
)

DEFAULT_AUTH_TIMEOUT = 20          # generous cap
MAX_REASONABLE_AUTH_TIMEOUT = 120  # sanity bound

CACHE_TTL_SECONDS = 300            # 5-minute TTL
CACHE_FILENAME = "backend_probe_cache.json"


# -- Data classes -----------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Reachability probe result (generalizes old AuthResult)."""

    ok: bool
    error: Optional[str] = None


@dataclass(frozen=True)
class BackendConfig:
    """A configured review backend entry.

    api_key_env holds the NAME of an env var, NEVER a raw secret.
    The secret value is looked up at probe time, never stored.
    """

    name: str
    type: str               # "api" | "cli"
    model: str              # may be "" for the session-default cli
    format: Optional[str] = None       # "openai" | "anthropic"
    base_url: Optional[str] = None     # only for api
    api_key_env: Optional[str] = None  # env var NAME only
    api_key_file: Optional[str] = None  # path to file containing the key
    command: str = ""                  # cli binary name or path
    default: bool = False              # config default marker
    max_tokens: int = 16384            # output token cap for api calls
    output_ceiling: int = 0            # 0 = use max_tokens; >0 = override cap
    project_id: Optional[str] = None  # vertex: GCP project ID
    region: Optional[str] = None      # vertex: GCP region (default: global)
    credentials_path: Optional[str] = None  # vertex: service account JSON path

    # Per-provider sampling and reasoning parameters
    temperature: float = -1.0              # -1 = omit; >=0 = send
    max_completion_tokens: int = 0         # 0 = fallback to existing max_tokens
    thinking_type: str = ""                # "enabled"|"adaptive"|"disabled"; ""=omit
    thinking_budget: int = 0              # >0 = add thinking.budget_tokens
    reasoning_effort: str = ""             # ""=omit; non-empty = send
    stream: bool = False                   # true = SSE, reassembled to one response
    timeout_s: int = 0                     # 0 = use default timeout chain
    outcap_key: str = ""                   # "" = format default cap key name
    params: Optional[dict] = field(
        default=None, compare=False, repr=False
    )
    headers: Optional[dict] = field(
        default=None, compare=False, repr=False
    )
    # extra request body params, and extra request headers for gateways
    # that take per-request options there rather than in the body.
    #
    # repr=False on both because the values are the reason the fields
    # exist: a gateway option is often a token, and a dataclass repr
    # goes into tracebacks and debug logs without anyone choosing to put
    # it there. The two fields hold the same class of value and are
    # shielded the same way -- one of them shielded and the other not
    # would read as a judgement that one is safe to print.
    #
    # compare=False on both: a frozen dataclass derives __hash__ from its
    # compared fields, and a dict is unhashable, so with these compared
    # the class could not be hashed at all. The cost is that two configs
    # differing only in params or headers are equal and hash alike --
    # accepted because nothing compares or hashes these, and the
    # alternative is a class that cannot go in a set.

    # CLI backend child-process env overrides
    env_unset: Tuple[str, ...] = ()        # var names to remove from child env
    env_set: Tuple[Tuple[str, str], ...] = ()  # (name, value) pairs to set


# -- DEFAULT_BACKEND -------------------------------------------------

DEFAULT_BACKEND = BackendConfig(
    name="session-default",
    type="cli",
    model="",
    format=None,
    base_url=None,
    api_key_env=None,
    command="",
    max_tokens=16384,
)


# -- Config parsing ---------------------------------------------------


def is_protected_header(name: str) -> bool:
    """Whether forge reserves this header name for itself.

    Case-folded, because HTTP does not distinguish spellings. Kept as a
    function rather than left as two inline lookups so config load and
    the send-time check ask the question the same way: two copies of the
    same predicate drift, and the one that drifts is the one nobody is
    looking at.
    """
    folded = name.lower()
    return (folded in PROTECTED_HEADER_KEYS
            or folded.startswith(PROTECTED_HEADER_PREFIXES))


def check_headers(headers: dict, name: str, fail) -> None:
    """Grammar, then permission, then uniqueness. Raises via `fail`.

    Config load runs this on the way in, where the error can name the
    backend and be fixed before anything runs; the send path runs it
    again on the way out, where it is the only thing standing between a
    backend built in code and the wire. Same three questions, same
    order, same messages -- `fail` supplies the exception type, since
    one caller reports a config error and the other a request error.

    Not one of the three can be left to urllib. Measured off a socket:
    urllib refuses a value carrying CRLF and refuses an empty name, and
    sends everything else verbatim -- 'X foo: v' goes out as 'X Foo: v',
    a tab in a name goes out as 'X\\tA: v', a control byte in a value
    goes out unremarked. Nor does urllib know that a name is reserved,
    or that two spellings of one name are one header.

    The mapping check belongs here rather than in the yaml wrapper: a
    backend built in code reaches the send path without passing the
    parser at all, and `headers` as a list of pairs would otherwise
    surface as AttributeError from .items() -- an error naming neither
    the backend nor the field, raised inside the retry loop, where the
    default is to retry it.
    """
    if not isinstance(headers, dict):
        raise fail(
            "backend %r: headers must be a mapping of name to string, "
            "not %s" % (name, type(headers).__name__)
        )
    seen: dict = {}
    for hk, hv in headers.items():
        if not isinstance(hk, str) or not isinstance(hv, str):
            raise fail(
                "backend %r: header %r must have a string name and a "
                "string value" % (name, hk)
            )
        if not _HEADER_NAME_RE.fullmatch(hk):
            raise fail(
                "backend %r: header name %r is not a valid HTTP field "
                "name (letters, digits and -!#$%%&'*+.^_`|~ only, and "
                "not empty)" % (name, hk)
            )
        if not _HEADER_VALUE_RE.fullmatch(hv):
            raise fail(
                "backend %r: value of header %r is not a valid HTTP "
                "field value (printable ASCII, no leading or trailing "
                "blanks, no line breaks)" % (name, hk)
            )
        folded = hk.lower()
        if is_protected_header(hk):
            raise fail(
                "backend %r: headers must not set %r -- forge controls "
                "that header (it carries the credential, the request "
                "framing, or the method)" % (name, hk)
            )
        # Header names are case-insensitive, so two spellings of one name
        # are one header carrying two values, and a dict cannot say which
        # was meant. Measured off a socket: urllib folds both into a
        # single line, keeps the value written LAST, and titlecases the
        # name, so 'x-note: one' then 'X-Note: two' sends 'X-Note: two'
        # and the reverse order sends 'X-Note: one'. Which value survives
        # is decided by the order of lines in a yaml file, and the other
        # is dropped without a word. Refusing is the only answer here
        # that is not a guess at which one was meant.
        if folded in seen:
            raise fail(
                "backend %r: headers set %r and %r, which HTTP treats as "
                "one header -- only one of the two values would be sent, "
                "and which one depends on the order they appear in"
                % (name, seen[folded], hk)
            )
        seen[folded] = hk


def check_params(params: dict, name: str, fail) -> None:
    """Mapping, then permission. Raises via `fail`.

    The params twin of check_headers, and run in the same two places for
    the same reason: config load catches the yaml case where the error
    can name the backend, and the send path catches the backend built in
    code, which reaches the wire without passing the parser at all.

    Two of check_headers' three questions are missing here rather than
    forgotten. There is no grammar to check because a param is a JSON
    value, and any value json.dumps accepts is one. There is no
    case-folded uniqueness check because JSON object keys are
    case-sensitive: 'Model' and 'model' are two distinct fields, and
    only the lowercase one is ours. Folding here would refuse a
    perfectly ordinary gateway param on a collision that does not exist.

    Keys are compared exactly for that same reason -- which does mean a
    gateway honouring a case-insensitive 'Model' would slip past. No
    such gateway is known, and refusing every capitalisation of every
    protected name would cost more than it buys.
    """
    if not isinstance(params, dict):
        raise fail(
            "backend %r: params must be a mapping of name to value, "
            "not %s" % (name, type(params).__name__)
        )
    # Iterating the config's own keys rather than the protected set: a
    # frozenset has no order, so with two protected keys present the
    # error would name whichever one the hash landed on first, and the
    # message would differ between runs on the same file.
    for pk in sorted(k for k in params if k in PROTECTED_PARAM_KEYS):
        raise fail(
            "backend %r: params must not contain protected "
            "key %r (use the dedicated config field instead)"
            % (name, pk)
        )


def _parse_headers(entry: dict, name: str) -> Optional[dict]:
    """Validate a backend's configured headers, or return None."""
    headers = entry.get("headers")
    if headers is None:
        return None
    check_headers(headers, name, CliError)
    return headers


def _parse_provider_fields(entry: dict, name: str) -> dict:
    """Extract and validate provider-aware fields from an api entry.

    Returns kwargs dict for BackendConfig construction.
    """
    kw: dict = {}

    # Typed sampling/reasoning fields
    kw["temperature"] = entry.get("temperature", -1.0)
    kw["max_completion_tokens"] = entry.get("max_completion_tokens", 0)
    kw["thinking_budget"] = entry.get("thinking_budget", 0)
    kw["reasoning_effort"] = entry.get("reasoning_effort", "")
    kw["stream"] = bool(entry.get("stream", False))
    kw["timeout_s"] = entry.get("timeout_s", 0)

    # thinking_type enum
    tt = entry.get("thinking_type", "")
    if tt and tt not in VALID_THINKING_TYPES:
        raise CliError(
            "backend %r: invalid thinking_type %r (expected: %s)"
            % (name, tt, "|".join(sorted(VALID_THINKING_TYPES)))
        )
    kw["thinking_type"] = tt

    # outcap_key enum
    ock = entry.get("outcap_key") or ""
    if ock and ock not in VALID_OUTCAP_KEYS:
        raise CliError(
            "backend %r: invalid outcap_key %r (expected: %s or empty)"
            % (name, ock, "|".join(sorted(VALID_OUTCAP_KEYS)))
        )
    kw["outcap_key"] = ock

    # Cap validation: at least one positive
    max_tokens = entry.get("max_tokens", 16384)
    mct = kw["max_completion_tokens"]
    if mct <= 0 and max_tokens <= 0:
        raise CliError(
            "backend %r: output token cap must be positive "
            "(max_completion_tokens and max_tokens are both zero)"
            % name
        )

    # params: reject protected keys
    params = entry.get("params")
    if params is not None:
        check_params(params, name, CliError)
    kw["params"] = params

    kw["headers"] = _parse_headers(entry, name)

    # Reject cli-only env fields on api/vertex
    if "env" in entry:
        raise CliError(
            "backend %r: 'env' field is only valid on cli backends"
            % name
        )
    if "env_unset" in entry:
        raise CliError(
            "backend %r: 'env_unset' is an internal field name, "
            "not a config key (use env: {unset: [...]})" % name
        )
    if "env_set" in entry:
        raise CliError(
            "backend %r: 'env_set' is an internal field name, "
            "not a config key (use env: {set: {...}})" % name
        )

    return kw


def _parse_cli_env(entry: dict, name: str) -> dict:
    """Extract and validate env fields from a cli entry.

    Returns kwargs dict with env_unset and env_set.
    """
    env = entry.get("env")
    if env is None:
        return {"env_unset": (), "env_set": ()}

    if not isinstance(env, dict):
        raise CliError(
            "backend %r: 'env' must be a dict, got %s"
            % (name, type(env).__name__)
        )

    allowed = {"unset", "set"}
    unknown = set(env.keys()) - allowed
    if unknown:
        raise CliError(
            "backend %r: unknown key(s) in env: %s"
            % (name, ", ".join(sorted(unknown)))
        )

    unset_list = env.get("unset") or []
    set_dict = env.get("set") or {}

    return {
        "env_unset": tuple(unset_list),
        "env_set": tuple(sorted(
            (k, str(v)) for k, v in set_dict.items()
        )),
    }


def _parse_backend_entry(entry: dict) -> BackendConfig:
    """Parse and validate a single backend config entry."""
    # One rule for null: it means absent. Strip null-valued keys so
    # every field falls back to its own default or sentinel instead of
    # storing None into a typed attribute (or crashing in max(0, None)
    # before validation can name it). The caller's dict is left
    # untouched -- a shallow copy keeps its other values shared.
    entry = {k: v for k, v in entry.items() if v is not None}
    name = entry.get("name", "<unnamed>")

    # Reject inline secrets (never allow raw keys in config)
    if "api_key" in entry:
        raise CliError(
            "backend %r: store the env-var NAME in api_key_env, "
            "never an inline api_key" % name
        )

    btype = entry.get("type", "")
    if btype not in VALID_BACKEND_TYPES:
        raise CliError(
            "backend %r: invalid type %r (expected: %s)"
            % (name, btype, "|".join(sorted(VALID_BACKEND_TYPES)))
        )

    model = entry.get("model", "")
    is_default = entry.get("default", False)
    max_tokens = entry.get("max_tokens", 16384)
    output_ceiling = max(0, entry.get("output_ceiling", 0))

    if btype == "api":
        fmt = entry.get("format")
        if not fmt:
            raise CliError(
                "backend %r (api): missing required field 'format' "
                "(expected: %s)"
                % (name, "|".join(sorted(VALID_API_FORMATS)))
            )
        if fmt not in VALID_API_FORMATS:
            raise CliError(
                "backend %r (api): invalid format %r (expected: %s)"
                % (name, fmt, "|".join(sorted(VALID_API_FORMATS)))
            )

        # Provider-aware fields (shared by all api formats)
        pf = _parse_provider_fields(entry, name)

        if fmt == "vertex":
            project_id = entry.get("project_id")
            if not project_id:
                raise CliError(
                    "backend %r (api/vertex): missing required field "
                    "'project_id'" % name
                )
            region = entry.get("region", "global")
            credentials_path = entry.get("credentials_path")
            if credentials_path:
                credentials_path = os.path.expanduser(credentials_path)
            return BackendConfig(
                name=name, type=btype, model=model, format=fmt,
                base_url=None, api_key_env=None, command="",
                default=is_default, max_tokens=max_tokens,
                output_ceiling=output_ceiling,
                project_id=project_id, region=region,
                credentials_path=credentials_path,
                **pf,
            )

        base_url = entry.get("base_url")
        if not base_url:
            raise CliError(
                "backend %r (api): missing required field 'base_url'"
                % name
            )
        api_key_env = entry.get("api_key_env")
        api_key_file = entry.get("api_key_file")
        if api_key_env and api_key_file:
            raise CliError(
                "backend %r (api): set api_key_env or api_key_file, "
                "not both" % name
            )
        if not api_key_env and not api_key_file:
            raise CliError(
                "backend %r (api): missing credential field -- set "
                "api_key_env (env var name) or api_key_file (path)"
                % name
            )
        if api_key_file:
            api_key_file = os.path.expanduser(api_key_file)
        return BackendConfig(
            name=name, type=btype, model=model, format=fmt,
            base_url=base_url, api_key_env=api_key_env,
            api_key_file=api_key_file, command="",
            default=is_default, max_tokens=max_tokens,
            output_ceiling=output_ceiling,
            **pf,
        )

    # type == "cli"
    # Reject api-only fields
    for af in _API_ONLY_FIELDS:
        if af in entry:
            raise CliError(
                "backend %r (cli): field %r is only valid on "
                "api backends" % (name, af)
            )

    env_kw = _parse_cli_env(entry, name)

    command = entry.get("command", "")
    return BackendConfig(
        name=name, type=btype, model=model,
        format=None, base_url=None, api_key_env=None,
        command=command, default=is_default, max_tokens=max_tokens,
        **env_kw,
    )


def load_backend_configs(
    data: Optional[dict],
) -> List[BackendConfig]:
    """Parse already-loaded config mapping into BackendConfig list.

    Expects backends as a dict with backend names as keys:
      backends:
        mimo:
          type: api
          ...

    Returns [] for None / empty / missing backends key.
    Raises CliError on invalid schema or multiple default: true entries.
    """
    if data is None:
        return []
    backends = data.get("backends")
    if not backends:
        return []
    if not isinstance(backends, dict):
        raise CliError(
            "backends must be a dict with backend names as keys"
        )
    configs = []
    for name, entry in backends.items():
        if not isinstance(entry, dict):
            raise CliError(
                "backend %r: entry must be a dict, got %s"
                % (name, type(entry).__name__)
            )
        # Null handling and caller-dict isolation live in
        # _parse_backend_entry; the name is injected on a copy so the
        # caller's entry dict is never mutated.
        entry = dict(entry)
        entry["name"] = name
        configs.append(_parse_backend_entry(entry))
    # multiple default: true entries raise CliError
    defaults = [c for c in configs if c.default]
    if len(defaults) > 1:
        names = ", ".join(c.name for c in defaults)
        raise CliError("multiple default backends: %s" % names)
    return configs


# -- Timeout resolution -----------------------------------------------


def resolve_auth_timeout(
    cli_value: Optional[int],
    env: Mapping[str, str],
) -> int:
    """Resolve auth probe timeout: cli > env > default (20s).

    Validates >= 1 and <= MAX_REASONABLE_AUTH_TIMEOUT.
    """
    if cli_value is not None:
        return _validate_timeout(cli_value, "--auth-timeout")
    raw = env.get("FORGE_AUTH_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_AUTH_TIMEOUT
    return _parse_env_timeout(raw)


def _parse_env_timeout(raw: str) -> int:
    """Parse FORGE_AUTH_TIMEOUT string to validated int."""
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise CliError(
            "invalid FORGE_AUTH_TIMEOUT: %r (expected int)" % raw
        ) from exc
    return _validate_timeout(value, "FORGE_AUTH_TIMEOUT")


def _validate_timeout(value: int, name: str) -> int:
    """Validate timeout >= 1 and <= cap."""
    if value < 1:
        raise CliError(
            "invalid %s: %d (must be >= 1)" % (name, value)
        )
    if value > MAX_REASONABLE_AUTH_TIMEOUT:
        raise CliError(
            "invalid %s: %d (exceeds sanity cap %d)"
            % (name, value, MAX_REASONABLE_AUTH_TIMEOUT)
        )
    return value


# -- Backend resolution ------------------------------------------------


def resolve_backend(
    env: Mapping[str, str],
    configs: List[BackendConfig],
    cli_value: Optional[str] = None,
) -> BackendConfig:
    """Resolve the active backend.

    Precedence: cli_value > FORGE_BACKEND env > config default > session
    default (DEFAULT_BACKEND).

    CRITICAL (NON-GOAL): This function MUST NOT accept a diff,
    complexity, or change-size parameter.  forge NEVER analyzes the diff
    to auto-select a backend or model.
    """
    # Step 1: determine the override key (cli > env)
    override = cli_value
    if override is None:
        override = env.get("FORGE_BACKEND")

    if override is not None and override != "":
        key = override.strip()
        if key == "":
            raise CliError(
                "invalid FORGE_BACKEND: whitespace-only value %r"
                % override
            )
        for cfg in configs:
            if cfg.name == key:
                return cfg
        configured = ", ".join(c.name for c in configs) or "none"
        raise CliError(
            "unknown backend %r (configured: %s)" % (key, configured)
        )

    # Step 2: empty-string override falls through
    # Step 3: config default
    if configs:
        for cfg in configs:
            if cfg.default:
                return cfg
        return configs[0]

    # Step 4: session-model default
    return DEFAULT_BACKEND


# -- Probe cache ------------------------------------------------------


def _default_cache_dir() -> Path:
    """Return the default cache directory for forge."""
    base = os.environ.get(
        "XDG_CACHE_HOME", str(Path.home() / ".cache")
    )
    return Path(base) / "code-forge"


def _read_cache(
    cache_dir: Path,
    backend_name: str,
    time_fn: Callable[[], float],
) -> Optional[ProbeResult]:
    """Read cached probe result if valid and within TTL.

    Returns None on ANY read failure: cache is convenience-only,
    so corrupted JSON, missing keys, wrong types, binary garbage,
    backend name mismatch, or permission errors all trigger a safe
    cache miss that re-probes.
    """
    try:
        cache_path = cache_dir / CACHE_FILENAME
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if data.get("backend") != backend_name:
            return None
        if data["ok"] and (time_fn() - data["timestamp"]) < CACHE_TTL_SECONDS:
            return ProbeResult(ok=True)
    except Exception:
        return None
    return None


def _write_cache(
    cache_dir: Path,
    backend_name: str,
    time_fn: Callable[[], float],
) -> None:
    """Write a successful probe result to cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / CACHE_FILENAME
    cache_path.write_text(json.dumps({
        "ok": True,
        "backend": backend_name,
        "timestamp": time_fn(),
    }), encoding="utf-8")


def invalidate_probe_cache(
    cache_dir: Optional[Path] = None,
) -> None:
    """Remove the probe cache file."""
    if cache_dir is None:
        cache_dir = _default_cache_dir()
    cache_path = cache_dir / CACHE_FILENAME
    cache_path.unlink(missing_ok=True)


# -- Backend-agnostic reachability probe ------------------------------


def probe_backend(
    backend: BackendConfig,
    which_fn: Callable = shutil.which,
    run_cmd: Callable = subprocess.run,
    env: Optional[Mapping[str, str]] = None,
    cache_dir: Optional[Path] = None,
    time_fn: Callable[[], float] = time.time,
    timeout: int = DEFAULT_AUTH_TIMEOUT,
) -> ProbeResult:
    """Backend-agnostic reachability probe.

    For cli/claude: runs ``claude auth status --json`` (NOT an
    inference call).  For api: checks api_key_env presence in env
    (no subprocess, no network).

    Explicitly configured cli backends (name != "session-default") bypass
    the probe entirely and return ProbeResult(ok=True) immediately.
    DEFAULT_BACKEND (name="session-default") still probes via _probe_cli.

    Successful results are cached with 5-min TTL.
    Failures are NOT cached.
    """
    if env is None:
        env = os.environ
    if cache_dir is None:
        cache_dir = _default_cache_dir()

    # Check cache first
    cached = _read_cache(cache_dir, backend.name, time_fn)
    if cached is not None:
        return cached

    # Explicitly configured cli backends bypass probe (trust configured).
    # DEFAULT_BACKEND has name="session-default"; any gate.yaml cli backend
    # has a user-chosen name and is assumed reachable as configured.
    if backend.type == "cli" and backend.name != "session-default":
        return ProbeResult(ok=True)

    # Dispatch on backend type
    if backend.type == "api":
        result = _probe_api(backend, env)
    else:
        result = _probe_cli(backend, which_fn, run_cmd, timeout)

    # Cache successful results only
    if result.ok:
        _write_cache(cache_dir, backend.name, time_fn)

    return result


def credential_error(
    backend: BackendConfig,
    env: Mapping[str, str],
) -> Optional[str]:
    """Single credential validation rule shared by fast-fail and probe.

    Returns None when credentials are resolvable, or a human-readable
    error string when they are not.  Wrappers decide how to surface the
    error (CliError vs ProbeResult).

    Covers explicitly configured credentials only.  Vertex ADC fallback
    (GOOGLE_APPLICATION_CREDENTIALS / gcloud ADC) is handled by
    _probe_api, not here -- the fast-fail path deliberately defers it.
    """
    # Vertex: explicit credentials_path only
    if backend.format == "vertex":
        if backend.credentials_path:
            if not Path(backend.credentials_path).is_file():
                return (
                    "backend %r (vertex): credentials_path %r not found"
                    % (backend.name, backend.credentials_path)
                )
        return None

    # File-based credential
    if backend.api_key_file:
        p = Path(backend.api_key_file)
        if not p.is_file():
            return (
                "backend %r: api_key_file not found: %s"
                % (backend.name, backend.api_key_file)
            )
        try:
            content = p.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return (
                "backend %r: api_key_file unreadable: %s: %s"
                % (backend.name, backend.api_key_file, exc)
            )
        if not content:
            return (
                "backend %r: api_key_file empty: %s"
                % (backend.name, backend.api_key_file)
            )
        mode = p.stat().st_mode
        if mode & 0o077:
            return (
                "backend %r: api_key_file %s is group/world "
                "readable (mode %o). chmod 600 it."
                % (backend.name, backend.api_key_file, mode & 0o777)
            )
        return None

    # Env-var credential
    key_name = backend.api_key_env
    if not key_name:
        return (
            "backend %r: no api_key_env or api_key_file configured"
            % backend.name
        )
    if env.get(key_name):
        return None
    return (
        "%s not set. Export the API key for backend %r."
        % (key_name, backend.name)
    )


def _probe_api(
    backend: BackendConfig,
    env: Mapping[str, str],
) -> ProbeResult:
    """Check that the configured backend credential is resolvable.

    No subprocess, no network call.  Uses credential_error for the
    shared validation rule; adds vertex ADC fallback (ADC / gcloud)
    which the fast-fail path deliberately defers.
    """
    # Shared rule: covers explicit credentials for all formats
    err = credential_error(backend, env)
    if err is not None:
        return ProbeResult(ok=False, error=err)

    # Vertex ADC fallback: only when no explicit credentials_path.
    # credential_error returns None for vertex without credentials_path,
    # so we check ADC here to cover the implicit-credentials case.
    if backend.format == "vertex" and not backend.credentials_path:
        if env.get("GOOGLE_APPLICATION_CREDENTIALS"):
            return ProbeResult(ok=True)
        adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if adc.is_file():
            return ProbeResult(ok=True)
        return ProbeResult(
            ok=False,
            error="backend %r (vertex): no GCP credentials. Set "
            "credentials_path, GOOGLE_APPLICATION_CREDENTIALS, or run "
            "'gcloud auth application-default login'." % backend.name,
        )

    return ProbeResult(ok=True)


def _probe_cli(
    backend: BackendConfig,
    which_fn: Callable,
    run_cmd: Callable,
    timeout: int,
) -> ProbeResult:
    """Probe a cli backend via ``claude auth status --json``.

    This is NOT an inference call -- zero token cost.
    """
    if which_fn("claude") is None:
        return ProbeResult(
            ok=False,
            error=(
                "claude binary not found in PATH. "
                "Install Claude CLI: "
                "https://docs.anthropic.com/en/docs/claude-code"
            ),
        )

    try:
        result = run_cmd(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            ok=False,
            error=(
                "reachability probe timed out after %ds. "
                "Increase FORGE_AUTH_TIMEOUT or check the CLI."
                % timeout
            ),
        )
    except OSError as exc:
        return ProbeResult(
            ok=False,
            error=(
                "claude reachability probe failed to start: %s"
                % exc
            ),
        )

    if result.returncode != 0:
        stderr_text = (result.stderr or "")[:100]
        return ProbeResult(
            ok=False,
            error=(
                "claude reachability check failed (exit %d): %s"
                % (result.returncode, stderr_text)
            ),
        )

    # Parse the JSON output
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return ProbeResult(
            ok=False,
            error="could not parse `claude auth status --json` output",
        )

    if parsed.get("loggedIn") is True:
        return ProbeResult(ok=True)

    return ProbeResult(
        ok=False,
        error=(
            "claude not logged in. "
            "Run `claude auth login` or set ANTHROPIC_API_KEY."
        ),
    )

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
        --key-pass personal/api/glm --key-env GLM_API_KEY
    forge-provider.py default glm-flash
    forge-provider.py set review-default --base-url https://new.host/anthropic
    forge-provider.py rename old-name new-name
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
whatever else resolves. Every writing command here re-seals the files it
wrote, and only those -- re-sealing a file this run did not touch would
authorize an edit nobody read.

Known limits, all reproduced rather than assumed:

  - A crash partway through the fleet leaves earlier files written and later
    ones not. Values are validated before the first write, so the realistic
    trigger is a permission error, not a bad argument.
  - `rollback` restores the newest backup per file. Two separate runs mean
    two stamps; rolling back the second does not undo the first, and mixing
    them yields a combination that never existed. Check `list` afterwards.
  - The probe uses one file's credentials. Configs that disagree about which
    env var holds the key get a warning, not thirty probes.
  - Files are parsed with regexes, not a YAML round-trip, to keep the
    comments a round-trip would drop. Anchors and aliases are not understood;
    no config on this host uses them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

USER_CONFIG = pathlib.Path.home() / ".config/code-forge/config.yaml"
SEARCH_ROOT = pathlib.Path.home()
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
    ".local", ".cache", ".venv", "node_modules", "site-packages",
    ".planning", ".git", "__pycache__",
}
BACKUP_FMT = ".bak-provider-%Y%m%d-%H%M%S-%f"
# Reasoning models spend output budget on hidden thinking before the first
# visible token. A cap tight enough for a "ping" starves that and comes back
# empty or truncated, which reads as a dead endpoint. Big enough to clear
# thinking, small enough to stay cheap.
PROBE_MAX_TOKENS = 1024

# Reviews send a large prompt and read a long answer back; a small cap
# truncates the verdict JSON and forge then parses zero findings out of a
# response that had plenty. See the max_tokens note in the fleet memory.
DEFAULT_MAX_TOKENS = 65536
DEFAULT_TIMEOUT_S = 2400


def find_configs():
    """Every gate.yaml a review could resolve, plus the user-level config.

    Backups, container layers, and archived review output all contain files
    named gate.yaml; none of them is a config any review reads. Missing a
    real one is the worse error -- it leaves a checkout pointing at the old
    backend with nothing to show that it was skipped.
    """
    found = [USER_CONFIG] if USER_CONFIG.is_file() else []
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
            f"{field}: {value!r} has characters that are not safe to write "
            "unquoted into YAML"
        )
    # Anything left is safe bare EXCEPT the words and shapes YAML resolves to
    # something other than a string. A model literally named `no` parses as
    # False, and `0755` as 493 -- both silently, both far from the edit.
    reserved = {"true", "false", "yes", "no", "on", "off", "null", "none", "~"}
    numeric = re.fullmatch(r"[+-]?(\d[\d_]*(\.\d*)?|\.\d+)([eE][+-]?\d+)?",
                           value)
    if (re.fullmatch(r"[A-Za-z0-9._-]+", value)
            and value.lower() not in reserved
            and not numeric):
        return value
    return '"' + value + '"'


def backends_block(text: str):
    """Locate the real `backends:` mapping. Returns (start, end) of its body.

    A freshly `code-forge init`-ed gate.yaml documents the whole schema in
    comments, so the word appears long before any backend is declared. Only an
    unindented, uncommented `backends:` followed by an indented entry is the
    real thing -- otherwise this reports a block that cannot be written to.

    The block ends at the next unindented key, not at the next unindented
    character: a comment written flush left between two backends is a comment,
    not the end of the mapping, and treating it as one hides every backend
    below it.
    """
    for head in re.finditer(r"^backends:[ \t\r]*$", text, re.MULTILINE):
        start = head.end() + 1
        tail = None
        for m in re.finditer(r"^(\S.*)$", text[start:], re.MULTILINE):
            if m.group(1).startswith("#"):
                continue
            tail = m
            break
        end = start + tail.start() if tail else len(text)
        body = text[start:end]
        # A mapping with at least one non-comment key is a real block.
        if re.search(r"^[ \t]+[\w.-]+:", body, re.MULTILINE):
            return start, end
    return None


def find_backend(text: str, name: str):
    """Locate one backend's body inside the file. Returns (start, end).

    The search is confined to the `backends:` mapping. A nested key alone on
    its line has the same shape as a backend key -- `headers:` is exactly
    that, and forge's own gate.yaml carries four of them -- so a whole-file
    search would let `rename headers h2` rewrite a sub-block belonging to
    some other backend and leave the real one untouched.

    The body ends at the next line indented no deeper than the key itself --
    a sibling backend, or the end of the mapping. Blank lines and comment
    lines carry no structure and must not end it, or a block that happens to
    be followed by a blank line swallows its neighbour and every field in it.
    """
    block = backends_block(text)
    if not block:
        return None
    lo, hi = block
    region = text[lo:hi]

    key_indent = None
    for m in re.finditer(r"^([ \t]+)([^\s#][^:\r\n]*):[ \t]*(\S*)[ \t\r]*$",
                         region, re.MULTILINE):
        if key_indent is None:
            key_indent = len(m.group(1))
        if len(m.group(1)) != key_indent:
            continue  # a nested key, not a backend
        if m.group(2).strip() != name:
            continue
        trailer = m.group(3)
        if trailer and not trailer.startswith("&"):
            # `name: *anchor` is an alias with no body of its own; there is
            # nothing here to edit and rewriting it would detach the alias.
            return None
        start = min(lo + m.end() + 1, hi)
        for nxt in re.finditer(r"^([ \t]*)(\S.*?)[\r]*$", text[start:hi],
                               re.MULTILINE):
            if nxt.group(2).startswith("#"):
                continue
            if len(nxt.group(1)) <= key_indent:
                return start, start + nxt.start()
        return start, hi
    return None


def read_field(text: str, name: str, field: str):
    span = find_backend(text, name)
    if not span:
        return None
    hit = re.search(
        rf"^[ \t]*{re.escape(field)}:[ \t]*(.+?)(?=[ \t]*\r?$)",
        text[span[0]:span[1]],
        re.MULTILINE,
    )
    if not hit:
        return None
    raw = hit.group(1)
    # Strip one matching pair of quotes; a value containing a quote of its own
    # must survive, which the older [^"\n]+ pattern silently dropped.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def list_backends(text: str):
    """Names of the backends declared in this file.

    A key carrying a YAML anchor (`name: &anchor`) counts: missing it makes
    `add` believe the backend is absent and write a duplicate key, which YAML
    resolves by silently keeping the last one.

    Indentation is matched the way find_backend matches it. The two once
    disagreed on tabs -- one saw no backends where the other saw one, so a
    file could be reported as empty and edited at the same time.
    """
    span = backends_block(text)
    if not span:
        return []
    body = text[span[0]:span[1]]
    first = re.match(r"^([ \t]+)", body)
    if not first:
        return []
    indent = re.escape(first.group(1))
    return re.findall(rf"^{indent}([\w.-]+):[ \t]*(?:[&*]\S+)?[ \t\r]*$",
                      body, re.MULTILINE)


def read_config(path: pathlib.Path) -> str:
    """Read a config verbatim, keeping whatever line endings it uses.

    pathlib's read_text translates CRLF to LF, and the matching write_text
    then writes LF back -- rewriting every line of a CRLF file as a side
    effect of changing one field.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_config(path: pathlib.Path, text: str) -> None:
    """Write a config verbatim, without translating line endings."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


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


def write_field(path, name, field, value, stamp, dry_run):
    """Replace one field in one backend. Returns (changed, old_value).

    A field the caller named but the pattern could not match is reported as
    an error rather than as "nothing to change" -- silently declining to
    write is how an operator ends up believing a backend was retargeted when
    the file still says what it always said.
    """
    text = read_config(path)
    span = find_backend(text, name)
    if not span:
        return False, None
    body = text[span[0]:span[1]]
    hit = re.search(rf"^([ \t]*{re.escape(field)}:[ \t]*)(.+?)(?=[ \t]*\r?$)", body,
                    re.MULTILINE)
    if not hit:
        return False, None
    old = hit.group(2).strip()
    if len(old) >= 2 and old[0] == old[-1] and old[0] in "\"'":
        old = old[1:-1]
    # The file always yields a string; the caller may pass an int for
    # numeric fields. Comparing those directly makes every numeric write look
    # like a change, so the file is rewritten, re-backed-up and re-sealed on
    # every run even when nothing moved.
    if old == str(value):
        return False, old
    rendered = value if isinstance(value, int) else yaml_scalar(str(value),
                                                                field)
    new_body = (body[:hit.start()] + f"{hit.group(1)}{rendered}"
                + body[hit.end():])
    if not dry_run:
        backup(path, stamp)
        write_config(path, text[:span[0]] + new_body + text[span[1]:])
    return True, old


def insert_backend(path, spec, stamp, dry_run):
    """Append a backend into the file's `backends:` mapping."""
    text = read_config(path)
    # list_backends, not find_backend: an aliased entry (`name: *anchor`) has
    # no body to locate but the key is taken, and adding a second one lets
    # YAML silently drop whichever it parses first.
    if spec["name"] in list_backends(text):
        return False, "already declared"
    span = backends_block(text)
    if not span:
        return False, "no backends: block"

    body = text[span[0]:span[1]]
    first = re.match(r"^(\s+)", body)
    indent = first.group(1) if first else "  "
    field = indent + "  "

    lines = [
        f"\n{indent}# added by forge-provider.py "
        f"{dt.date.today().isoformat()}\n",
        f"{indent}{yaml_scalar(spec['name'], 'name')}:\n",
        f"{field}type: api\n",
        f"{field}format: {yaml_scalar(spec['format'], 'format')}\n",
        f"{field}base_url: {yaml_scalar(spec['base_url'], 'base_url')}\n",
        f"{field}api_key_env: {yaml_scalar(spec['key_env'], 'key_env')}\n",
        f"{field}model: {yaml_scalar(spec['model'], 'model')}\n",
        f"{field}max_tokens: {spec['max_tokens']}\n",
        f"{field}timeout_s: {spec['timeout_s']}\n",
        f"{field}stream: false\n",
    ]
    if not dry_run:
        backup(path, stamp)
        write_config(path, text[:span[1]].rstrip("\n") + "\n"
                        + "".join(lines) + text[span[1]:])
    return True, None


def set_default(path, name, stamp, dry_run):
    """Make one backend the default and clear the flag from the rest.

    Each edit shifts every offset after it, so the spans are recomputed on the
    rewritten text at each step rather than taken once up front. Getting this
    wrong makes the command look idempotent-ish -- it reports a change every
    run because a second `default: true` it thought it had removed is still
    there.
    """
    text = read_config(path)
    if not find_backend(text, name):
        return False, "not declared"

    changed = False
    for other in list_backends(text):
        span = find_backend(text, other)
        if not span:
            continue
        key_line = re.search(rf"^([ \t]*){re.escape(other)}:[ \t\r]*$", text,
                             re.MULTILINE)
        indent_of = len(key_line.group(1)) if key_line else 2
        body = text[span[0]:span[1]]
        has = re.search(r"^[ \t]*default:[ \t]*true[ \t\r]*$", body, re.MULTILINE)
        if other == name and not has:
            fields = re.search(r"^([ \t]+)\S", body, re.MULTILINE)
            # A backend with no field lines of its own gives no indent to
            # copy; one level deeper than its key is the only safe guess.
            pad = fields.group(1) if fields else " " * (indent_of + 2)
            text = (text[:span[1]].rstrip("\n") + f"\n{pad}default: true\n"
                    + text[span[1]:])
            changed = True
        elif other != name and has:
            new_body = re.sub(r"^[ \t]*default:[ \t]*true[ \t\r]*$\n?", "",
                              body, flags=re.MULTILINE)
            text = text[:span[0]] + new_body + text[span[1]:]
            changed = True

    if changed and not dry_run:
        backup(path, stamp)
        write_config(path, text)
    return changed, None


def reseal_trust(paths, dry_run):
    """Re-record trust for gate.yaml files this run actually wrote.

    Callers must pass only the files they edited. Re-sealing a file this run
    did not touch would authorize whatever it already said -- including an
    edit made by someone else, which is the case forge's trust gate exists to
    catch.

    forge hashes the credential-bearing fields of the backends block and
    refuses a config whose hash moved (trust.py:125). Editing base_url or
    api_key_env therefore silently un-trusts the file: the backend is dropped
    and the review falls back to whatever else resolves, with one line on
    stderr as the only clue. Every editing command here re-seals, so the next
    session does not have to read trust.py to find out why its backend
    vanished.

    The user-level config carries no per-file trust record, so it is skipped.
    """
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
        import yaml  # noqa: PLC0415 - optional, only needed when re-sealing
        from code_forge.trust import record_trust  # noqa: PLC0415
    except ImportError as exc:
        print(f"\ncould not re-seal trust ({exc}); run `code-forge trust` "
              "in each repo before its next review", file=sys.stderr)
        return 0

    sealed = 0
    for path in paths:
        if path == USER_CONFIG:
            continue
        try:
            data = yaml.safe_load(read_config(path))
        except (OSError, yaml.YAMLError):
            # A file we cannot parse is not one we just edited.
            continue
        if not isinstance(data, dict) or "backends" not in data:
            continue
        if not dry_run:
            # A failure here leaves the file edited but untrusted, which is
            # exactly the silent state this function exists to prevent.
            try:
                record_trust(path, data)
            except OSError as exc:
                print(f"  could not re-seal {path}: {exc}", file=sys.stderr)
                continue
        sealed += 1
    return sealed


def report_trust(sealed, dry_run):
    if not sealed:
        return
    verb = "would re-seal" if dry_run else "re-sealed"
    print(f"{verb} trust on {sealed} gate.yaml file(s)")


def probe(base_url, model, key, fmt):
    """Send one real request. A 401 here beats a careful diff."""
    base = base_url.rstrip("/")
    if fmt == "anthropic":
        url, headers = base + "/v1/messages", {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    else:
        url, headers = base + "/chat/completions", {
            "Authorization": f"Bearer {key}",
            "content-type": "application/json",
        }
    payload = {"model": model, "max_tokens": PROBE_MAX_TOKENS,
               "messages": [{"role": "user", "content": "ping"}]}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
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
            has_text = ("content" in doc or "choices" in doc) \
                if isinstance(doc, dict) else False
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
            out = subprocess.run(["pass", key_pass], capture_output=True,
                                 text=True, timeout=30)
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
        for name in list_backends(text):
            span = find_backend(text, name)
            if not span:
                continue
            url = read_field(text, name, "base_url") or "?"
            model = read_field(text, name, "model") or "?"
            is_def = bool(re.search(
                r"^[ \t]*default:[ \t]*true[ \t\r]*$",
                text[span[0]:span[1]], re.MULTILINE
            ))
            seen.setdefault(name, {"urls": {}, "model": model, "n": 0,
                                   "default_in": 0})
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
            print(f"      default flag set in only {info['default_in']}"
                  f" of {info['n']} -- a review's backend depends on the repo")
    return 0


def cmd_add(args):
    key = resolve_key(args.key_pass, args.key_env)
    if not key:
        print(f"could not resolve a key from "
              f"{args.key_pass or args.key_env}", file=sys.stderr)
        return 2

    spec = {
        "name": args.name,
        "base_url": args.base_url,
        "model": args.model,
        "format": args.format,
        "key_env": args.key_env or (args.name.upper().replace("-", "_")
                                    + "_API_KEY"),
        "max_tokens": args.max_tokens,
        "timeout_s": args.timeout_s,
    }

    # Reject unwritable values before the probe, not on the first file. An
    # add that dies partway leaves some configs carrying the backend and the
    # rest not, which is harder to notice than a refusal up front.
    for field in ("name", "base_url", "model", "format", "key_env"):
        try:
            yaml_scalar(str(spec[field]), field)
        except ValueError as exc:
            print(f"refusing to write: {exc}", file=sys.stderr)
            return 2

    print(f"probing {spec['base_url']} ({spec['format']}, {spec['model']})"
          " ... ", end="", flush=True)
    ok, detail = probe(spec["base_url"], spec["model"], key, spec["format"])
    print(detail)
    if not ok:
        print("\nendpoint did not answer; nothing written", file=sys.stderr)
        return 3

    stamp = dt.datetime.now().strftime(BACKUP_FMT)
    configs = find_configs()
    added = skipped = 0
    written = []
    for path in configs:
        did, why = insert_backend(path, spec, stamp, args.dry_run)
        if did:
            added += 1
            written.append(path)
        else:
            skipped += 1
            if why != "already declared":
                print(f"  skipped {path}: {why}")

    verb = "would add" if args.dry_run else "added"
    print(f"\n{verb} '{spec['name']}' to {added} file(s), "
          f"{skipped} skipped")
    if added:
        report_trust(reseal_trust(written, args.dry_run), args.dry_run)
    if added and not args.dry_run:
        print(f"backups: <file>{stamp}")
        print(f"\nexport {spec['key_env']} in your shell rc "
              + (f"(pass {args.key_pass})" if args.key_pass else ""))
        print(f"make it the review default: "
              f"{pathlib.Path(sys.argv[0]).name} default {spec['name']}")
    return 0


def cmd_set(args):
    configs = [p for p in find_configs()
               if find_backend(read_config(p), args.name)]
    if not configs:
        print(f"no config declares '{args.name}'", file=sys.stderr)
        return 1

    text = read_config(configs[0])
    model = args.model or read_field(text, args.name, "model")
    fmt = args.format or read_field(text, args.name, "format") or "anthropic"
    url = args.base_url or read_field(text, args.name, "base_url")

    edits = [(f, v) for f, v in (
        ("base_url", args.base_url), ("model", args.model),
        ("max_tokens", args.max_tokens), ("timeout_s", args.timeout_s),
        ("api_key_env", args.key_env), ("format", args.format),
    ) if v is not None]
    if not edits:
        print("nothing to change", file=sys.stderr)
        return 2

    key = resolve_key(args.key_pass, args.key_env
                      or read_field(text, args.name, "api_key_env"))

    # The probe uses one file's credentials. If the configs disagree about
    # which env var holds the key, a pass here does not vouch for the rest.
    envs = {read_field(read_config(p), args.name, "api_key_env") or "(unset)"
            for p in configs}
    if len(envs) > 1:
        print(f"note: '{args.name}' reads its key from {sorted(envs)} across "
              "these configs; the probe covers only the first",
              file=sys.stderr)
    if key and url and model:
        print(f"probing {url} ({fmt}, {model}) ... ", end="", flush=True)
        ok, detail = probe(url, model, key, fmt)
        print(detail)
        if not ok:
            print("\nendpoint did not answer; nothing written",
                  file=sys.stderr)
            return 3
    elif any(f in ("base_url", "model", "format") for f, _ in edits):
        # Retargeting without a probe is how a backend ends up pointing at an
        # endpoint nobody checked. Fields like timeout_s cannot break routing,
        # so those still go through.
        print("cannot probe: no key resolved. Pass --key-pass or --key-env, "
              "or change only non-routing fields.", file=sys.stderr)
        return 3

    stamp = dt.datetime.now().strftime(BACKUP_FMT)

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
    for path in configs:
        hits = []
        for field, value in edits:
            did, old = write_field(path, args.name, field, value, stamp,
                                   args.dry_run)
            if did:
                hits.append(f"{field}: {old} -> {value}")
        if hits:
            changed += 1
            written.append(path)
            print(f"  {path}")
            for h in hits:
                print(f"      {h}")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {changed} of {len(configs)} file(s)")
    if changed:
        report_trust(reseal_trust(written, args.dry_run), args.dry_run)
    if changed and not args.dry_run:
        print(f"backups: <file>{stamp}")
    return 0


def cmd_default(args):
    stamp = dt.datetime.now().strftime(BACKUP_FMT)
    configs = find_configs()
    changed = missing = 0
    written = []
    for path in configs:
        did, why = set_default(path, args.name, stamp, args.dry_run)
        if did:
            changed += 1
            written.append(path)
        elif why == "not declared":
            missing += 1
    verb = "would set" if args.dry_run else "set"
    print(f"{verb} '{args.name}' default in {changed} file(s)")
    if missing:
        print(f"{missing} file(s) do not declare it -- run "
              f"`add {args.name}` there first")
    if changed:
        report_trust(reseal_trust(written, args.dry_run), args.dry_run)
    if changed and not args.dry_run:
        print(f"backups: <file>{stamp}")
    return 0


def cmd_rollback(args):
    restored = 0
    written = []
    for path in find_configs():
        backups = sorted(path.parent.glob(path.name + ".bak-provider-*"))
        if not backups:
            continue
        print(f"  {path}\n      <- {backups[-1].name}")
        if not args.dry_run:
            shutil.copy2(backups[-1], path)
        restored += 1
        written.append(path)
    verb = "would restore" if args.dry_run else "restored"
    print(f"\n{verb} {restored} file(s)")
    if restored:
        report_trust(reseal_trust(written, args.dry_run), args.dry_run)
    return 0


def cmd_trust(args):
    """Report which gate.yaml files forge currently refuses, and optionally fix."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
        import io  # noqa: PLC0415
        import contextlib  # noqa: PLC0415
        import yaml  # noqa: PLC0415
        from code_forge.trust import is_trusted  # noqa: PLC0415
    except ImportError as exc:
        print(f"cannot import forge's trust module ({exc})", file=sys.stderr)
        return 2

    untrusted = []
    checked = 0
    for path in find_configs():
        if path == USER_CONFIG:
            continue
        try:
            data = yaml.safe_load(read_config(path))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict) or "backends" not in data:
            continue
        checked += 1
        # is_trusted prints its own advice to stderr; we render our own.
        with contextlib.redirect_stderr(io.StringIO()):
            if not is_trusted(path, data):
                untrusted.append(path)

    print(f"{checked} gate.yaml file(s) checked, "
          f"{len(untrusted)} not trusted")
    for path in untrusted:
        print(f"  {path}")
    if untrusted:
        print("\nforge drops the backends block of an untrusted file and "
              "falls back to whatever else resolves.")
        if args.fix:
            sealed = reseal_trust(untrusted, args.dry_run)
            report_trust(sealed, args.dry_run)
        else:
            print(f"re-seal them: {pathlib.Path(sys.argv[0]).name} trust --fix")
    return 0


def cmd_rename(args):
    """Rename a backend everywhere, carrying its default flag and trust."""
    stamp = dt.datetime.now().strftime(BACKUP_FMT)
    configs = find_configs()
    touched = []
    clashes = []

    for path in configs:
        text = read_config(path)
        if not find_backend(text, args.old):
            continue
        if find_backend(text, args.new):
            clashes.append(path)
            continue
        touched.append(path)

    if clashes:
        print(f"'{args.new}' already exists in {len(clashes)} file(s); "
              "resolve those by hand first:", file=sys.stderr)
        for path in clashes:
            print(f"  {path}", file=sys.stderr)
        return 1
    if not touched:
        print(f"no config declares '{args.old}'", file=sys.stderr)
        return 1

    for path in touched:
        text = read_config(path)
        # Only the key line. A backend name can also appear in a comment or in
        # some other backend's prose, and rewriting those changes meaning.
        new_text = re.sub(
            rf"^([ \t]+){re.escape(args.old)}:([ \t\r]*)$",
            lambda m, n=args.new: f"{m.group(1)}{n}:{m.group(2)}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if new_text == text:
            continue
        print(f"  {path}")
        if not args.dry_run:
            backup(path, stamp)
            write_config(path, new_text)

    verb = "would rename" if args.dry_run else "renamed"
    print(f"\n{verb} '{args.old}' -> '{args.new}' in {len(touched)} file(s)")
    report_trust(reseal_trust(touched, args.dry_run), args.dry_run)
    if not args.dry_run:
        print(f"backups: <file>{stamp}")
    return 0


def main():
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
    p.add_argument("--format", choices=["anthropic", "openai"],
                   default="openai")
    p.add_argument("--key-pass", help="pass(1) entry holding the key")
    p.add_argument("--key-env", help="env var name (default: NAME_API_KEY)")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("set", help="change fields of an existing backend")
    p.add_argument("name")
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--format", choices=["anthropic", "openai"])
    p.add_argument("--key-pass")
    p.add_argument("--key-env")
    p.add_argument("--max-tokens", type=int)
    p.add_argument("--timeout-s", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("default", help="make one backend the review default")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_default)

    p = sub.add_parser("rename", help="rename a backend everywhere")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_rename)

    p = sub.add_parser("trust",
                       help="show which gate.yaml files forge refuses")
    p.add_argument("--fix", action="store_true",
                   help="re-seal every untrusted file")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_trust)

    p = sub.add_parser("rollback", help="restore the newest backup everywhere")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_rollback)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

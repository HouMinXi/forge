"""Locate changes to explicit `# noqa` comments between two commits.

Answers one narrow question: which explicit suppression comments moved, were
added, removed or had their rule codes changed. Anything wider is refused on
purpose. Ruff's actual suppression counts, technical debt, a quality score and
CI gating are all outside this measurement, and the output says so rather than
letting a number imply more than it proves.

Two properties make the comparison honest:

* Both sides are read from Git objects at fixed revisions, never from the
  working tree, so an uncommitted edit cannot enter a comparison.
* A suppression is paired to its counterpart by a unique source anchor (the
  code text before the comment, then one and two lines of context). When the
  anchor is not unique the site is reported as unmatched instead of guessed,
  so a reshuffle cannot masquerade as an edit.

Every failure is visible: an unreadable blob, an unparseable file, a changed
path inventory or a rewritten `unclassified` directive produces `unavailable`
or `incomparable` with the failing paths listed, never a zero. Exit codes:
0 comparable, 2 unavailable, 3 incomparable.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
import signal
import platform
import re
import subprocess
import sys
import tokenize

SCOPE = "src/code_forge"
GIT_TIMEOUT_S = 8
DEFINITION = (
    "forge-noqa-comment-sites-v3"  # v3: an unmatched anchor on the other side counts as added/removed
)
COUNT_KEYS = ("explicit_sites", "blanket_sites", "unclassified_sites", "excluded_file_directives")


class Unavailable(ValueError):
    """An expected failure with a stable, non-sensitive reason code."""


DIRECTIVE = re.compile(r"#\s*noqa(?=[:\s]|$)(.*)", re.I)
EXPLICIT = re.compile(
    r":\s*([A-Za-z]+[0-9]+(?:\s*,\s*[A-Za-z]+[0-9]+)*)(?:\s{2,}(?![A-Za-z]+[0-9]+\b)\S.*|\s+(?:--?\s|#).*)?"
)
FILE_DIRECTIVE = re.compile(r"#\s*(?:ruff|flake8)\s*:\s*noqa\b", re.I)


@dataclass
class Source:
    counts: Counter
    rules: Counter
    sites: list
    anchors: Counter
    contexts: tuple
    code_keys: set


def scan(raw):
    # tokenize() does not treat a lone CR as a line break, while this function
    # counts physical lines. Normalise the bytes once, before either reads them,
    # so rows and lines describe the same text.
    blob = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    ast.parse(blob)
    counts, rules, sites, anchors = Counter(), Counter(), [], Counter()
    line_has_code = set()
    directive_columns = {}
    for t in tokenize.tokenize(BytesIO(blob).readline):
        kind, comment, (row, col), _, _ = t
        if kind == tokenize.ERRORTOKEN:
            raise ValueError("unclassifiable source token")
        if kind not in (
            tokenize.COMMENT,
            tokenize.ENCODING,
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
        ):
            line_has_code.add(row)
            line_has_code.add(t.end[0])
        if kind != tokenize.COMMENT:
            continue
        codes = []
        if FILE_DIRECTIVE.match(comment):
            category = "excluded_file_directives"
        elif match := DIRECTIVE.match(comment):
            tail = match[1].strip()
            if not tail or tail.startswith(("- ", "-- ", "#")):
                category = "blanket_sites"
            elif result := EXPLICIT.fullmatch(tail):
                category = "explicit_sites"
                codes = sorted(set(re.findall(r"[A-Z]+[0-9]+", result[1].upper())))
            else:
                category = "unclassified_sites"
        else:
            continue
        directive_columns[row] = col
        counts[category] += 1
        rules.update(codes)
        sites.append((row, category, codes))

    # Tokenize's line field may span several physical lines (multiline strings).
    # Count every physical line from the decoded input, including unannotated
    # duplicates, and require a code token before any inline anchor.
    encoding, _ = tokenize.detect_encoding(BytesIO(blob).readline)
    lines = blob.decode(encoding).removesuffix("\n").split("\n")
    keys = {
        row: line[: directive_columns.get(row)].rstrip(" \t\r\n") for row, line in enumerate(lines, 1)
    }
    # Standalone directives have no anchor even when their text is unique.
    # Surrounding lines stay in the original order and keep all other text.
    contexts = {
        row: (
            key,
            tuple(keys.get(i) for i in range(row - 1, row + 2)),
            tuple(keys.get(i) for i in range(row - 2, row + 3)),
        )
        for row, key in keys.items()
        if key
    }
    anchors.update(key for key in keys.values() if key)
    return Source(
        counts,
        rules,
        [
            (
                contexts.get(row) if row in line_has_code else None,
                {"line": row, "category": category, "rules": codes},
            )
            for row, category, codes in sites
        ],
        anchors,
        tuple(Counter(c[i] for c in contexts.values()) for i in (1, 2)),
        {(i, context[i]) for row, context in contexts.items() if row in line_has_code for i in range(3)},
    )


def classify(blob):
    data = scan(blob)
    return data.counts, data.rules


def detail(path, old, new):
    changes = {"added": [], "removed": [], "changed": []}
    unmatched = []
    result = {"confirmed_changes": changes, "unmatched": unmatched}
    if old is None or new is None:
        changes["added" if old is None else "removed"] = [
            site for _, site in (new if old is None else old).sites
        ]
        return result

    code_keys = old.code_keys & new.code_keys

    def candidate(context):
        if context is None:
            return None
        line = context[0]
        if new.anchors[line] == old.anchors[line] == 1 and (0, line) in code_keys:
            return (0, line)
        for radius, window in enumerate(context[1:], 1):
            if (radius, window) in code_keys and (
                new.contexts[radius - 1][window] == old.contexts[radius - 1][window] == 1
            ):
                return (radius, window)
        return None

    def counterpart_missing(context, other):
        """True when no line on the other side carries this anchor text.

        That is the shape of code being written or deleted together with its
        directive, so the site counts as added or removed. Only a counterpart
        that exists but cannot be identified is ambiguous.
        """
        return context is not None and not other.anchors[context[0]]

    def record_unmatched(side, context, site, other):
        if context is None:
            reason = "no_source_anchor"
        elif (0, context[0]) not in other.code_keys:
            reason = "no_code_anchor_on_other_side"
        else:
            reason = "repeated_source_anchor"
        unmatched.append(dict(site, side=side, path=path, reason=reason))

    new_by_anchor = {key: site for context, site in new.sites if (key := candidate(context)) is not None}
    consumed = set()
    for context, site in old.sites:
        key = candidate(context)
        if key is None:
            if counterpart_missing(context, new):
                changes["removed"].append(site)
            else:
                record_unmatched("before", context, site, new)
            continue
        match = new_by_anchor.get(key)
        if match is None:
            changes["removed"].append(site)
            continue
        consumed.add(key)
        if (site["category"], site["rules"]) != (match["category"], match["rules"]):
            changes["changed"].append({"before": site, "after": match})
    for context, site in new.sites:
        key = candidate(context)
        if key is None:
            if counterpart_missing(context, old):
                changes["added"].append(site)
            else:
                record_unmatched("after", context, site, old)
        elif key not in consumed:
            changes["added"].append(site)
    return result


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_ALLOW_PROTOCOL="",
    )
    # Own session so a timeout can take the whole tree down: a git reached
    # through a wrapper leaves children holding the pipe after the leader dies.
    proc = subprocess.Popen(
        ["git", "--no-replace-objects", "--no-lazy-fetch", "-C", repo, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=GIT_TIMEOUT_S)
    except BaseException:
        # Timeout or a read that ran out of memory: the child is still alive and
        # holding both pipe ends, so kill the group and reap it. wait(), not a
        # second communicate(): a descendant that left the group still holds the
        # write end, and waiting for EOF would hand it the clock.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        raise
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, proc.args)
    return out


def measure(repo, revision, cache=None):
    cache = {} if cache is None else cache
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise Unavailable("invalid_commit_id")
    try:
        kind = git(repo, "cat-file", "-t", revision).strip()
    except subprocess.CalledProcessError as exc:
        raise Unavailable("commit_unreadable") from exc
    if kind != b"commit":
        raise Unavailable("not_a_commit")
    entries = git(repo, "ls-tree", "-rz", "--full-tree", revision, "--", SCOPE).split(b"\0")
    sites, rules, manifest, paths, details, failures = Counter(), Counter(), [], [], [], []
    for entry in sorted(filter(None, entries), key=lambda entry: entry.split(b"\t", 1)[1]):
        metadata, path = entry.split(b"\t", 1)
        mode, kind, digest = metadata.split()
        if not path.endswith(b".py") and mode != b"160000":
            continue
        display_path = path.decode("utf-8", errors="surrogateescape")
        try:
            if not path.startswith((SCOPE + "/").encode()) or mode == b"160000":
                raise Unavailable("unsupported_scope_entry")
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise Unavailable("unsupported_source_mode")
            if any(0xDC80 <= ord(c) <= 0xDCFF for c in display_path):
                raise Unavailable("unsupported_path_encoding")
            d = digest.decode("ascii")
            if d not in cache:
                try:
                    source = git(repo, "cat-file", "blob", d)
                except MemoryError as exc:
                    raise Unavailable("blob_too_large") from exc
                except subprocess.TimeoutExpired as exc:
                    raise Unavailable("blob_read_timeout") from exc
                except (OSError, subprocess.SubprocessError) as exc:
                    raise Unavailable("blob_unreadable") from exc
                try:
                    cache[d] = scan(source)
                except MemoryError as exc:
                    raise Unavailable("blob_too_large") from exc
                except (
                    ValueError,
                    SyntaxError,
                    tokenize.TokenError,
                    RecursionError,
                    LookupError,
                ) as exc:
                    raise Unavailable("source_not_parseable") from exc
            data = cache[d]
        except Unavailable as exc:
            failures.append({"path": display_path, "reason": str(exc)})
            continue
        sites.update(data.counts)
        rules.update(data.rules)
        manifest.append(entry)
        paths.append(path)
        details.append(
            {
                "path": display_path,
                "blob": d,
                "counts": {k: data.counts[k] for k in COUNT_KEYS},
                "rule_site_counts": dict(sorted(data.rules.items())),
            }
        )
    if failures:
        return {
            "status": "unavailable",
            "revision": revision,
            "reason": failures[0]["reason"],
            "failures": failures,
        }
    return {
        "status": "measured",
        "revision": revision,
        "definition": DEFINITION,
        "scope": SCOPE + "/**/*.py (tracked regular blobs)",
        "python": platform.python_implementation() + " " + platform.python_version(),
        "files": len(manifest),
        "path_inventory_sha256": sha256(b"\0".join(paths)).hexdigest(),
        "tree_inventory_sha256": sha256(b"\0".join(manifest)).hexdigest(),
        "counts": {k: sites[k] for k in COUNT_KEYS},
        "rule_site_counts": dict(sorted(rules.items())),
        "file_counts": details,
    }


def compare(before, after):
    if any(record.get("status") != "measured" for record in (before, after)):
        return {"status": "unavailable"}
    fields = ("definition", "scope", "python", "path_inventory_sha256")
    drift = [key for key in fields if before[key] != after[key]]
    if drift:
        return {"status": "incomparable", "mismatched_fields": drift}
    files = (before.get("files"), after.get("files"))
    if files == (0, 0):
        # Neither side holds a tracked Python file under the scope: there is no
        # measurement to compare, and reporting 0 against 0 would look like one.
        return {"status": "unavailable", "reason": "empty_scope_on_both_sides"}
    if any(record["counts"].get("unclassified_sites") for record in (before, after)):
        return {"status": "incomparable", "reason": "unclassified_directives"}
    before_rules = before.get("rule_site_counts", {})
    after_rules = after.get("rule_site_counts", {})
    deltas = {
        k: after_rules.get(k, 0) - before_rules.get(k, 0)
        for k in sorted(before_rules.keys() | after_rules.keys())
    }
    return {
        "status": "comparable",
        "delta": {k: after["counts"][k] - before["counts"][k] for k in before["counts"]},
        "rule_site_delta": {k: v for k, v in deltas.items() if v},
    }


def snapshot(repo, revision, cache=None):
    try:
        return measure(repo, revision, cache)
    except Unavailable as exc:
        reason = str(exc)
    except subprocess.TimeoutExpired:
        reason = "git_timeout"
    except (OSError, subprocess.SubprocessError, ValueError):
        reason = "git_input_unreadable"
    return {
        "status": "unavailable",
        "revision": revision if re.fullmatch("[0-9a-f]{40}", revision) else None,
        "reason": reason,
    }


def summary(document):
    comparison = document["comparison"]
    head = [f"Status: {comparison['status']}"]
    if comparison.get("reason") == "empty_scope_on_both_sides":
        # Neither side measured anything; printing "0 sites across 0 paths"
        # would read as a measurement instead of the signal it is.
        head.append("Reason: empty_scope_on_both_sides")
        return "\n".join(head)
    for side in ("before", "after"):
        snapshot = document[side]
        if snapshot["status"] == "measured":
            head.append(
                f"{side}: {snapshot['counts']['explicit_sites']} explicit sites "
                f"across {snapshot['files']} paths"
            )
        else:
            head.append(f"{side}: unavailable ({snapshot['reason']})")
            head.extend(f"  {ascii(f['path'])}: {f['reason']}" for f in snapshot.get("failures", ()))
    if comparison.get("reason"):
        head.append(f"Reason: {comparison['reason']}")
    if comparison.get("mismatched_fields"):
        head.append("Mismatched fields: " + ", ".join(comparison["mismatched_fields"]))
    if "file_details" not in comparison:
        return "\n".join(head)

    totals = Counter()
    unresolved = 0
    body = []

    def site_text(site):
        return f"{site['line']}: {site['category']} ({', '.join(site['rules']) or '-'})"

    for entry in comparison["file_details"]:
        path = ascii(entry["path"])
        for action, entries in entry["confirmed_changes"].items():
            totals[action] += len(entries)
            for change in entries:
                text = (
                    site_text(change["before"]) + " -> " + site_text(change["after"])
                    if action == "changed"
                    else site_text(change)
                )
                body.append(f"  Confirmed {action}: {path}:{text}")
        for item in entry["unmatched"]:
            unresolved += 1
            body.append(f"  Unmatched {item['side']}: {path}:{site_text(item)} ({item['reason']})")

    middle = [
        f"Confirmed: {totals['added']} added, {totals['removed']} removed, "
        f"{totals['changed']} changed. Unmatched: {unresolved}."
    ]
    if "delta" not in comparison:
        middle.append("No whole-scope delta.")
    for action in ("added", "removed"):
        middle.extend(f"Path {action}: {ascii(path)}" for path in comparison[f"paths_{action}"])
    return "\n".join(head + middle + body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary", action="store_true", help="emit human-readable details instead of JSON"
    )
    parser.add_argument("--repo", required=True, help="path to a local Git repository with both commits")
    parser.add_argument("before", help="full baseline commit SHA-1")
    parser.add_argument("after", help="full subject commit SHA-1")
    args = parser.parse_args(argv)
    cache = {}
    before = snapshot(args.repo, args.before, cache)
    after = snapshot(args.repo, args.after, cache)
    comparison = compare(before, after)
    if comparison["status"] != "unavailable":
        old = {f["path"]: f["blob"] for f in before["file_counts"]}
        new = {f["path"]: f["blob"] for f in after["file_counts"]}
        comparison["paths_added"] = sorted(new.keys() - old.keys())
        comparison["paths_removed"] = sorted(old.keys() - new.keys())
        comparison["file_details"] = [
            dict(path=path, **detail(path, cache.get(old.get(path)), cache.get(new.get(path))))
            for path in sorted(old.keys() | new.keys())
            if old.get(path) != new.get(path)
        ]
    document = {
        "schema": "forge-noqa-comparison-v3",
        "definition": DEFINITION,
        "headline_count": "explicit_sites only; excludes blanket, file-level and unclassified",
        "before": before,
        "after": after,
        "comparison": comparison,
    }
    print(summary(document) if args.summary else json.dumps(document, sort_keys=True, indent=2))
    return {"comparable": 0, "unavailable": 2, "incomparable": 3}[comparison["status"]]


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: Apache-2.0
"""Context sources: external facts that go into the L1 prompt.

Phase 59-B1. A ContextSource wraps one fact provider (today: graph
triage; later: an MCP server) behind a fixed contract:

  - every fact row carries where it came from (source name) and, when
    the provider indexes a snapshot of the tree, which commit that
    snapshot was taken at;
  - a snapshot taken at a commit other than the one under review is
    refused unless the operator opts in, because facts about an older
    tree are worse than no facts (they look authoritative);
  - a provider that raises is recorded as an error, not swallowed into
    an empty table;
  - the rendered text is byte-for-byte what cli.py built before this
    module existed, so the shared prompt prefix stays cacheable.

The blast-radius block at cli.py:3722-3761 is the prototype this
generalises. Its output format is kept verbatim in render_blast_radius.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    from .advisory import AdvisoryFinding


@dataclass(frozen=True)
class FactRow:
    """One row of context from one source."""

    entity: str
    file: str
    downstream: str
    dependents: str
    source: str
    origin_line: Optional[int] = None
    # location -> the post-image source line at that location, and
    # location -> the nearest def/class header above it. Filled by
    # RemovedSymbolReaders (A4-0c); empty for graph_triage rows. The
    # dicts are never mutated after construction.
    # Stored as sorted (key, value) tuples so the frozen dataclass stays
    # hashable (frozen=True derives __hash__ from every field; a dict
    # here made every FactRow, graph_triage rows included, unhashable).
    # Construct with plain dicts; read .snippets / .enclosing as dicts.
    _snippets: tuple = field(default=(), repr=False)
    _enclosing: tuple = field(default=(), repr=False)

    def __init__(self, entity, file, downstream, dependents, source,
                 origin_line=None, snippets=None, enclosing=None):
        object.__setattr__(self, "entity", entity)
        object.__setattr__(self, "file", file)
        object.__setattr__(self, "downstream", downstream)
        object.__setattr__(self, "dependents", dependents)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "origin_line", origin_line)
        object.__setattr__(self, "_snippets", tuple(sorted((snippets or {}).items())))
        object.__setattr__(self, "_enclosing", tuple(sorted((enclosing or {}).items())))

    @property
    def snippets(self) -> dict:
        return dict(self._snippets)

    @property
    def enclosing(self) -> dict:
        return dict(self._enclosing)


@dataclass
class GatherResult:
    rows: list[FactRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    snapshot_shas: dict[str, Optional[str]] = field(default_factory=dict)


class ContextSource(Protocol):
    name: str

    def snapshot_sha(self) -> Optional[str]:
        """Commit the source's index was built at, or None if it has no
        snapshot (computes facts from the working tree on demand)."""
        ...

    def facts(self, changed_files: list[str], diff_text: str) -> list[FactRow]:
        ...


@dataclass
class GraphTriageSource:
    """Adapter over GraphTriageRunner.run.

    Snapshot sha comes from graph.db metadata.git_head_sha when the
    backend is graphdb; sem computes on demand, so it reports None.
    """

    repo_root: Path
    name: str = "graph_triage"
    # The raw AdvisoryFindings from the last facts() call. cli.py seeds
    # each hold-cycle's GraphTriageRunner._cached_findings from this so
    # sem/graph.db is queried once per review, not once per round.
    findings_cache: Optional[list] = None

    def snapshot_sha(self) -> Optional[str]:
        from .graph_triage import _detect_backend
        backend = _detect_backend(self.repo_root, _gate_cfg(self.repo_root))
        if backend is None or backend[0] != "graphdb":
            return None
        return _graphdb_head_sha(Path(backend[1]))

    def facts(self, changed_files: list[str], diff_text: str) -> list[FactRow]:
        from .graph_triage import GraphTriageRunner
        runner = GraphTriageRunner()
        findings = runner.run(diff_text, self.repo_root)
        if runner.infra_errors:
            raise RuntimeError("; ".join(runner.infra_errors))
        self.findings_cache = list(findings)
        return [_adapt_advisory(f, self.name) for f in findings]


def _gate_cfg(repo_root: Path) -> dict:
    """gate.yaml as a dict; absent file is {}.

    A malformed or unreadable file is NOT {}: that would let
    _detect_backend pick a backend the operator disabled. It propagates
    so gather() records it under this source's name.
    """
    from .gate_check import load_gate_config
    try:
        return load_gate_config(repo_root / ".code-forge" / "gate.yaml")
    except FileNotFoundError:
        return {}


def _graphdb_head_sha(db: Path) -> Optional[str]:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            row = con.execute(
                "select value from metadata where key='git_head_sha'"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] else None


def _adapt_advisory(f: "AdvisoryFinding", source: str) -> FactRow:
    """Parse the AdvisoryFinding description exactly as cli.py did.

    Description shape: "name (impact: N downstream) -- top dependents: a, b".
    """
    desc = f.description
    parts = desc.split(" (impact: ", 1)
    ename = parts[0] if parts else "unknown"
    downstream = "0"
    deps = ""
    if len(parts) > 1:
        rest = parts[1]
        dp = rest.split(" downstream)", 1)
        downstream = dp[0] if dp else "0"
        if len(dp) > 1 and "-- top dependents: " in dp[1]:
            deps = dp[1].split("-- top dependents: ", 1)[1].strip()
    origin = None
    lr = getattr(f, "line_range", None)
    if lr and len(lr) >= 1 and isinstance(lr[0], int) and lr[0] > 0:
        origin = lr[0]
    return FactRow(
        entity=ename, file=f.file, downstream=downstream,
        dependents=deps, source=source, origin_line=origin,
    )


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
# `name=`, `name,`, `name)` or `name:` inside a def/signature line.
# The colon is the annotated form (`name: int = 3`); without it rule 2
# was blind to every typed parameter (review R2). This also matches
# call-site keyword args and dict keys, which is why the rule only
# fires when the name is gone from every + signature line AND a
# self.<name> read survives.
# A parameter name sits at the start of the signature, or right after a
# comma, possibly followed by an annotation, then `=`, `,`, `)` or `:`.
# A name that follows `=` is a default VALUE, not a parameter.
_PARAM = re.compile(
    r"(?:^|[(,])\s*\*{0,2}([A-Za-z_][A-Za-z0-9_]{3,})\s*(?:[:][^=,)]*)?(?=[=,):]|$)"
)
_COMMON = frozenset("""
self None True False return import from class pass elif else with
while break continue lambda yield raise except finally assert global
async await print range list dict tuple type super object
""".split())


@dataclass
class RemovedSymbolReaders:
    """For each identifier the diff removes, the post-image files that
    still reference it.

    The falsifier judging a refactor-shaped change ("dropped a parameter",
    "moved a path", "re-added a normalisation") needs one fact it cannot
    get from the hunk: does anything still read the thing that went
    away? A2/A4-0 measured three real regressions dismissed for lack of
    exactly this. It is a grep over the working tree, so there is no
    index and no snapshot (snapshot_sha is None); facts() runs after the
    diff has been applied, on the post-image.

    An identifier qualifies when it appears on a removed line, does not
    appear on any added line in the same file section, and is longer
    than three characters and not a Python keyword or builtin. Rows list
    reader locations as file:line, with "(test)" appended when the path
    is under a tests/ or testing/ directory, so the judge can weigh a
    surviving test call differently from a production read.
    """

    repo_root: Path
    name: str = "removed-symbol-readers"
    max_readers_per_symbol: int = 12
    # Above this many readers the token is vocabulary, not an API:
    # measured on 20 real trees, `isinstance` 970, `values` 1072,
    # `boolean` 447 (from docstrings), `astropy` 7048. The identifiers
    # that mattered had 1-12. Rows are ordered fewest-readers-first for
    # the same reason.
    max_readers_to_report: int = 100
    # per-instance file cache for _lines_at; not part of the row identity
    _file_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def snapshot_sha(self) -> Optional[str]:
        return None

    def facts(self, changed_files: list[str], diff_text: str) -> list[FactRow]:
        rows: list[FactRow] = []
        # get_changed_files lists only files with an added line. A
        # deleted or shrink-only file is a removal of identifiers, so
        # the secondary filter here uses the paths the diff itself
        # names. An empty changed_files list still means "no extra
        # restriction", matching gather() callers that pass [].
        # The three readers below each walked the diff body on their own.
        # Parse it once per call and hand every reader the same lines;
        # hunk labels stay in for the signature reader.
        parsed = list(_diff_body_lines(diff_text, include_hunks=True))
        scope = _removal_scope(diff_text, changed_files, parsed_lines=parsed)
        # Rule 2 first (precise): parameter dropped from a signature while
        # the same file still reads self.<name>. Rule 1 misses this when
        # the name survives in a docstring on a + line (c08:
        # `store_cv_values=True` in prose, parameter gone from __init__,
        # fit() reads self.store_cv_values four times).
        for file, params in _dropped_parameters_by_file(diff_text, parsed_lines=parsed).items():
            if scope is not None and file not in scope:
                continue
            for name in sorted(params):
                reads = self._self_reads(file, name)
                if not reads:
                    continue
                # Merge in whole-word readers elsewhere (a test that still
                # passes the argument, a caller in another module) so the
                # judge sees both the self.<name> reads and who else
                # touches the name.
                others = [r for r in self._readers(name)
                          if not r.startswith(file + ":")]
                deps = "parameter removed from signature; self.%s still read at %s" % (
                    name, ", ".join(reads[: self.max_readers_per_symbol]))
                if others:
                    deps += "; also referenced at %s" % ", ".join(
                        others[: self.max_readers_per_symbol])
                locs = (reads[: self.max_readers_per_symbol]
                        + others[: self.max_readers_per_symbol])
                snip, encl = self._lines_at(locs)
                rows.append(FactRow(
                    entity=name, file=file,
                    downstream=str(len(reads) + len(others)),
                    dependents=deps, source=self.name,
                    snippets=snip, enclosing=encl,
                ))
        already = {(r.file, r.entity) for r in rows}
        # Rule 1: identifier gone from the file entirely, still read elsewhere.
        removed = _removed_identifiers_by_file(diff_text, parsed_lines=parsed)
        for file, idents in removed.items():
            if scope is not None and file not in scope:
                continue
            found: list[tuple[int, str, list[str]]] = []
            for ident in sorted(idents):
                if (file, ident) in already:
                    continue
                readers = self._readers(ident)
                if not readers or len(readers) > self.max_readers_to_report:
                    continue
                found.append((len(readers), ident, readers))
            for n, ident, readers in sorted(found):
                shown = readers[: self.max_readers_per_symbol]
                more = n - len(shown)
                deps = ", ".join(shown) + (" (+%d more)" % more if more else "")
                snip, encl = self._lines_at(shown)
                rows.append(FactRow(
                    entity=ident, file=file, downstream=str(n),
                    dependents=deps, source=self.name,
                    snippets=snip, enclosing=encl,
                ))
        return rows

    def _lines_at(self, locations: list[str]) -> tuple[dict, dict]:
        """For each "path:line[ (test)]" give the source line and the
        nearest def/class header above it, read from the working tree.
        A4-0b handed the judge addresses ("ridge.py:1077") and it
        answered that it could not see the code; the address was never
        the evidence. One read per file, cached on the instance."""
        cache = self._file_cache
        snippets: dict[str, str] = {}
        enclosing: dict[str, str] = {}
        for loc in locations:
            key = loc[:-7] if loc.endswith(" (test)") else loc
            path, _, lineno = key.rpartition(":")
            try:
                n = int(lineno)
            except ValueError:
                continue
            lines = cache.get(path)
            if lines is None:
                try:
                    lines = (self.repo_root / path).read_text(
                        encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                cache[path] = lines
            if not 1 <= n <= len(lines):
                continue
            snippets[key] = lines[n - 1].rstrip()
            # The enclosing def is the nearest header ABOVE with LESS
            # indentation than the read. "Nearest def above" alone
            # returned a nested helper (identity_estimator) for a read
            # in the enclosing fit() on the real sklearn tree.
            indent = len(lines[n - 1]) - len(lines[n - 1].lstrip())
            for i in range(n - 2, -1, -1):
                raw = lines[i]
                t = raw.lstrip()
                if not t:
                    continue
                d = len(raw) - len(t)
                if d < indent and t.startswith(("def ", "async def ", "class ")):
                    enclosing[key] = t.rstrip()
                    break
        return snippets, enclosing

    def _self_reads(self, file: str, name: str) -> list[str]:
        """Lines in `file` (post-image) that read self.<name>, excluding
        the assignment `self.<name> =` that a constructor would make."""
        proc = subprocess.run(
            ["git", "grep", "-n", "-E", "--",
             r"self\.%s\b" % re.escape(name), "--", file],
            cwd=self.repo_root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError("git grep failed (rc=%d): %s"
                               % (proc.returncode, proc.stderr.strip()[:200]))
        out = proc.stdout
        reads = []
        write = re.compile(r"self\.%s\s*=[^=]" % re.escape(name))
        for line in out.splitlines():
            split = _split_grep_line(line)
            if split is None:
                continue
            path, lineno, text = split
            t = text.strip()
            if t.startswith("#"):
                continue
            if write.match(t):
                continue  # the write, not a read
            reads.append("%s:%s" % (path, lineno))
        return reads

    def _readers(self, ident: str) -> list[str]:
        """file:line of every non-comment line in a tracked *.py file that
        contains `ident` as a whole word. Comment lines are excluded
        because a mention in prose is not a read."""
        # git grep exits 1 for "no match" (fine) and 128 for "not a
        # repo" / bad args (not fine). Anything but 0/1 is raised so
        # gather() records it as an error; an empty table here would
        # read as "no readers", the one answer this source must never
        # fabricate (module contract, lines 13-14).
        proc = subprocess.run(
            ["git", "grep", "-n", "-w", "-I", "--", ident, "--", "*.py"],
            cwd=self.repo_root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError("git grep failed (rc=%d): %s"
                               % (proc.returncode, proc.stderr.strip()[:200]))
        out = proc.stdout
        found: list[str] = []
        assign = re.compile(r"(self\.)?%s\s*=[^=]" % re.escape(ident))
        for line in out.splitlines():
            split = _split_grep_line(line)
            if split is None:
                continue
            path, lineno, text = split
            t = text.strip()
            if t.startswith("#"):
                continue
            if assign.match(t):
                continue  # `name = ...` / `self.name = ...` is a write
            tag = " (test)" if _is_test_path(path) else ""
            found.append("%s:%s%s" % (path, lineno, tag))
        return found


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    return any(p in ("tests", "test", "testing") for p in parts[:-1]) \
        or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")


# string literals and bracketed groups, for heuristics that must judge
# the code outside them. Handles escaped quotes and triple quotes.
_STR_LITERAL = re.compile(
    r'"""(?:\\.|(?!""")[^\\])*"""|'
    r"'''(?:\\.|(?!''')[^\\])*'''|"
    r'"(?:\\.|[^"\\])*"|'
    r"'(?:\\.|[^'\\])*'"
)
_STR_OR_BRACKET = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|" + _STR_LITERAL.pattern)

_GREP_LINE = re.compile(r"^(.*?):(\d+):(.*)$")


def _split_grep_line(line: str):
    """git grep -n prints path:lineno:text. The path may itself contain
    ':' (a Windows drive, a file literally named a:b.py) and so may the
    text; the line number is the one field that cannot. Anchor on it:
    the shortest path prefix followed by ':digits:'."""
    m = _GREP_LINE.match(line)
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3)


def _looks_like_code(text: str) -> bool:
    """A removed line whose tokens are worth grepping. Prose (docstring
    body, comments) removed from a file is not an API removal; on real
    trees its words (`cross`, `boolean`, `indicating`) each have dozens
    of readers and drown the one identifier that matters. Code lines
    carry at least one of = ( . : [ and do not start with # or a
    quote."""
    t = text.strip()
    if not t or t.startswith(("#", '"', "'")):
        return False
    head, _, tail = t.partition(" ")
    if head in ("return", "yield", "raise", "del", "await",
                "assert", "global", "nonlocal"):
        # statement keyword + a bare name is code with no punctuation
        # (`return value`); keyword + a sentence is a docstring line
        # (`return the computed value`). One or two tokens after the
        # keyword is a name or a short expression; more is prose.
        return len(tail.split()) <= 2
    if not any(ch in t for ch in "=(.:["):
        return False
    # Three or more consecutive lowercase words is prose ("values for
    # each alpha", "indicating if the"); code rarely has that outside a
    # string, and the identifiers we want (`store_cv_values`, `HEAD`,
    # `note_object`) never appear in such a run.
    # Judge that outside brackets and string literals: `combine(alpha,
    # beta, gamma)` and `f("a b c")` are code whose word runs live
    # inside the punctuation.
    outside = _STR_OR_BRACKET.sub(" ", t)
    return re.search(r"\b[a-z]+ [a-z]+ [a-z]+\b", outside) is None


def _diff_body_lines(diff_text: str, *, include_hunks: bool = False):
    """Yield (line, file) for every body line of a unified diff, with
    `file` the post-image path -- or, for a deleted file, the path that
    used to exist. git writes `+++ /dev/null` for deletions; keying a
    deleted module's identifiers under '/dev/null' makes facts() skip
    them (the changed_files guard never saw that path), which loses the
    one case where every remaining reader is broken by construction."""
    current: Optional[str] = None
    pre: Optional[str] = None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            pre = line[4:].strip()
            pre = pre[2:] if pre.startswith("a/") else pre
            continue
        if line.startswith("+++ "):
            current = line[4:].strip()
            current = current[2:] if current.startswith("b/") else current
            if current == "/dev/null":
                current = pre
            continue
        if current is None or line.startswith(("diff ", "index ")):
            continue
        if line.startswith("@@") and not include_hunks:
            continue
        yield line, current


def _removal_scope(diff_text: str, changed_files: list[str], *,
                   parsed_lines: Optional[list[tuple[str, str]]] = None) -> Optional[set[str]]:
    """Files whose removed identifiers this source may report.

    get_changed_files is additions-only. Callers that pass that list
    as the facts() scope would otherwise drop a deleted or shrink-only
    file. When changed_files is empty there is no extra restriction.
    Otherwise the scope is that list plus every path the diff itself
    names, including deletions.

    parsed_lines is a shared parse of diff_text (facts() parses once for
    all three readers); None re-parses here, an empty list stays empty.
    The shared list keeps @@ hunk labels for the signature reader; scope
    names files from body content only, so skip them exactly as a
    standalone parse would.
    """
    if not changed_files:
        return None
    named: set[str] = set()
    lines = _diff_body_lines(diff_text) if parsed_lines is None else parsed_lines
    for line, path in lines:
        if line.startswith("@@"):
            continue
        if path:
            named.add(path)
    return set(changed_files) | named


def _removed_identifiers_by_file(
    diff_text: str, *, parsed_lines: Optional[list[tuple[str, str]]] = None
) -> dict[str, set[str]]:
    """Identifiers on `-` lines that do not reappear on any `+` line of
    the same file. Anything that survives the change is not "removed".

    parsed_lines is a shared parse of diff_text; None re-parses here, an
    empty list stays empty. Hunk labels in a shared list need no special
    handling: only +/- lines are mined, and @@ lines are neither.
    """
    removed: dict[str, set[str]] = {}
    added: dict[str, set[str]] = {}
    current: Optional[str] = None
    lines = _diff_body_lines(diff_text) if parsed_lines is None else parsed_lines
    for line, current in lines:
        if line.startswith("-"):
            if _looks_like_code(line[1:]):
                removed.setdefault(current, set()).update(_IDENT.findall(line[1:]))
        elif line.startswith("+"):
            added.setdefault(current, set()).update(_IDENT.findall(line[1:]))
    out: dict[str, set[str]] = {}
    for file, idents in removed.items():
        keep = {i for i in idents - added.get(file, set())
                if i not in _COMMON}
        if keep:
            out[file] = keep
    return out


def _dropped_parameters_by_file(
    diff_text: str, *, parsed_lines: Optional[list[tuple[str, str]]] = None
) -> dict[str, set[str]]:
    """Compare uniquely paired signatures using visible enclosing scopes.

    Keep repeated definitions separate. When scope is missing, only a
    unique replacement in the same diff block supports correspondence.
    Unmatched or ambiguous signatures supply no parameter-removal claim.

    parsed_lines is a shared parse of diff_text; None re-parses here, an
    empty list stays empty. The parse must keep hunk labels, as this
    reader does on a standalone call.
    """
    def _sig_name(text: str) -> Optional[str]:
        m = re.match(r"\s*(async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return m.group(2) if m else None

    def _class_name(text: str) -> Optional[str]:
        m = re.match(r"\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return m.group(1) if m else None

    def _params_by_func(lines: list[tuple[int, str]]):
        records = []
        depth = 0
        in_sig = False
        in_triple: Optional[str] = None
        names: set[str] = set()
        scopes: list[tuple[int, str]] = []
        complete_scope = False
        for location, raw in lines:
            if raw.startswith("@@"):
                if not in_sig:
                    scopes = []
                    complete_scope = False
                    # Git's section label is context, not another signature.
                    raw = raw.split("@@", 2)[-1].removeprefix(" ")
                    cls = _class_name(raw)
                    if cls is not None:
                        indent = len(raw) - len(raw.lstrip())
                        scopes = [(indent, "class " + cls)]
                        complete_scope = indent == 0
                continue
            text = raw
            indent = len(text) - len(text.lstrip())
            if not in_sig and text.strip() and not text.lstrip().startswith("#"):
                while scopes and scopes[-1][0] >= indent:
                    scopes.pop()
                if indent == 0:
                    complete_scope = True
            cls = _class_name(text) if not in_sig else None
            name = _sig_name(text) if not in_sig else None
            if cls is not None:
                scopes.append((indent, "class " + cls))
            if name is not None:
                in_sig = True
                depth = 0
                in_triple = None
                # Missing ancestors stay unknown. A shared replacement can
                # align them locally, but cannot identify a moved method.
                key = (tuple(scopes), indent, name,
                       None if complete_scope else location)
                names = set()
                records.append((key, names))
                scopes.append((indent, "def " + name))
            if not in_sig:
                continue
            bare_parts: list[str] = []
            i = 0
            n = len(text)
            while i < n:
                if in_triple is not None:
                    idx = text.find(in_triple, i)
                    if idx == -1:
                        i = n
                    else:
                        bs = 0
                        k = idx - 1
                        while k >= 0 and text[k] == "\\":
                            bs += 1
                            k -= 1
                        if bs % 2 == 0:
                            i = idx + len(in_triple)
                            in_triple = None
                        else:
                            i = idx + 1
                else:
                    if text[i:i + 3] in ('"""', "'''"):
                        q = text[i:i + 3]
                        idx = text.find(q, i + 3)
                        while idx != -1:
                            bs = 0
                            k = idx - 1
                            while k >= 0 and text[k] == "\\":
                                bs += 1
                                k -= 1
                            if bs % 2 == 0:
                                break
                            idx = text.find(q, idx + 1)
                        if idx != -1:
                            i = idx + 3
                        else:
                            in_triple = q
                            i = n
                    elif text[i] in ('"', "'"):
                        q = text[i]
                        i += 1
                        while i < n:
                            if text[i] == "\\":
                                i += 2
                            elif text[i] == q:
                                i += 1
                                break
                            else:
                                i += 1
                    else:
                        bare_parts.append(text[i])
                        i += 1
            bare = "".join(bare_parts)
            names.update(m for m in _PARAM.findall(bare) if m not in _COMMON)
            # count parens outside string literals: a default like
            # sep=')' must not close a multi-line signature early
            depth += bare.count("(") - bare.count(")")
            # The signature ends on the line that brings the paren depth
            # back to zero (or below, for a one-line def).
            if depth <= 0 and in_triple is None and (")" in bare or "(" in bare):
                in_sig = False
        return records

    minus: dict[str, list[tuple[int, str]]] = {}
    plus: dict[str, list[tuple[int, str]]] = {}
    current: Optional[str] = None
    change_start: Optional[int] = None
    lines = (_diff_body_lines(diff_text, include_hunks=True)
             if parsed_lines is None else parsed_lines)
    for location, (line, current) in enumerate(lines):
        if line.startswith("@@"):
            change_start = None
            minus.setdefault(current, []).append((location, line))
            plus.setdefault(current, []).append((location, line))
            continue
        if line.startswith(("-", "+")):
            # Both images of a replacement share an alignment hint.
            # Enclosing scopes separate definitions within that block.
            if change_start is None:
                change_start = location
            side = minus if line.startswith("-") else plus
            side.setdefault(current, []).append((change_start, line[1:]))
        else:
            change_start = None
            # Keep signature state across hunks: a later hunk may contain
            # the tail of this same signature without repeating its def.
            text = line[1:] if line.startswith(" ") else line
            minus.setdefault(current, []).append((location, text))
            plus.setdefault(current, []).append((location, text))
    out: dict[str, set[str]] = {}
    for file, lines in minus.items():
        pre = _params_by_func(lines)
        post = _params_by_func(plus.get(file, []))
        gone: set[str] = set()
        for key, names in pre:
            old = [params for identity, params in pre if identity == key]
            new = [params for identity, params in post if identity == key]
            if len(old) == len(new) == 1:
                gone |= names - new[0]
        if gone:
            out[file] = gone
    return out


def gather(
    sources: list[ContextSource],
    changed_files: list[str],
    diff_text: str,
    head_sha: Optional[str],
    allow_unsnapshotted: bool = False,
    on_error: Optional[Callable[[str, str], None]] = None,
) -> GatherResult:
    """Collect facts from every source, applying the snapshot gate.

    Each source is wrapped in its own try/except so one failing source
    never hides another's facts, and the failure is recorded rather
    than becoming an empty table.

    Snapshot gate: a source whose snapshot_sha() is a commit other than
    head_sha is skipped (recorded in result.skipped) unless
    allow_unsnapshotted is True. A source with no snapshot (None)
    computes on demand and always runs. If head_sha itself is unknown
    the gate cannot be applied and snapshotted sources are skipped too,
    for the same reason: stale facts read as authoritative.
    """
    result = GatherResult()
    for src in sources:
        name = getattr(src, "name", type(src).__name__)
        try:
            snap = src.snapshot_sha()
            result.snapshot_shas[name] = snap
            if snap is not None and not allow_unsnapshotted:
                if head_sha is None or snap != head_sha:
                    result.skipped.append(
                        "%s: index at %s, review head %s"
                        % (name, (snap or "?")[:12], (head_sha or "unknown")[:12])
                    )
                    continue
            rows = src.facts(changed_files, diff_text)
            result.rows.extend(rows)
        except Exception as exc:  # noqa: BLE001 - attribute, do not swallow
            msg = "%s: %s: %s" % (name, type(exc).__name__, exc)
            result.errors.append(msg)
            if on_error is not None:
                on_error(name, msg)
    return result


def render_blast_radius(rows: list[FactRow]) -> str:
    """Render rows as the exact table cli.py:3756-3759 built.

    Empty input renders "" (not a header-only table), matching the
    pre-change behaviour where no findings meant no context section.
    """
    if not rows:
        return ""
    body = "\n".join(
        "| %s | %s | %s | %s |" % (r.entity, r.file, r.downstream, r.dependents)
        for r in rows
    )
    return (
        "| Entity | File | Downstream | Top Dependents |\n"
        "|--------|------|------------|----------------|\n"
        + body
    )


def render_context_sources(result: GatherResult) -> str:
    """Render the non-blast-radius facts as a ## Context Sources body.

    Today every FactRow comes from graph_triage and goes into the
    blast-radius table, so this returns "" and the L1 prompt is
    unchanged. B2 wires it into build_l1_provider; MCP sources (B3)
    are the first producers of text here.
    """
    # Reader rows (RemovedSymbolReaders) are falsifier evidence, handed
    # to it directly via build_falsifier(context_rows=); they are not
    # L1 context and must not change the L1 prompt (oracle 24ed2e0).
    extra = [r for r in result.rows
             if r.source not in ("graph_triage", "removed-symbol-readers")]
    if not extra:
        return ""
    lines = [
        "| Source | Entity | File | Line | Note |",
        "|--------|--------|------|------|------|",
    ]
    for r in extra:
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (r.source, r.entity, r.file,
               r.origin_line if r.origin_line is not None else "",
               r.dependents)
        )
    return "\n".join(lines)

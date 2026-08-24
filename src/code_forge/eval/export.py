# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Corpus export from adjudicated ledger rows.

Reads a ledger (produced by the CI write path) and materializes a
self-contained corpus directory with manifest.yaml + diffs/ for eval
replay.  Only terminal-state rows (FIXED / DISPROVED / ESCAPED) are
emitted; UNADJUDICATED and DUPLICATE rows are skipped with distinct
counters.

Skipped-row precedence (D-15, CP1b): unadjudicated > stale-sha >
duplicate-excluded > empty-diff > dedup-collapse.  A row is attributed
to exactly ONE reason and the counters sum to the total rows read.

D-17 toolchain self-containment: any ``.code-forge/gate.yaml`` section
in a materialized foreign diff is STRIPPED from the emitted patch file
so replay never executes a hostile foreign test.command.  A reviewed
diff that LEGITIMATELY changes the gate config is exported with the
gate section removed -- toolchain self-containment wins over reviewing
gate-config diffs (documented D-17 tradeoff).

Re-export hygiene (D-22): the exporter owns manifest.yaml and the
diffs/<name>.diff files named in it.  A re-export deletes the previous
manifest-managed diff files and writes the new set; foreign files the
user dropped into the output dir are left untouched.  A non-empty
output dir that does not already contain an exporter manifest requires
force=True.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from code_forge.ledger import LedgerRow, TerminalState, iter_rows
from code_forge.eval.corpus import CorpusEntry, ExpectedFinding, load_corpus


class ExportError(Exception):
    """Raised when the output dir fails the D-22 non-empty force gate."""


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportSummary:
    """Mutually exclusive counters for the export run (D-15, CP1b).

    Every row is attributed to exactly one counter by documented
    precedence: unadjudicated > stale-sha > duplicate-excluded >
    empty-diff > dedup-collapse.  Sum of all counters == total_rows_read.
    """

    emitted: int = 0
    unadjudicated_skipped: int = 0
    stale_sha_skipped: int = 0
    duplicate_excluded: int = 0
    empty_diff_skipped: int = 0
    dedup_collapsed: int = 0

    @property
    def total_rows_read(self) -> int:
        return (
            self.emitted
            + self.unadjudicated_skipped
            + self.stale_sha_skipped
            + self.duplicate_excluded
            + self.empty_diff_skipped
            + self.dedup_collapsed
        )


# ---------------------------------------------------------------------------
# Axis mapping (D-14)
# ---------------------------------------------------------------------------

_AXIS_CLAIM_TABLE: dict[str, list[str]] = {
    "security vulnerability": ["SEC"],
    "sql injection": ["SEC"],
    "command injection": ["SEC"],
    "path traversal": ["SEC"],
    "injection": ["SEC"],
    "authentication bypass": ["SEC"],
    "authorization bypass": ["SEC"],
    "access control": ["SEC"],
    "race condition": ["RELIABILITY"],
    "deadlock": ["RELIABILITY"],
    "concurrency": ["RELIABILITY"],
    "null pointer": ["RELIABILITY"],
    "uninitialized": ["RELIABILITY"],
    "logic error": ["CORRECTNESS"],
    "off-by-one": ["CORRECTNESS"],
    "boundary": ["CORRECTNESS"],
    "type confusion": ["CORRECTNESS"],
    "integer overflow": ["CORRECTNESS"],
    "data race": ["RELIABILITY"],
    "use-after-free": ["RELIABILITY"],
    "memory leak": ["RELIABILITY"],
    "memory safety": ["RELIABILITY"],
    "buffer overflow": ["SEC"],
    "api misuse": ["CORRECTNESS"],
    "regex": ["CORRECTNESS"],
    "error handling": ["RELIABILITY"],
    "exception handling": ["RELIABILITY"],
    "logging": ["OBSERVABILITY"],
    "observability": ["OBSERVABILITY"],
    "performance": ["PERFORMANCE"],
    "denial of service": ["PERFORMANCE"],
    "credential": ["SEC"],
    "secret": ["SEC"],
    "hardcoded": ["CORRECTNESS"],
    "deprecated": ["MAINTENANCE"],
    "legacy": ["MAINTENANCE"],
    "style": ["STYLE"],
    "readability": ["STYLE"],
    "naming": ["STYLE"],
    "formatting": ["STYLE"],
    "testing": ["TEST"],
    "test": ["TEST"],
    "coverage": ["TEST"],
    "documentation": ["DOC"],
    "docs": ["DOC"],
    "typo": ["DOC"],
    "comment": ["DOC"],
    "migration": ["MAINTENANCE"],
    "backward compatibility": ["MAINTENANCE"],
    "compatibility": ["MAINTENANCE"],
    "configuration": ["CONFIG"],
    "config": ["CONFIG"],
    "build": ["CONFIG"],
    "ci": ["CONFIG"],
    "deployment": ["CONFIG"],
    "infrastructure": ["CONFIG"],
    "trust": ["TRUST"],
    "supply chain": ["TRUST"],
    "dependency": ["TRUST"],
    "third-party": ["TRUST"],
    "vendor": ["TRUST"],
    "review": ["REVIEW"],
    "process": ["REVIEW"],
    "workflow": ["REVIEW"],
    "clean": ["CLEAN"],
    "no findings": ["CLEAN"],
    "lint": ["STYLE"],
    "clippy": ["STYLE"],
    "ruff": ["STYLE"],
    "eslint": ["STYLE"],
}


def _map_axis_claim(claim: Optional[str]) -> list[str]:
    """Map a free-text axis_claim to a list of axis_tags (D-14).

    Exact-match lookup on the lowercased claim.  Falls back to
    skip-with-warning: prints a warning to stderr and returns
    ``["UNKNOWN"]``.  None/empty claims take the same fallback rather
    than crashing the export.
    """
    key = (claim or "").strip().lower()
    result = _AXIS_CLAIM_TABLE.get(key)
    if result is not None:
        return list(result)
    print(
        "export: warning: unmapped axis_claim %r -> UNKNOWN" % claim,
        file=sys.stderr,
    )
    return ["UNKNOWN"]


# ---------------------------------------------------------------------------
# SHA validation
# ---------------------------------------------------------------------------

_VALID_SHA = re.compile(r"[0-9a-fA-F]{7,64}")


def _sha_format_ok(sha: object) -> bool:
    """Reject ledger-carried values that are not plain hex SHAs.

    The ledger is local, but export consumes rows that may travel with a
    foreign repo; a value like ``--upload-pack=...`` must never reach a
    git subprocess as an argument.  ``fullmatch`` (not ``$``) is used so
    a trailing newline cannot slip past the anchor.
    """
    return isinstance(sha, str) and _VALID_SHA.fullmatch(sha) is not None


def _sha_is_resolvable(repo_root: Path, sha: str) -> bool:
    """Check whether ``sha`` resolves via ``git cat-file -e``."""
    try:
        res = subprocess.run(
            ["git", "cat-file", "-e", sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # repo_root moved, was deleted, never existed, or git hung on a
        # corrupted object store: unresolvable.
        return False
    return res.returncode == 0


# ---------------------------------------------------------------------------
# Diff materialization
# ---------------------------------------------------------------------------


def _materialize_diff(
    repo_root: Path, base_sha: str, head_sha: str
) -> Optional[str]:
    """Run ``git diff base..head`` in ``repo_root`` (may be empty).

    None means the diff could not be produced (non-zero exit or git
    hang); the caller counts that as stale-sha, never as empty-diff.
    """
    try:
        res = subprocess.run(
            ["git", "diff", base_sha + ".." + head_sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        # SHAs resolved moments ago but diff failed (repo moved, shallow
        # graft, corruption): the caller counts this as stale, never as
        # an empty diff.
        return None
    return res.stdout


# ---------------------------------------------------------------------------
# D-17 gate.yaml stripping
# ---------------------------------------------------------------------------

_GATE_DIFF_PATTERN = re.compile(
    r"^diff --git (?:a/\S+ )?b/\.code-forge/gate\.yaml\r?\n"
    r".*?(?=^diff --git |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _strip_gate_yaml(diff_text: str) -> str:
    """Remove any diff section producing ``.code-forge/gate.yaml``.

    D-17: the extractor strips foreign gate.yaml additions/modifications
    from materialized diffs so replay never executes a hostile foreign
    test.command.  Any section whose b-side path is gate.yaml is removed
    regardless of the a-side, so a rename from an innocent source path
    cannot smuggle the file past the strip; a rename FROM gate.yaml TO
    another name is left intact since the result no longer controls the
    toolchain.  CRLF line endings (core.autocrlf / eol=crlf) are
    tolerated.
    """
    return _GATE_DIFF_PATTERN.sub("", diff_text)


# ---------------------------------------------------------------------------
# D-22 output dir hygiene
# ---------------------------------------------------------------------------


def _managed_diff_files(out_dir: Path) -> list[str]:
    """Return the diff_file list from a previously exported manifest.

    Reads ``manifest.yaml.prev`` first: the re-export flow renames the
    old manifest aside before writing the new one, so between rename and
    replace the previous manifest lives at ``manifest.yaml.prev``.
    Missing or unparseable manifest -> empty list (nothing managed).
    Entries that are absolute or contain ``..`` are rejected here rather
    than at the join site, so a tampered manifest can never steer the
    re-export cleanup outside the output dir.
    """
    manifest_path = out_dir / "manifest.yaml.prev"
    if not manifest_path.is_file():
        manifest_path = out_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return []
    try:
        entries = load_corpus(manifest_path)
    except (ValueError, KeyError, TypeError, OSError):
        # load_corpus wraps yaml.YAMLError into ValueError upstream;
        # OSError covers the file vanishing or becoming unreadable
        # between the is_file check and the open.
        return []
    managed: list[str] = []
    for e in entries:
        rel = Path(str(e.diff_file))
        if rel.is_absolute() or ".." in rel.parts:
            print(
                "export: ignoring unsafe managed path %r" % e.diff_file,
                file=sys.stderr,
            )
            continue
        managed.append(str(rel))
    return managed


def _check_out_dir(out_dir: Path, force: bool) -> None:
    """Enforce the D-22 force gate on a non-foreign-managed output dir.

    A dir that already contains an exporter manifest is ours: re-export
    proceeds without force.  Any other non-empty dir requires force.
    """
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise ExportError(
            "output path exists and is not a directory: %s" % out_dir
        )
    if not any(out_dir.iterdir()):
        return
    if (out_dir / "manifest.yaml").exists() or (
        out_dir / "manifest.yaml.prev"
    ).exists():
        # A previous export landed here; .prev without .yaml means the
        # last run crashed mid-swap, which is still our managed dir.
        return
    if not force:
        raise ExportError(
            "output directory %s is not empty and was not produced by "
            "export-eval; re-run with --force to write into it" % out_dir
        )


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_eval(
    ledger_root: Path,
    out_dir: Path,
    repo_root_override: Optional[Path] = None,
    force: bool = False,
) -> ExportSummary:
    """Read adjudicated ledger rows and emit a corpus directory.

    Args:
        ledger_root: repo root containing ``.code-forge/ledger.jsonl``.
        out_dir: output directory for manifest.yaml, diffs/, etc.
        repo_root_override: optional override for row.repo_root when
            resolving SHAs (D-09).
        force: allow writing into a non-empty dir not previously
            produced by export-eval (D-22).

    Returns:
        ExportSummary with mutually exclusive counters.

    Raises:
        ExportError: output dir fails the D-22 force gate.
    """
    _check_out_dir(out_dir, force)

    summary = ExportSummary()
    rows = list(iter_rows(ledger_root))

    # Dedup by fingerprint -- latest row wins (D-08 read-side dedup).
    # Collapsed rows are counted under dedup-collapse, the lowest
    # precedence slot: a collapsed row is never evaluated for the
    # other reasons because the latest row supersedes it.
    latest_by_fp: dict[str, LedgerRow] = {}
    for r in rows:
        latest_by_fp[r.fingerprint] = r
    deduped = list(latest_by_fp.values())
    summary = dataclasses.replace(
        summary, dedup_collapsed=len(rows) - len(deduped)
    )

    entries: list[CorpusEntry] = []
    stale_sha_list: list[str] = []
    new_diff_texts: dict[str, str] = {}

    for row in deduped:
        # Priority 1: UNADJUDICATED (D-15: wins over every other reason)
        if row.terminal_state == TerminalState.UNADJUDICATED:
            summary = dataclasses.replace(
                summary, unadjudicated_skipped=summary.unadjudicated_skipped + 1
            )
            continue

        repo_root = (
            repo_root_override
            if repo_root_override is not None
            else Path(row.repo_root)
        )

        # Priority 2: stale SHAs (D-03).  Format is checked before any
        # git subprocess sees the value.
        if not (
            _sha_format_ok(row.base_sha)
            and _sha_format_ok(row.head_sha)
            and _sha_is_resolvable(repo_root, row.base_sha)
            and _sha_is_resolvable(repo_root, row.head_sha)
        ):
            summary = dataclasses.replace(
                summary, stale_sha_skipped=summary.stale_sha_skipped + 1
            )
            stale_sha_list.append(
                "%s (base=%s head=%s)"
                % (row.fingerprint, row.base_sha, row.head_sha)
            )
            continue

        # Priority 3: DUPLICATE -- excluded from export (deepseek H-1:
        # the bug WAS real, reported twice; emitting it as
        # expect-no-catch would penalize finding a real bug).
        if row.terminal_state == TerminalState.DUPLICATE:
            summary = dataclasses.replace(
                summary, duplicate_excluded=summary.duplicate_excluded + 1
            )
            continue

        # Priority 4: empty diff (gemini B-2: base == head or a 0-byte
        # materialized diff replays as permanent PASS -> false green).
        # base == head guarantees a 0-byte diff and is short-circuited
        # before the git subprocess; the stripped-text check below
        # catches a diff whose only change is a foreign gate.yaml
        # (stripped to nothing by D-17), which must not emit a vacuous
        # HOLD entry.
        if row.base_sha == row.head_sha:
            summary = dataclasses.replace(
                summary, empty_diff_skipped=summary.empty_diff_skipped + 1
            )
            continue

        diff_text = _materialize_diff(repo_root, row.base_sha, row.head_sha)
        if diff_text is None:
            summary = dataclasses.replace(
                summary, stale_sha_skipped=summary.stale_sha_skipped + 1
            )
            stale_sha_list.append(
                "%s (git diff failed for %s..%s)"
                % (row.fingerprint, row.base_sha, row.head_sha)
            )
            continue
        # D-17: strip any foreign gate.yaml before the empty check and
        # before the diff hits disk.
        diff_text = _strip_gate_yaml(diff_text)
        if not diff_text.strip():
            summary = dataclasses.replace(
                summary, empty_diff_skipped=summary.empty_diff_skipped + 1
            )
            continue

        name = _entry_name(row)
        expected_verdict = (
            "HOLD"
            if row.terminal_state in (TerminalState.FIXED, TerminalState.ESCAPED)
            else "PASS"
        )

        expected_findings: list[ExpectedFinding] = []
        if row.file and row.line:
            expected_findings.append(
                ExpectedFinding(
                    file=row.file,
                    description=row.axis_claim or "ledger-derived finding",
                    line_range=(row.line, row.line),
                )
            )

        entries.append(
            CorpusEntry(
                name=name,
                diff_file="diffs/%s.diff" % name,
                expected_verdict=expected_verdict,
                axis_tags=_map_axis_claim(row.axis_claim),
                expected_findings=expected_findings,
            )
        )
        new_diff_texts["diffs/%s.diff" % name] = diff_text

    # Re-export hygiene (D-22): write the new managed set first, then
    # remove previously manifest-managed diff files no longer emitted.
    # A crash between the two steps leaves an orphaned diff file (a new
    # re-export cleans it up via the freshly written manifest), never a
    # manifest pointing at missing diffs.  Foreign files are never
    # touched.
    out_dir.mkdir(parents=True, exist_ok=True)
    diff_dir = out_dir / "diffs"
    if new_diff_texts:
        diff_dir.mkdir(parents=True, exist_ok=True)
    for rel, text in new_diff_texts.items():
        (out_dir / rel).write_text(text, encoding="utf-8")

    manifest = {
        "provenance": _provenance(ledger_root),
        "entries": [_entry_to_dict(e) for e in entries],
    }
    # Write the manifest last and atomically so a failed export never
    # leaves managed diff files orphaned without their manifest.  The
    # previous manifest is kept as manifest.yaml.prev so stale-diff
    # cleanup can still find it after the new manifest has landed.
    prev_manifest = out_dir / "manifest.yaml"
    prev_aside = out_dir / "manifest.yaml.prev"
    if prev_manifest.is_file():
        prev_manifest.replace(prev_aside)
    manifest_tmp = out_dir / "manifest.yaml.tmp"
    manifest_tmp.write_text(
        yaml.dump(manifest, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    manifest_tmp.replace(prev_manifest)

    for old_rel in _managed_diff_files(out_dir):
        if old_rel in new_diff_texts:
            continue
        old_path = out_dir / old_rel
        try:
            if old_path.is_file() and old_path.resolve().is_relative_to(
                out_dir.resolve()
            ):
                old_path.unlink()
        except OSError:
            # Externally removed or made unreadable between the check
            # and the unlink: nothing left to clean up.
            continue
    if prev_aside.is_file():
        prev_aside.unlink()

    for item in stale_sha_list:
        print("export: stale SHA skipped: %s" % item, file=sys.stderr)

    summary = dataclasses.replace(summary, emitted=len(entries))
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_name(row: LedgerRow) -> str:
    """Derive a stable entry name from a ledger row.

    Ledger fingerprints are hex digests in practice, but rows may travel
    with a foreign repo; anything outside a safe filename alphabet is
    replaced so a crafted fingerprint (e.g. containing '/') can never
    steer the diff path outside diffs/ or crash the write.  The name is
    clamped so the on-disk path stays well under the 255-byte filename
    limit on ext4/APFS.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", row.fingerprint).lower()
    if safe != row.fingerprint or len(safe) > 100:
        print(
            "export: sanitized unsafe fingerprint %r for naming"
            % row.fingerprint,
            file=sys.stderr,
        )
        # Distinct raw fingerprints can sanitize to the same name
        # ('a/b' vs 'a-b'); pin a short hash of the raw value so the
        # entry name stays unique and no diff silently overwrites.
        safe = "%s-%s" % (
            safe[:100], hashlib.sha256(row.fingerprint.encode()).hexdigest()[:8],
        )
    return "lgr-%s" % safe


def _provenance(ledger_root: Path) -> str:
    """Return the repo basename only (D-09 PII guard)."""
    return ledger_root.resolve().name


def _entry_to_dict(entry: CorpusEntry) -> dict:
    """Serialize a CorpusEntry as a load_corpus-compatible manifest dict.

    eval-bank-compat fields may ride alongside as extra keys --
    load_corpus ignores unknown keys (corpus.py reads only known ones).
    """
    d: dict = {
        "name": entry.name,
        "diff_file": entry.diff_file,
        "expected_verdict": entry.expected_verdict,
        "axis_tags": entry.axis_tags,
        "expected_findings": [
            {
                "file": ef.file,
                "description": ef.description,
            }
            | (
                {"line_range": [ef.line_range[0], ef.line_range[1]]}
                if ef.line_range is not None
                else {}
            )
            for ef in entry.expected_findings
        ],
    }
    if entry.expected_advisory:
        d["expected_advisory"] = entry.expected_advisory
    return d

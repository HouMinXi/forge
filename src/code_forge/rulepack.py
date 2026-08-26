# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Domain rulepack engine (Stage 1).

Provides curated, allowlisted semgrep rule packs with explicit per-rule
matrix accounting: VIOLATION / CLEAN / NOT_APPLICABLE / NOT_RUN.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .advisory import AdvisoryFinding
from .parsers.base import Finding, ToolError
from .parsers.semgrep import parse_semgrep


# Map semgrep language names to file extensions we filter by.
# Semgrep itself filters per-rule; this is accounting-level coverage only.
_LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx"),
    "typescript": (".ts", ".tsx"),
    "tsx": (".tsx",),
    "jsx": (".jsx",),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "bash": (".sh", ".bash"),
    "go": (".go",),
    "rust": (".rs",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".hpp", ".cxx"),
    "generic": ("",),  # blanket language sentinel
}


@dataclass(frozen=True)
class RuleMeta:
    """Metadata for a single rule, as declared in meta.yaml."""

    id: str
    title: str
    category: str
    impact_tier: str
    languages: list[str]
    source: str
    adjudication: bool = False


@dataclass(frozen=True)
class RulepackManifest:
    """Loaded pack descriptor.

    frozen=True enforces immutability; list/set fields make instances
    unhashable by design, which is fine -- we key by pack name, not object.
    """

    name: str
    rules: list[RuleMeta]
    rules_yaml_path: Path
    meta_yaml_path: Path
    missing_rule_ids: set[str] = field(default_factory=set)
    load_error: Optional[str] = None


@dataclass
class RulepackMatrix:
    """Per-rule accounting artifact.

    schema_version is fixed at 1 for Stage 1.
    packs is a list of dicts shaped by to_dict().
    """

    schema_version: int = 1
    packs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return schema-versioned JSON dict."""
        return {
            "schema_version": self.schema_version,
            "packs": list(self.packs),
        }

    def write(self, repo_root: Path) -> None:
        """Atomically persist matrix to .code-forge/rulepack-matrix.json."""
        out = repo_root / ".code-forge" / "rulepack-matrix.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, out)


def _stable_hash(text: str) -> str:
    """Deterministic hash for embedding in fingerprints."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _language_extensions(languages: list[str]) -> tuple[str, ...]:
    """Return file-extension tuple accounting for a list of languages."""
    exts: set[str] = set()
    for lang in languages:
        for part in lang.lower().replace(",", " ").split():
            exts.update(_LANGUAGE_EXTENSIONS.get(part, ()))
    return tuple(sorted(exts))


def _files_for_languages(
    files: list[Path], languages: list[str]
) -> list[Path]:
    """Filter source files by pack languages.

    An empty language list or unknown language maps to "generic" and
    keeps all files, avoiding accidental NOT_APPLICABLE for broad packs.
    """
    exts = _language_extensions(languages)
    if not exts or ("",) in exts:
        return list(files)
    return [f for f in files if str(f).lower().endswith(exts)]


def _extract_rule_ids(rules_yaml_path: Path) -> tuple[set[str], Optional[str]]:
    """Return (rule_ids, error_message) from a semgrep rules.yaml file."""
    try:
        data = yaml.safe_load(rules_yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return set(), "invalid YAML in rules.yaml: %s" % exc
    except OSError as exc:
        return set(), "cannot read rules.yaml: %s" % exc

    if not isinstance(data, dict):
        return set(), "rules.yaml must be a mapping with a 'rules' key"

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return set(), "rules.yaml 'rules' must be a list"

    ids: set[str] = set()
    duplicates: set[str] = set()
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            return set(), "rules.yaml rule at index %d is not a mapping" % idx
        rid = rule.get("id")
        if not isinstance(rid, str) or not rid:
            return set(), "rules.yaml rule at index %d missing string 'id'" % idx
        if rid in ids:
            duplicates.add(rid)
        ids.add(rid)

    if duplicates:
        return set(), "duplicate rule IDs in rules.yaml: %s" % sorted(duplicates)
    return ids, None


def _validate_rule_meta(rule: dict, index: int) -> Optional[str]:
    """Return error string if a meta.yaml rule entry is malformed."""
    required = ("id", "title", "category", "impact_tier", "languages", "source")
    for key in required:
        if key not in rule:
            return "meta.yaml rule[%d] missing required field %r" % (index, key)
    if not isinstance(rule.get("id"), str) or not rule.get("id"):
        return "meta.yaml rule[%d] 'id' must be a non-empty string" % index
    if not isinstance(rule.get("languages"), list):
        return "meta.yaml rule[%d] 'languages' must be a list" % index
    return None


def load_pack(pack_dir: Path) -> RulepackManifest:
    """Load a rulepack directory.

    Parses meta.yaml and rules.yaml, performs AC-2 missing-rule detection,
    and returns a fail-soft RulepackManifest on malformed data.
    """
    pack_dir = Path(pack_dir)
    meta_path = pack_dir / "meta.yaml"
    rules_path = pack_dir / "rules.yaml"
    errors: list[str] = []

    # Determine pack name early so callers have something to report.
    default_name = pack_dir.name

    # Read meta.yaml
    meta_data: dict = {}
    try:
        raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            meta_data = raw
        else:
            errors.append("meta.yaml is not a mapping")
    except FileNotFoundError:
        errors.append("missing meta.yaml")
    except yaml.YAMLError as exc:
        errors.append("invalid YAML in meta.yaml: %s" % exc)
    except OSError as exc:
        errors.append("cannot read meta.yaml: %s" % exc)

    name = meta_data.get("name", default_name)
    if not isinstance(name, str):
        name = default_name

    meta_rules: list[RuleMeta] = []
    meta_ids: set[str] = set()
    duplicate_meta_ids: set[str] = set()
    raw_rules = meta_data.get("rules", [])
    if not isinstance(raw_rules, list):
        raw_rules = []
        errors.append("meta.yaml 'rules' must be a list")

    for idx, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            errors.append("meta.yaml rule at index %d is not a mapping" % idx)
            continue
        err = _validate_rule_meta(raw, idx)
        if err:
            errors.append(err)
            continue
        rid = raw["id"]
        if rid in meta_ids:
            duplicate_meta_ids.add(rid)
        meta_ids.add(rid)
        meta_rules.append(
            RuleMeta(
                id=rid,
                title=raw["title"],
                category=raw["category"],
                impact_tier=raw["impact_tier"],
                languages=list(raw["languages"]),
                source=raw["source"],
                adjudication=bool(raw.get("adjudication", False)),
            )
        )

    if duplicate_meta_ids:
        errors.append(
            "duplicate rule IDs in meta.yaml: %s" % sorted(duplicate_meta_ids)
        )

    # Read rules.yaml IDs for missing-rule detection.
    rules_ids, rules_error = _extract_rule_ids(rules_path)
    if rules_error:
        errors.append(rules_error)

    missing_ids = meta_ids - rules_ids if rules_ids else set(meta_ids)

    load_error = "; ".join(errors) if errors else None
    return RulepackManifest(
        name=name,
        rules=meta_rules,
        rules_yaml_path=rules_path,
        meta_yaml_path=meta_path,
        missing_rule_ids=missing_ids,
        load_error=load_error,
    )


def discover_packs(repo_root: Path) -> dict[str, Path]:
    """Return discovered packs: built-in overrides repo-local.

    Built-in path: src/code_forge/rules/packs/<name>/
    Repo-local path: .code-forge/packs/<name>/
    """
    builtin_root = Path(__file__).parent / "rules" / "packs"
    local_root = Path(repo_root) / ".code-forge" / "packs"
    packs: dict[str, Path] = {}

    if local_root.is_dir():
        for subdir in sorted(local_root.iterdir()):
            if subdir.is_dir():
                packs[subdir.name] = subdir

    if builtin_root.is_dir():
        for subdir in sorted(builtin_root.iterdir()):
            if subdir.is_dir():
                # Built-in wins on collision.
                packs[subdir.name] = subdir

    return packs


def resolve_active_packs(
    gate_config: dict, repo_root: Path
) -> list[RulepackManifest]:
    """Resolve allowlisted packs from gate.yaml.

    Raises:
        ValueError: if gate.yaml names an unknown pack.
    """
    names = gate_config.get("rulepacks", []) or []
    if not names:
        return []

    discovered = discover_packs(repo_root)
    active: list[RulepackManifest] = []
    for name in names:
        if name not in discovered:
            raise ValueError(
                "Unknown rulepack %r; searched built-in %s and repo-local %s"
                % (
                    name,
                    Path(__file__).parent / "rules" / "packs",
                    Path(repo_root) / ".code-forge" / "packs",
                )
            )
        active.append(load_pack(discovered[name]))
    return active


class RulepackRunner:
    """Advisory axis: executes configured semgrep rule packs.

    Satisfies AdvisoryAxisRunner (is_advisory=True). machine.py sets
    source_files before dispatch; the runner does NOT invoke git.
    """

    def __init__(self, timeout: int = 120) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []
        self.packs: list[RulepackManifest] = []
        self.matrix: Optional[RulepackMatrix] = None
        self._default_timeout = timeout

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: non-blocking by default."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run active rule packs and return advisory findings.

        Also writes the matrix artifact to .code-forge/rulepack-matrix.json
        when active packs are configured.
        """
        # Avoid cross-run accumulation when runner is reused.
        self.infra_errors.clear()
        del diff_text  # unused; protocol requires the parameter

        from .gate_check import load_gate_config

        repo_root = Path(repo_root)
        try:
            gate_config = load_gate_config(repo_root / ".code-forge" / "gate.yaml")
        except Exception as exc:  # noqa: BLE001
            msg = "rulepack: failed to load gate.yaml: %s" % exc
            self.infra_errors.append(msg)
            self.matrix = RulepackMatrix(packs=[])
            self.matrix.write(repo_root)
            return []

        try:
            self.packs = resolve_active_packs(gate_config, repo_root)
        except ValueError as exc:
            self.infra_errors.append(str(exc))
            self.matrix = RulepackMatrix(packs=[])
            self.matrix.write(repo_root)
            return []

        if not self.packs:
            self.matrix = RulepackMatrix(packs=[])
            self.matrix.write(repo_root)
            return []

        # Guard: semgrep must be installed.
        if shutil.which("semgrep") is None:
            msg = "Rulepack axis requires semgrep. Install: pip install semgrep"
            self.infra_errors.append(msg)
            print(msg, file=sys.stderr)
            self.matrix = self._build_matrix(
                {
                    p.name: {r.id: "NOT_RUN" for r in p.rules}
                    for p in self.packs
                },
                {p.name: {} for p in self.packs},
            )
            self.matrix.write(repo_root)
            return []

        # No source files -> every rule is NOT_APPLICABLE (or NOT_RUN if missing).
        if self.source_files is None or not self.source_files:
            statuses: dict[str, dict[str, str]] = {}
            reasons: dict[str, dict[str, Optional[str]]] = {}
            for pack in self.packs:
                pack_statuses: dict[str, str] = {}
                pack_reasons: dict[str, Optional[str]] = {}
                for rule in pack.rules:
                    if rule.id in pack.missing_rule_ids:
                        pack_statuses[rule.id] = "NOT_RUN"
                        pack_reasons[rule.id] = "rule missing from rules.yaml"
                    else:
                        pack_statuses[rule.id] = "NOT_APPLICABLE"
                statuses[pack.name] = pack_statuses
                reasons[pack.name] = pack_reasons
            self.matrix = self._build_matrix(statuses, reasons)
            self.matrix.write(repo_root)
            return []

        timeout = gate_config.get("rulepack_timeout_seconds", self._default_timeout)
        if not isinstance(timeout, int) or timeout <= 0:
            timeout = self._default_timeout
        jobs = min(2, os.cpu_count() or 1)

        all_statuses: dict[str, dict[str, str]] = {}
        all_reasons: dict[str, dict[str, Optional[str]]] = {}
        advisories: list[AdvisoryFinding] = []

        for pack in self.packs:
            pack_statuses, pack_reasons, pack_advisories = self._run_pack(
                pack=pack,
                repo_root=repo_root,
                timeout=timeout,
                jobs=jobs,
            )
            all_statuses[pack.name] = pack_statuses
            all_reasons[pack.name] = pack_reasons
            advisories.extend(pack_advisories)

        self.matrix = self._build_matrix(all_statuses, all_reasons)
        self.matrix.write(repo_root)
        self._print_summary()
        return advisories

    def _run_pack(
        self,
        pack: RulepackManifest,
        repo_root: Path,
        timeout: int,
        jobs: int,
    ) -> tuple[dict[str, str], dict[str, Optional[str]], list[AdvisoryFinding]]:
        """Execute one pack, returning statuses, reasons, and advisory findings."""
        statuses: dict[str, str] = {}
        reasons: dict[str, Optional[str]] = {}

        # Missing rules are NOT_RUN regardless of execution.
        for rule in pack.rules:
            if rule.id in pack.missing_rule_ids:
                statuses[rule.id] = "NOT_RUN"
                reasons[rule.id] = "rule missing from rules.yaml"
            elif pack.load_error:
                statuses[rule.id] = "NOT_RUN"
                reasons[rule.id] = "pack load error: %s" % pack.load_error
            else:
                statuses[rule.id] = "NOT_APPLICABLE"

        if pack.load_error:
            return statuses, reasons, []

        # Determine files eligible for this pack's languages.
        pack_languages = [
            lang for rule in pack.rules for lang in rule.languages
        ]
        files = _files_for_languages(self.source_files or [], pack_languages)
        files = [f for f in files if (repo_root / f).exists()]

        if not files:
            # NOT_APPLICABLE already set for non-missing rules.
            return statuses, reasons, []

        cmd = [
            "semgrep", "scan",
            "--config", str(pack.rules_yaml_path),
            "--sarif",
            "--timeout", str(timeout),
            "--jobs", str(jobs),
        ] + [str(f) for f in files]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True, encoding="utf-8",
                errors="replace",
                timeout=timeout + 10,
                cwd=str(repo_root),
            )
        except subprocess.TimeoutExpired:
            msg = "rulepack %s: semgrep scan timed out (%ss)" % (pack.name, timeout)
            self.infra_errors.append(msg)
            for rule in pack.rules:
                if rule.id not in pack.missing_rule_ids:
                    statuses[rule.id] = "NOT_RUN"
                    reasons[rule.id] = "semgrep timeout (%ss)" % timeout
            return statuses, reasons, []
        except (OSError, subprocess.SubprocessError) as exc:
            msg = "rulepack %s: semgrep failed: %s" % (pack.name, exc)
            self.infra_errors.append(msg)
            for rule in pack.rules:
                if rule.id not in pack.missing_rule_ids:
                    statuses[rule.id] = "NOT_RUN"
                    reasons[rule.id] = "semgrep error: %s" % exc
            return statuses, reasons, []

        if result.returncode >= 2:
            msg = (
                "rulepack %s: semgrep error (exit %d): %s"
                % (pack.name, result.returncode, result.stderr[:200])
            )
            self.infra_errors.append(msg)
            for rule in pack.rules:
                if rule.id not in pack.missing_rule_ids:
                    statuses[rule.id] = "NOT_RUN"
                    reasons[rule.id] = "semgrep exit %d" % result.returncode
            return statuses, reasons, []

        parsed = parse_semgrep(result.stdout, exit_code=result.returncode)
        findings: list[Finding] = []
        for item in parsed:
            if isinstance(item, ToolError):
                self.infra_errors.append(item.message)
            elif isinstance(item, Finding):
                findings.append(item)

        matched_ids = {f.rule_id for f in findings}

        for rule in pack.rules:
            if rule.id in pack.missing_rule_ids:
                continue
            if rule.id in matched_ids:
                statuses[rule.id] = "VIOLATION"
                reasons[rule.id] = None
            else:
                statuses[rule.id] = "CLEAN"
                reasons[rule.id] = None

        advisories = self._findings_to_advisories(pack.name, findings)
        return statuses, reasons, advisories

    def _findings_to_advisories(
        self, pack_name: str, findings: list[Finding]
    ) -> list[AdvisoryFinding]:
        """Convert parser Finding objects to AdvisoryFinding for a pack."""
        advisories: list[AdvisoryFinding] = []
        for f in findings:
            advisories.append(
                AdvisoryFinding(
                    id="rulepack:%s:%s:%s:%s" % (
                        pack_name, f.file, f.line, f.rule_id
                    ),
                    axis="rulepack:%s" % pack_name,
                    file=f.file,
                    line_range=(f.line, f.end_line),
                    description=f.message,
                    attribution="semgrep-ce/rulepack",
                )
            )
        return advisories

    def _build_matrix(
        self,
        statuses: dict[str, dict[str, str]],
        reasons: dict[str, dict[str, Optional[str]]],
    ) -> RulepackMatrix:
        """Build RulepackMatrix from per-pack status maps."""
        packs: list[dict] = []
        for pack in self.packs:
            pack_dict: dict = {
                "name": pack.name,
                "version": "",
                "source": "",
                "rules": [],
            }
            # Try to read version/source from meta.yaml for transparency.
            try:
                meta = yaml.safe_load(
                    pack.meta_yaml_path.read_text(encoding="utf-8")
                )
                if isinstance(meta, dict):
                    pack_dict["version"] = meta.get("version", "")
                    pack_dict["source"] = meta.get("source", "")
            except Exception:  # noqa: BLE001
                pass
            pack_statuses = statuses.get(pack.name, {})
            pack_reasons = reasons.get(pack.name, {})
            for rule in pack.rules:
                rule_dict = {
                    "id": rule.id,
                    "status": pack_statuses.get(rule.id, "NOT_RUN"),
                    "findings": 0,
                }
                reason = pack_reasons.get(rule.id)
                if reason:
                    rule_dict["reason"] = reason
                pack_dict["rules"].append(rule_dict)
            packs.append(pack_dict)
        return RulepackMatrix(packs=packs)

    def _print_summary(self) -> None:
        """Print one-line rulepack summary to stderr."""
        if self.matrix is None:
            return
        counts = {"VIOLATION": 0, "CLEAN": 0, "NOT_APPLICABLE": 0, "NOT_RUN": 0}
        for pack in self.matrix.packs:
            for rule in pack.get("rules", []):
                counts[rule.get("status", "NOT_RUN")] += 1
        total = sum(counts.values())
        print(
            "rulepack: %d rules [%d violation, %d clean, %d n/a, %d not_run]"
            % (
                total,
                counts["VIOLATION"],
                counts["CLEAN"],
                counts["NOT_APPLICABLE"],
                counts["NOT_RUN"],
            ),
            file=sys.stderr,
        )

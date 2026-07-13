# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-10 falsifier + autofixer + revert_fn factories.

Centralizes "which impl do we instantiate" decisions so cli.py stays
declarative and Phase 4 can swap impls without touching the CLI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from .autofix import AutoFixer, FixOutcome, StubAutoFixer
from .baseline import ResolvedReview
from .disposition import Disposition
from .falsify import Falsifier, StubFalsifier
from .e2e_check import run_e2e_check
from .mutation import run_mutation
from .state import StateFinding


def build_falsifier(
    engine: str,
    backend=None,
) -> Falsifier:
    """STATE-10 engine factory.

    engine = "auto": try RealFalsifier import; fall back to stub if absent.
    engine = "stub": always StubFalsifier.
    engine = "real": RealFalsifier (falsify_real.py); NotImplementedError only if
        that import genuinely fails.
    """

    if engine == "stub":
        return StubFalsifier()
    if engine == "auto":
        try:
            from .falsify_real import RealFalsifier  # noqa: F401
            return RealFalsifier(backend=backend)
        except ImportError:
            return StubFalsifier()
    if engine == "real":
        try:
            from .falsify_real import RealFalsifier
            return RealFalsifier(backend=backend)
        except ImportError:
            raise NotImplementedError(
                "--falsification-engine=real requires falsify_real.py "
                "(import failed). Use "
                "--falsification-engine=auto or =stub."
            )
    raise ValueError(
        "unknown engine: %r (expected auto|stub|real)" % engine
    )


def build_autofixer(resolved: ResolvedReview) -> AutoFixer:
    """v2.0 returns StubAutoFixer; non-git wraps to prevent PARSE_FAIL.

    R2-M1: non-git mode wraps StubAutoFixer in _NonGitSafeAutoFixer
    to convert PARSE_FAIL -> NO_CHANGE, preventing revert_fn from
    being invoked (which would raise NotImplementedError per B1).
    """
    base = StubAutoFixer()
    if resolved.mode_hint == "non-git":
        return _NonGitSafeAutoFixer(base)
    return base


class _NonGitSafeAutoFixer(AutoFixer):
    """R2-M1 wrapper: convert PARSE_FAIL -> NO_CHANGE in non-git mode.

    Prevents StateMachine from calling revert_fn (which raises
    NotImplementedError per B1 in non-git mode). Behavior:
      - PARSE_FAIL -> NO_CHANGE (consumes fix budget; no revert call)
      - SUCCESS / NO_CHANGE / EXCEPTION pass through unchanged

    R3-7: fix signature byte-equal to AutoFixer ABC.
    """

    def __init__(self, inner: AutoFixer):
        super().__init__()
        self._inner = inner

    def fix(self, finding: StateFinding, mode_hint: str) -> FixOutcome:
        """Wrap inner fix; convert PARSE_FAIL to NO_CHANGE."""
        outcome = self._inner.fix(finding, mode_hint)
        if outcome == FixOutcome.PARSE_FAIL:
            return FixOutcome.NO_CHANGE
        return outcome


def build_revert_fn(
    resolved: ResolvedReview, cwd: Path
) -> Callable[[StateFinding], None]:
    """Build revert_fn for StateMachine constructor.

    Dispatches on resolved.mode_hint:
      "git"     -> git restore <file>
      "non-git" -> NotImplementedError (B1: v2.0 limitation)
    """
    if resolved.mode_hint == "git":
        return _make_git_restore(cwd)
    if resolved.mode_hint == "non-git":
        return _make_snapshot_restore(cwd, resolved)
    raise ValueError("unknown mode_hint: %r" % resolved.mode_hint)


def _make_git_restore(
    cwd: Path,
) -> Callable[[StateFinding], None]:
    """Git mode revert: restore file to index version."""
    def _revert(finding: StateFinding) -> None:
        subprocess.run(
            ["git", "restore", "--", finding.file],
            cwd=str(cwd), check=True,
        )
    return _revert


def _make_snapshot_restore(
    cwd: Path, resolved: ResolvedReview
) -> Callable[[StateFinding], None]:
    """B1: non-git revert NOT supported in v2.0.

    02-03 Snapshot stores content_hash only, not raw content.
    revert_fn raises NotImplementedError unconditionally.
    """
    def _revert(finding: StateFinding) -> None:
        raise NotImplementedError(
            "non-git autofix revert is not supported in v2.0 "
            "(02-03 Snapshot stores content_hash, not raw content). "
            "Use git mode for autofix, or configure autofixer to "
            "avoid PARSE_FAIL outcomes in non-git mode. "
            "Tracked as v2.x candidate."
        )
    return _revert


def build_l2_runner() -> Callable:
    """Build l2_runner (mutation testing) callable.

    Returns a callable with signature:
        (diff_files: list[str], baseline_cmd: list[str])
        -> tuple[list[StateFinding], list[str]]

    If mutmut is not on PATH, returns a no-op callable that produces
    a single MUTATION_SKIPPED finding (soft dependency).

    The returned callable delegates to run_mutation from the mutation
    module when mutmut is available.
    """
    if shutil.which("mutmut") is None:
        # mutmut not available, return no-op with MUTATION_SKIPPED
        def _no_mutation(
            diff_files: list[str],
            baseline_cmd: list[str],
        ) -> tuple[list[StateFinding], list[str]]:
            findings = [
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-unavailable",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="mutmut not installed (soft dependency)",
                )
            ]
            infra_errors = ["mutmut not found on PATH"]
            return (findings, infra_errors)
        return _no_mutation

    # mutmut is available, delegate to run_mutation
    return run_mutation


def build_e2e_checker() -> Callable:
    """Build e2e_checker callable for R3 coverage heuristic.

    Returns a callable with signature:
        (diff_text: str, repo_root: Path)
        -> tuple[list[StateFinding], list[str]]

    Unlike build_l2_runner, there is no external-binary availability check:
    e2e_check has no soft dependency (unidiff is a hard dep already used by
    diff.py). The factory returns run_e2e_check directly.

    The factory exists for symmetry with build_l2_runner so plan 03-03 can
    inject it into the state machine the same way as l2_runner, and a future
    variant (e.g. with config loading) can be swapped without touching
    machine.py.
    """
    return run_e2e_check


def build_l1_provider(
    engine: str,
    resolved: "ResolvedReview",
    backend=None,
    conventions_digest: str = "",
    post_image: str = "",
    graph_impact_context: str = "",
    contract_spec: str = "",
    breaker=None,
    max_attempts: int = 5,
    initial_delay_s: float = 2.0,
) -> "Callable":
    """Build l1_provider. Returns (findings, excerpts, Usage, duration_s) 4-tuple.

    Args:
        engine: falsification engine name (e.g. "auto", "stub").
        resolved: resolved review with git_diff and source_files.
        backend: BackendConfig for llm_invoke, or None for default.
        conventions_digest: compact naming conventions for reviewer context (D11).
        post_image: current content of changed files for excerpt verification (D11).
        graph_impact_context: blast-radius impact table for reviewer context.
        contract_spec: cross-repo contract reference for reviewer context.
    """
    from .llm_invoke import Usage

    if engine == "stub":
        return lambda: ([], [], Usage(), 0.0)

    from .llm_invoke import LLMInvokeError, llm_invoke
    from .reviewer_json import (
        _collect_excerpts,
        _json_to_state_findings,
        validate_reviewer_json,
    )

    def _provider() -> tuple:
        diff_text = resolved.git_diff or ""
        if not diff_text:
            return ([], [], Usage(), 0.0)

        pass_configs = [
            ("qodo", "structural code reviewer: correctness and logic errors"),
            ("expert", "senior engineer: SOLID, architecture, security"),
            ("adversarial", "adversarial QE: assume bugs exist"),
        ]

        # -- Build all prompts before execution -------------------------
        prompts = []
        for _pn, role in pass_configs:
            prompt = (
                "You are a " + role + ". Review this diff.\n"
                'Return JSON: {"findings": [{"file": "...", "line": N, '
                '"severity": "P0"|"P1"|"P2"|"P3", '
                '"description": "..."}], '
                '"code_excerpts": [{"file": "...", "start_line": N, '
                '"end_line": M, "content": "..."}]}\n'
                "Each diff hunk MUST have at least one code_excerpt.\n"
                "Even if findings is empty, provide code_excerpts "
                "covering each changed hunk.\n"
                "code_excerpts content must be actual source code lines, "
                "not diff format -- no +/- prefixes, no @@ headers.\n"
            )
            if post_image:
                prompt += (
                    "\n## Post-Image (current file content)\n"
                    + post_image + "\n"
                )
            if conventions_digest:
                prompt += (
                    "\n## Conventions Digest\n"
                    + conventions_digest + "\n"
                )
            if graph_impact_context:
                prompt += (
                    "\n## Blast Radius Context\n"
                    + graph_impact_context + "\n"
                )
            if contract_spec:
                prompt += (
                    "\n## Contract Reference\n"
                    + contract_spec + "\n"
                )
            prompt += "\nDiff:\n" + diff_text
            prompts.append(prompt)

        # -- Execute passes ---------------------------------------------
        # CLI backends use a module-global _active_proc that is not
        # thread-safe; keep serial for those.
        is_cli = backend is None or backend.type == "cli"

        def _run_pass(idx):
            pn = pass_configs[idx][0]
            r = llm_invoke(
                prompts[idx], backend=backend,
                max_attempts=max_attempts,
                initial_delay_s=initial_delay_s,
            )
            if (r.usage.input_tokens > 0
                    or r.usage.output_tokens > 0):
                bname = backend.name if backend else "unknown"
                sys.stderr.write(
                    "[%s:%s] %d in / %d out tokens\n"
                    % (bname, pn,
                       r.usage.input_tokens,
                       r.usage.output_tokens)
                )
            return r

        pass_results = []
        _parallel_wall = 0.0
        if is_cli:
            for i in range(len(pass_configs)):
                try:
                    pass_results.append(_run_pass(i))
                except Exception as exc:
                    pass_results.append(exc)
        else:
            from concurrent.futures import ThreadPoolExecutor
            _t0 = time.monotonic()
            with ThreadPoolExecutor(
                max_workers=len(pass_configs),
            ) as pool:
                futures = [
                    pool.submit(_run_pass, i)
                    for i in range(len(pass_configs))
                ]
                for f in futures:
                    try:
                        pass_results.append(f.result())
                    except Exception as exc:
                        pass_results.append(exc)
            _parallel_wall = time.monotonic() - _t0

        # -- Fold results in fixed order --------------------------------
        all_candidates = []
        all_excerpts = []
        seen = set()
        total_input = 0
        total_output = 0
        total_duration = 0.0

        for i, (pass_name, _role) in enumerate(pass_configs):
            pr = pass_results[i]

            if isinstance(pr, LLMInvokeError):
                print(
                    "code-forge: L1 pass '%s' failed: %s"
                    % (pass_name, pr),
                    file=sys.stderr,
                )
                from .disposition import Disposition
                from .state import StateFinding
                all_candidates.append(StateFinding(
                    id="l1-%s-invoke-fail" % pass_name,
                    fingerprint="invoke-fail-%s" % pass_name,
                    source="INFRA",
                    disposition=Disposition.CONFIRMED,
                    file="<llm-invoke>",
                    line_range=[0, 0],
                    description="L1 invoke failed: %s" % pr,
                    is_timeout=pr.is_timeout,
                ))
                if breaker is not None:
                    if pr.is_timeout:
                        breaker.record_timeout()
                    else:
                        breaker.record_other_error()
                total_duration += pr.duration_s
                continue

            if isinstance(pr, Exception):
                print(
                    "code-forge: L1 pass '%s' UNEXPECTED: %s: %s"
                    % (pass_name, type(pr).__name__, pr),
                    file=sys.stderr,
                )
                from .disposition import Disposition
                from .state import StateFinding
                all_candidates.append(StateFinding(
                    id="l1-%s-invoke-fail" % pass_name,
                    fingerprint="invoke-fail-%s" % pass_name,
                    source="INFRA",
                    disposition=Disposition.CONFIRMED,
                    file="<llm-invoke>",
                    line_range=[0, 0],
                    description="L1 invoke failed: %s: %s"
                    % (type(pr).__name__, pr),
                ))
                if breaker is not None:
                    breaker.record_other_error()
                continue

            result = pr
            response = result.content
            total_input += result.usage.input_tokens
            total_output += result.usage.output_tokens
            total_duration += result.duration_s

            try:
                validated = validate_reviewer_json(response)
            except ValueError as exc:
                from .disposition import Disposition
                from .state import StateFinding
                all_candidates.append(StateFinding(
                    id="l1-%s-schema-fail" % pass_name,
                    fingerprint="schema-fail-%s" % pass_name,
                    source="INFRA",
                    disposition=Disposition.CONFIRMED,
                    file="<schema-validation>",
                    line_range=[0, 0],
                    description="schema validation failed: %s" % exc,
                ))
                if breaker is not None:
                    breaker.record_other_error()
                continue

            if breaker is not None:
                breaker.record_success()

            all_excerpts.extend(_collect_excerpts(validated))

            # Coverage guard: when the pass reports zero findings,
            # verify that excerpts cover every changed file before
            # accepting it as a genuine clean pass. A truncated
            # response can parse as valid JSON with findings==[]
            # and partial excerpts, producing a false-green verdict.
            if len(validated["findings"]) == 0 and diff_text:
                from .verify import parse_diff_files
                changed = set(parse_diff_files(diff_text).keys())
                covered = {
                    exc.get("file", "")
                    for exc in validated.get("code_excerpts", [])
                }
                covered.discard("")

                def _normalize(p: str) -> str:
                    p = p.replace("\\", "/")
                    if p.startswith("./"):
                        p = p[2:]
                    if p.startswith("/"):
                        p = p.lstrip("/")
                    return p

                norm_covered = {_normalize(c) for c in covered}
                uncovered = []
                for cf in changed:
                    ncf = _normalize(cf)
                    # Exact normalized match
                    if ncf in norm_covered:
                        continue
                    # Basename fallback: match only when unambiguous
                    bn = os.path.basename(ncf)
                    bn_matches = [
                        c for c in norm_covered
                        if os.path.basename(c) == bn
                    ]
                    if len(bn_matches) == 1:
                        continue
                    # Suffix match: cross-repo absolute paths
                    matched = False
                    for nc in norm_covered:
                        if nc.endswith("/" + ncf) or ncf.endswith("/" + nc):
                            matched = True
                            break
                    if matched:
                        continue
                    uncovered.append(cf)

                if uncovered:
                    desc_files = ", ".join(uncovered[:3])
                    if len(uncovered) > 3:
                        desc_files += " (and %d more)" % (
                            len(uncovered) - 3
                        )
                    from .disposition import Disposition
                    from .state import StateFinding
                    all_candidates.append(StateFinding(
                        id="l1-%s-incomplete-coverage" % pass_name,
                        fingerprint=(
                            "incomplete-coverage-%s" % pass_name
                        ),
                        source="INFRA",
                        disposition=Disposition.CONFIRMED,
                        file="<coverage-guard>",
                        line_range=[0, 0],
                        description=(
                            "L1 pass '%s' returned 0 findings but "
                            "excerpts do not cover: %s"
                            % (pass_name, desc_files)
                        ),
                    ))
                    continue

            for sf in _json_to_state_findings(validated, pass_name):
                if sf.fingerprint in seen:
                    continue
                seen.add(sf.fingerprint)
                all_candidates.append(sf)
        if not is_cli:
            total_duration = _parallel_wall
        return (all_candidates, all_excerpts, Usage(total_input, total_output), total_duration)

    return _provider


def build_sampling_l1_provider(
    session,                        # ServerSession (no type annotation)
    loop,                           # asyncio event loop from get_running_loop()
    resolved: "ResolvedReview",
    conventions_digest: str = "",
    post_image: str = "",
    graph_impact_context: str = "",
    contract_spec: str = "",
) -> "Callable":
    """Build L1 provider that dispatches via MCP sampling.

    Like build_l1_provider but calls invoke_sampling instead of llm_invoke.
    The returned closure is sync (L1Provider type). It bridges to async via
    asyncio.run_coroutine_threadsafe using the captured event loop reference.

    Args:
        session: MCP ServerSession with create_message capability.
        loop: The MCP server's asyncio event loop, captured via
            asyncio.get_running_loop() in the async handler BEFORE
            dispatching to asyncio.to_thread.
        resolved: Resolved review with git_diff.
        (remaining args same as build_l1_provider)
    """
    import asyncio as _aio
    import sys as _sys

    from .disposition import Disposition
    from .llm_invoke import LLMInvokeError, Usage, invoke_sampling
    from .reviewer_json import (
        _collect_excerpts,
        _json_to_state_findings,
        validate_reviewer_json,
    )
    from .state import StateFinding

    def _provider() -> tuple:
        diff_text = resolved.git_diff or ""
        if not diff_text:
            return ([], [], Usage(), 0.0)

        pass_configs = [
            ("qodo", "structural code reviewer: correctness and logic errors"),
            ("expert", "senior engineer: SOLID, architecture, security"),
            ("adversarial", "adversarial QE: assume bugs exist"),
        ]

        # -- Build all prompts before execution -------------------------
        prompts = []
        for _pn, role in pass_configs:
            prompt = (
                "You are a " + role + ". Review this diff.\n"
                'Return JSON: {"findings": [{"file": "...", "line": N, '
                '"severity": "P0"|"P1"|"P2"|"P3", '
                '"description": "..."}], '
                '"code_excerpts": [{"file": "...", "start_line": N, '
                '"end_line": M, "content": "..."}]}\n'
                "Each diff hunk MUST have at least one code_excerpt.\n"
                "Even if findings is empty, provide code_excerpts "
                "covering each changed hunk.\n"
                "code_excerpts content must be actual source code lines, "
                "not diff format -- no +/- prefixes, no @@ headers.\n"
            )
            if post_image:
                prompt += "\n## Post-Image (current file content)\n" + post_image + "\n"
            if conventions_digest:
                prompt += "\n## Conventions Digest\n" + conventions_digest + "\n"
            if graph_impact_context:
                prompt += "\n## Blast Radius Context\n" + graph_impact_context + "\n"
            if contract_spec:
                prompt += "\n## Contract Reference\n" + contract_spec + "\n"
            prompt += "\nDiff:\n" + diff_text
            prompts.append(prompt)

        # -- Execute passes concurrently via asyncio.gather -------------
        _system_prompt = (
            "You are a code review tool. Respond with ONLY valid JSON, "
            "no markdown fences, no explanatory text. The JSON must have "
            'keys "findings" (array) and "code_excerpts" (array).'
        )

        async def _gather():
            coros = [
                _aio.wait_for(
                    invoke_sampling(
                        session, prompts[i], max_tokens=16384,
                        temperature=0.0, system_prompt=_system_prompt,
                    ),
                    timeout=300,
                )
                for i in range(len(pass_configs))
            ]
            return await _aio.gather(*coros, return_exceptions=True)

        # All 3 coroutines submitted; parallel tokens consumed even if
        # one fails -- accepted trade-off for 3x wall-clock speedup.
        _t0 = time.monotonic()
        outer = _aio.run_coroutine_threadsafe(_gather(), loop)
        try:
            pass_results = list(outer.result(timeout=330))
        except Exception:
            outer.cancel()
            raise
        _parallel_wall = time.monotonic() - _t0

        # -- Fold results in fixed order --------------------------------
        all_candidates = []
        all_excerpts = []
        seen = set()
        total_duration = 0.0

        for i, (pass_name, _role) in enumerate(pass_configs):
            pr = pass_results[i]

            if isinstance(pr, LLMInvokeError):
                raise pr

            if isinstance(pr, BaseException):
                print(
                    "code-forge: L1 sampling pass '%s' UNEXPECTED: "
                    "%s: %s" % (pass_name, type(pr).__name__, pr),
                    file=_sys.stderr,
                )
                all_candidates.append(StateFinding(
                    id="l1-%s-invoke-fail" % pass_name,
                    fingerprint="invoke-fail-%s" % pass_name,
                    source="INFRA",
                    disposition=Disposition.CONFIRMED,
                    file="<llm-invoke>",
                    line_range=[0, 0],
                    description="L1 sampling invoke failed: %s: %s"
                    % (type(pr).__name__, pr),
                ))
                continue

            result = pr
            total_duration += result.duration_s
            response = result.content

            try:
                validated = validate_reviewer_json(response)
            except ValueError as exc:
                all_candidates.append(StateFinding(
                    id="l1-%s-schema-fail" % pass_name,
                    fingerprint="schema-fail-%s" % pass_name,
                    source="INFRA",
                    disposition=Disposition.CONFIRMED,
                    file="<schema-validation>",
                    line_range=[0, 0],
                    description="schema validation failed: %s" % exc,
                ))
                continue

            all_excerpts.extend(_collect_excerpts(validated))

            # Coverage guard (same as build_l1_provider)
            if len(validated["findings"]) == 0 and diff_text:
                import os
                from .verify import parse_diff_files
                changed = set(parse_diff_files(diff_text).keys())
                covered = {
                    exc_item.get("file", "")
                    for exc_item in validated.get("code_excerpts", [])
                }
                covered.discard("")

                def _normalize(p):
                    p = p.replace("\\", "/")
                    if p.startswith("./"):
                        p = p[2:]
                    if p.startswith("/"):
                        p = p.lstrip("/")
                    return p

                norm_covered = {_normalize(c) for c in covered}
                uncovered = []
                for cf in changed:
                    ncf = _normalize(cf)
                    if ncf in norm_covered:
                        continue
                    bn = os.path.basename(ncf)
                    bn_matches = [c for c in norm_covered if os.path.basename(c) == bn]
                    if len(bn_matches) == 1:
                        continue
                    matched = any(
                        nc.endswith("/" + ncf) or ncf.endswith("/" + nc)
                        for nc in norm_covered
                    )
                    if matched:
                        continue
                    uncovered.append(cf)

                if uncovered:
                    desc_files = ", ".join(uncovered[:3])
                    if len(uncovered) > 3:
                        desc_files += " (and %d more)" % (len(uncovered) - 3)
                    all_candidates.append(StateFinding(
                        id="l1-%s-incomplete-coverage" % pass_name,
                        fingerprint="incomplete-coverage-%s" % pass_name,
                        source="INFRA",
                        disposition=Disposition.CONFIRMED,
                        file="<coverage-guard>",
                        line_range=[0, 0],
                        description=(
                            "L1 pass '%s' returned 0 findings but "
                            "excerpts do not cover: %s" % (pass_name, desc_files)
                        ),
                    ))
                    continue

            for sf in _json_to_state_findings(validated, pass_name):
                if sf.fingerprint in seen:
                    continue
                seen.add(sf.fingerprint)
                all_candidates.append(sf)

        total_duration = _parallel_wall
        return (all_candidates, all_excerpts, Usage(0, 0), total_duration)

    return _provider

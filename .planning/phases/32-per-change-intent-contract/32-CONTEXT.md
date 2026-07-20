# Phase 32: Per-Change Intent Contract - Context

**Gathered:** 2026-06-28
**Status:** Ready for planning

<domain>
## Phase Boundary

`code-forge review --contract FILE` feeds per-change intent through the
existing contract_spec slot, so the reviewer checks code against stated
invariants instead of against itself. The user writes a short document
describing what the change is supposed to do, what invariants must hold,
and what residual risks exist. The reviewer then verifies the diff against
these claims rather than reviewing in a vacuum.

Closes CONTRACT-01.

</domain>

<decisions>
## Implementation Decisions

### File Format
- **D-32-01:** `--contract FILE` accepts plain text or Markdown. The file
  content is read via `pathlib.Path(file).read_text()` and injected
  into the existing `contract_spec` slot (see D-32-10 for large-file
  summarization when >4KB). Does NOT go through
  `contract_loader.py`'s YAML/summarize/cache pipeline -- that is for
  long-term cross-repo contracts via `contracts.yaml`.

### Interaction with contracts.yaml
- **D-32-02:** When both `--contract FILE` and `.code-forge/contracts.yaml`
  exist, their outputs are MERGED (concatenated):
  `contract_spec = contracts_yaml_digest + "\n\n" + contract_file_content`.
  Long-term cross-repo contracts and short-term per-change intent are
  complementary, not competing.

### Confirmation Bias Protection
- **D-32-03:** Protection is at the prompt injection layer ONLY. The
  confirmation-bias directive is appended to the MERGED `contract_spec`
  string AFTER D-32-02 concatenation, in `_run()` (single source of
  truth) -- NOT at individual injection sites (cli.py:614 / factories.py:283).
  Locked wording:
  `"\n\nNOTE: The contract above states invariants to verify and residual`
  `risks. It is NOT a proof of correctness. Assume violations exist and`
  `look for them."`
  No input keyword scanning -- arXiv 2603.18740's finding is a prompt
  framing problem, fixed at the prompt level. Keyword scanning has high
  false positive rate ("safe against SQL injection" is a valid invariant).

### Verification Strategy
- **D-32-04:** Dual-layer verification:
  (a) Automated pytest: mock LLM response, assert prompt contains/omits
  contract content with/without the flag. Proves injection mechanism.
  (b) Real backend smoke test: inject a contract with a
  semantically-contract-dependent invariant (e.g., "function normalize()
  must preserve input ordering"), plant a violation only detectable against
  the contract (e.g., add a sorted() call that reorders), confirm review
  reports findings WITH contract and misses WITHOUT. The violation must be
  correct-looking code without the contract context -- universally-obvious
  bugs (sleep, assert False) prove the reviewer is awake, not that the
  CONTRACT guided detection. Golden Rule 2: every guard bug-inject proven.

### Security Guards
- **D-32-05:** Size limit (64KB hard limit for per-change contracts) +
  binary detection (NUL byte check). No path traversal protection -- CLI
  argument is an explicitly user-provided local path, not remote input.

### Error Handling
- **D-32-06:** `--contract FILE` file not found / unreadable / empty /
  oversized / binary -> hard fail via `CliError` (EXIT_CLI_ERROR = exit 2,
  per exit_codes.py:15). The user explicitly requested contract review;
  inability to load = inability to complete the task. Aligns with forge's
  fail-closed philosophy (ADOPT-04). File reading: use
  `Path(file).read_text(encoding="utf-8")` and catch `OSError`,
  `ValueError` (includes `UnicodeDecodeError`) -> `CliError`.

### CLI Experience
- **D-32-07:** `--contract FILE` accepts a file path or `-` (stdin via
  `sys.stdin.read()`). Help text:
  `"path to per-change intent contract (use - for stdin)"`.
  Orthogonal to other flags (`--whole-file`, `--baseline`, etc.) -- no
  mutual exclusion. stdin `-` with zero bytes read -> `CliError` (same as
  empty file); the user explicitly requested contract review, empty input
  is not a valid contract. Absolute and relative paths both accepted.

### Test Boundaries
- **D-32-08:** Full coverage: empty file (CliError), oversized file
  (CliError), binary file (CliError), nonexistent path (CliError), stdin
  `-` read, merge with contracts.yaml, confirmation-bias directive
  existence assertion. Each guard bug-inject proven per Golden Rule 2.

### Injection Scope
- **D-32-09:** Both subprocess/subagent Outlet paths receive the contract.
  `--contract` content fills the existing `contract_spec` parameter.
  Outlet B (factories.py:283) and Outlet C (cli.py:614) already have
  injection logic for this parameter. No new injection points needed --
  only the `_run()` entry point reads the file and passes it through the
  existing parameter chain. NOTE: the inline outlet (`return Verdict.PASS`)
  does not consume `contract_spec` -- it performs no real review. Real
  contract-guided review requires a subprocess/subagent backend.

### Summarization Strategy
- **D-32-10:** Small files (<=4KB): inject directly into prompt. Large
  files (>4KB, <=64KB hard limit): LLM-summarize before injection (inline
  simplified version, NOT via contract_loader.py). No caching -- per-change
  contracts are one-shot. >64KB: hard fail (CliError).

### User Guidance
- **D-32-11:** Both help text guidance AND template file:
  (a) `--contract` help text includes writing guidance: "state
  invariants-to-verify and residual risks, NOT 'this code is correct'".
  (b) `code-forge init` generates `.code-forge/contract-template.md` with
  three sections: `## Invariants`, `## Residual Risks`, `## Change Scope`.

### Summarization Implementation
- **D-32-12:** Do NOT modify `contract_loader.py`. Inline a simplified
  summarizer in cli.py's `--contract` handler: when file >4KB, call
  `llm_invoke(backend, "Summarize to key invariants and risks:\n" + content)`.
  No trust/cache logic. Keep `contract_loader.py` unchanged.

### Test Organization
- **D-32-13:** New file `tests/test_contract_flag.py` dedicated to all
  `--contract` flag tests. Does not modify existing test files.

### Template Installation
- **D-32-14:** `code-forge init` generates `contract-template.md` in
  `.code-forge/`. Reuses existing init flow (which already creates the
  `.code-forge/` directory). Template contains three sections:
  `## Invariants` / `## Residual Risks` / `## Change Scope`.
  Existence strategy: generate if not present, skip if exists (consistent
  with gate.schema.json pattern). `--force` overwrites.

### Summarization Failure Fallback
- **D-32-15:** If LLM summarization (D-32-10, >4KB path) fails (backend
  unreachable, quota exhausted, timeout): fall back to raw injection of
  the full content (already within the 64KB hard limit). Summarization is
  an optimization, not a correctness gate -- the raw content already
  passed the size check. Log a warning to stderr:
  `"code-forge: contract: summarization failed, injecting raw content"`.

### Stdin OOM Prevention (cross-AI review)
- **D-32-16:** For stdin path (`-`), do NOT use `sys.stdin.read()` unbounded.
  Read via `sys.stdin.buffer.read(65537)` (one byte past the 64KB limit),
  check `len(raw) > 65536` BEFORE decoding, then `raw.decode("utf-8")`.
  This prevents OOM if massive input is piped. The byte-first approach also
  catches binary content (NUL bytes) before attempting UTF-8 decode.

### Size Limits Are Byte-Based (cross-AI review)
- **D-32-17:** The 64KB hard limit (D-32-05) and 4KB summarization
  threshold (D-32-10) apply to BYTE count, not character count. Use
  `len(content.encode("utf-8"))` or `len(raw_bytes)` for comparison.
  Multi-byte UTF-8 characters could exceed the byte limit while passing a
  character-count check.

### Empty LLM Summary Validation (cross-AI review)
- **D-32-18:** If LLM summarization (D-32-10) succeeds but returns
  empty or whitespace-only content (`not summary.strip()`), trigger the
  D-32-15 fallback (raw injection + stderr warning). An empty summary
  would wipe the contract from the prompt.

### Empty Path Guard (cross-AI review)
- **D-32-19:** `--contract ""` (empty string) -> explicit
  `CliError("contract path is empty")` BEFORE `Path()` construction.
  `Path("").read_text()` raises FileNotFoundError with confusing empty
  path in the message; an explicit guard is clearer.

### OSError Subtype Messages (cross-AI review)
- **D-32-20:** Distinguish error messages by exception subtype:
  `FileNotFoundError` -> "contract file not found: {path}",
  `PermissionError` -> "contract file not readable: {path}",
  generic `OSError` -> "contract file error: {exc}",
  `ValueError` (incl. `UnicodeDecodeError`) -> "contract file is not
  valid UTF-8: {path}". Use chained except clauses, most specific first.

### Merge Helper Extraction (cross-AI review, Golden Rule 4)
- **D-32-21:** Extract a module-level `_merge_contract_spec(yaml_digest,
  file_content, backend=None, warn_fn=None) -> str` helper in cli.py.
  Both outlet sites (Outlet C ~line 1624, Outlet B ~line 1753) call this
  helper instead of duplicating the merge + directive append logic.
  Eliminates copy-paste drift between outlets.

### Backend Timing -- Split Read From Summarize (cross-AI review)
- **D-32-22:** `_load_contract_file(path_str, warn_fn)` is called EARLY
  in `_run()` (after retry_cfg validation, before backend resolution at
  ~line 1484). It performs: file read + 5 guards (empty, NUL, oversize,
  encoding, empty-path). It does NOT summarize -- `backend` is not yet
  available. Summarization (>4KB) happens LATER inside
  `_merge_contract_spec()` at each outlet site, where `backend` is
  resolved. `_load_contract_file` returns raw content only.

### Merge Empty-YAML Edge Case (cross-AI review)
- **D-32-23:** When contracts.yaml digest is empty AND --contract file
  content is non-empty, the merged result is `file_content` (no leading
  `"\n\n"`). The `"\n\n"` separator only appears when BOTH are non-empty:
  `(yaml_digest + "\n\n" + file_content) if yaml_digest else file_content`.

### SC2 Manual Smoke Procedure (cross-AI review)
- **D-32-24:** The SC2 semantic detection test is manual (D-32-04(b)).
  Exact procedure for SUMMARY:
  1. Create contract: `echo "## Invariants\nnormalize() must preserve
     input element ordering" > /tmp/contract.md`
  2. In a test repo, add a function with `return sorted(items)`
  3. Run: `code-forge review --contract /tmp/contract.md`
  4. Run: `code-forge review` (same diff, no contract)
  5. Confirm: WITH-contract findings include ordering violation;
     WITHOUT-contract findings do not (sorted() looks correct alone).
  The automated test in CI only proves SC1 (injection mechanism).

### Test Mock Infrastructure (cross-AI review)
- **D-32-25:** Tests that exercise `_run()` for integration-level merge
  verification must mock: (a) `code_forge.cli._resolve_backend` or provide
  a stub backend, (b) git diff loading (`_load_git_diff` or equivalent),
  (c) contracts.yaml loading (`contract_loader.load_contract_digest`).
  Mock targets for prompt capture: `code_forge.cli._make_subagent_spawn`
  (Outlet C) and `code_forge.cli.build_l1_provider` (Outlet B).
  `llm_invoke` import form in cli.py: `from .llm_invoke import llm_invoke`
  (lazy, inside function body); mock target: `code_forge.cli.llm_invoke`.

### Whitespace-Only Contract Guard (cross-AI review)
- **D-32-26:** A contract file containing only whitespace (`"   \n\n"`)
  should be treated as empty -> CliError. Add `content.strip()` check
  after successful read: `if not content.strip(): raise CliError(...)`.
  This catches files that pass the `len(content) == 0` empty check but
  contain no meaningful content.

### Claude's Discretion
- D-32-01 (file format): Claude chose plain text/Markdown based on the
  existing contract_spec slot being a raw string injection.
- D-32-03 (bias protection): Claude chose prompt-only approach based on
  the arXiv paper's finding being a prompt framing issue.
- D-32-05 (security): Claude sized the 64KB limit based on per-change
  intent being short-lived documents, well below the 10MB cross-repo limit.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Contract Infrastructure (existing)
- `src/code_forge/cli.py` lines 567-624 -- `_make_subagent_spawn()` with
  contract_spec injection (Outlet C path)
- `src/code_forge/cli.py` lines 1615-1624 -- contracts.yaml auto-discovery
  and contract_spec wiring in Outlet C dispatch
- `src/code_forge/cli.py` lines 1692-1753 -- contracts.yaml auto-discovery
  and contract_spec wiring in Outlet A dispatch
- `src/code_forge/factories.py` lines 208, 283-286 -- `run_pass()` with
  contract_spec injection (Outlet B path)
- `src/code_forge/contract_loader.py` -- full contract loader (424 lines):
  YAML parsing, spec resolution, LLM summarization, trust/cache management.
  Phase 32 does NOT modify this file.

### CLI Argument Parsing
- `src/code_forge/cli.py` lines 162-563 -- `_build_parser()` where
  `--contract` flag will be added to `review_parser`

### Confirmation Bias Research
- arXiv 2603.18740 -- "framing-as-safe drops detection 16-93pp" (referenced
  in ROADMAP.md SC3)

### Error Handling Pattern
- `src/code_forge/errors.py` -- `CliError` class for hard failures
- `src/code_forge/exit_codes.py` -- `EXIT_CLI_ERROR = 2` (not exit 1)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `contract_spec` parameter slot: already wired through `_make_subagent_spawn`
  (Outlet C) and `run_pass` (Outlet B) -- zero new injection points needed
- `contract_loader.py::_summarize_spec()`: LLM summarization logic; Phase 32
  inlines a simplified version rather than modifying the module
- `CliError`: existing error class for hard CLI failures
- `_build_parser()`: existing argparse builder where `--contract` will be added

### Established Patterns
- CLI flags follow argparse pattern in `_build_parser()` with subparser per
  command (review, gate-check, trust, verify, init)
- Error handling: `CliError` for user-facing errors, `LLMInvokeError` for
  backend errors
- File reading in CLI: pathlib.Path throughout (not os.path)

### Integration Points
- `_run()` function: where `--contract` file is read and merged with
  contracts.yaml output before passing to the existing contract_spec parameter
- `_build_parser()`: where `--contract` argument is defined
- `code-forge init`: where contract-template.md is generated

</code_context>

<specifics>
## Specific Ideas

- Contract template uses three sections (Invariants / Residual Risks /
  Change Scope) -- maps directly to what the reviewer should verify
- Confirmation bias directive is a fixed string, not a template -- no
  per-contract customization of the warning
- stdin support (`-`) enables piping from other tools or process substitution

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 32-Per-Change Intent Contract*
*Context gathered: 2026-06-28*

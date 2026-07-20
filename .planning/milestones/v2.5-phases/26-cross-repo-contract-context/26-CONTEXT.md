# Phase 26: Cross-Repo Contract Context - Context

**Gathered:** 2026-06-21
**Status:** Ready for planning

<domain>
## Phase Boundary

This phase adds an opt-in mechanism that injects a sibling repository's contract
spec (e.g., kernel YNL spec) into the L1 reviewer's prompt as a read-only
reference. The reviewer sees the spec alongside the diff but does not review or
gate on the sibling. Without the opt-in, behavior is unchanged.

</domain>

<decisions>
## Implementation Decisions

### D-01: Opt-in mechanism -- standalone contracts.yaml
- A new file `.code-forge/contracts.yaml` declares external spec references.
- Separate from gate.yaml to keep responsibilities distinct (gate.yaml owns
  test/backends/trust; contracts.yaml owns external spec injection).

### D-02: Path resolution -- repo env var + relative path
- Each spec entry has `repo:` (supports `$ENV_VAR` expansion) and `path:`
  (relative to the repo root).
- Example: `repo: $KERNEL_REPO`, `path: net/ovs_flow.yaml`.

### D-03: Trust gate -- reuse existing gate.yaml trust mechanism
- contracts.yaml requires the same trust confirmation as gate.yaml
  (`record_trust` / `is_trusted` via `.code-forge/trusted` file).
- Rationale: a malicious contracts.yaml could point to large files (context
  blowup) or sensitive files (information leak). Risk level is comparable to
  gate.yaml.

### D-04: Spec content -- LLM summary for large specs, raw for small
- Specs under `max_raw_size` (configurable per spec, planner picks default)
  are injected as raw text.
- Specs over `max_raw_size` are summarized by the L1 review backend LLM
  before injection.
- The summarizer reuses the same backend configured for L1 review (no
  separate summarizer config).

### D-05: Injection point -- new prompt section before diff
- A new `## Contract Reference` section in the L1 prompt, placed BEFORE
  the diff section.
- Order: `## Post-Image` > `## Conventions Digest` > `## Blast Radius Context` > `## Contract Reference` > `## Diff`.
- Multiple specs get independent sections: `## Contract: {name}`.

### D-06: Cross-repo mode -- inject to threads running L1
- Contract spec injects into every thread that runs an L1 review.
- In the current architecture only the primary thread runs L1 (real
  `build_l1_provider`); sibling threads use a no-op L1 lambda
  (`cross_repo.py:313`), so injection is primary-only today.
- When siblings gain real L1 providers, extend injection to each
  sibling-with-diff at the same `build_l1_provider` call site.
- Add one test asserting siblings receive no `contract_spec` kwarg,
  locking the current constraint.
- *(Amended 2026-06-21: original "inject to repos with diffs" was
  aspirational; gm BLOCKER dissolved by documenting the real constraint.)*

### D-07: Error handling -- graceful empty digest + stderr warning
- Missing spec path, unreadable file, or missing env var: empty digest
  injected, one-line stderr warning (e.g., `contract spec not found: $path`),
  review continues normally.
- Matches SC-2: "produces a graceful empty digest in context -- never an
  error, never a crash."

### D-08: contracts.yaml schema -- repos-grouped with dataclass validation
- Schema groups specs by repo:
  ```yaml
  repos:
    kernel:
      path: $KERNEL_REPO
      specs:
        - path: net/ovs_flow.yaml
          max_raw_size: 16384
        - path: net/tc_flower.yaml
    pnfs:
      path: $PNFS_REPO
      specs:
        - path: docs/pnfs.rst
  ```
- YAML loaded and validated via Python dataclass (not pydantic -- no new
  dependency). Type errors and missing required fields raise CliError.

### D-09: Summary cache -- file hash in repo-local cache
- sha256 of spec file content -> cached summary text.
- Stored in `.code-forge/cache/contracts/` inside the reviewed repo.
- Cache hit when hash matches; cache miss triggers fresh LLM summarization.
- `.code-forge/cache/` should be in `.gitignore`.

### D-10: Test boundary cases (beyond SC-1/2/3)
- Environment variable not set: warn + skip that spec, do not crash.
- Binary / oversized file: `max_raw_size` gate rejects, LLM summary
  attempted; if summary fails, graceful empty + warning.
- Symlink: resolved normally via `Path.resolve()`.

### D-11: Cache storage location -- repo-local
- Cache stored in `.code-forge/cache/contracts/` inside the reviewed repo.
- Not global (~/.code-forge/cache/) to avoid cross-repo cache collisions.

### D-12: Schema validation -- dataclass
- contracts.yaml loaded via `yaml.safe_load()`, validated by constructing
  Python dataclass instances. Type errors and missing required fields
  raise CliError with descriptive message.

### D-13: Trust implementation -- independent hashes, unified CLI
- One `code-forge trust` command records, revokes, and reports trust for
  both gate.yaml and contracts.yaml.
- Storage uses independent hashes so a change to either file is detected
  independently (keyed by realpath, same as gate.yaml).
- The contracts trust hash covers the RESOLVED SPEC FILE CONTENTS (all
  spec paths resolved + their file contents hashed together), not just the
  contracts.yaml manifest. Rationale: the injected payload is the spec
  content (the L1 prompt-injection surface); hashing only the manifest
  would let a post-trust spec edit bypass re-approval.
- `trust --revoke` and `trust --status` cover contracts alongside gate.
- *(Amended 2026-06-21: original "same trust grant" was imprecise;
  separate hashes match the existing gate model which hashes only the
  backends block, not the whole file. GAP1 -- spec content hash -- and
  GAP2 -- revoke/status coverage -- closed per DF-1 adjudication.)*

### Claude's Discretion
- Default `max_raw_size` value (planner picks a practical default based on
  typical backend context limits).
- Summary prompt wording.
- Exact dataclass field names and validation error messages.
- Cache directory structure inside `.code-forge/cache/contracts/`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Cross-repo infrastructure
- `src/code_forge/cross_repo.py` -- existing multi-repo orchestration
  (get_sibling_diff, build_cross_repo_context, run_cross_repo)
- `src/code_forge/factories.py` -- build_l1_provider with conventions_digest
  and post_image injection (the model for contract_spec injection)

### Trust mechanism
- `src/code_forge/trust.py` -- record_trust / is_trusted (if exists)
- `src/code_forge/gate_check.py` -- gate.yaml loading, trust validation

### Config and CLI
- `src/code_forge/cli.py` -- _assemble_post_image, argv parsing
- `docs/configuration.md` -- user-facing docs (Phase 26 additions will go here)

### ROADMAP success criteria
- `.planning/ROADMAP.md` Phase 26 section -- SC-1/2/3 are the acceptance gates

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `build_l1_provider(conventions_digest=, post_image=)` in factories.py:
  established pattern for injecting extra context into L1 prompt. Contract
  spec injection follows the same model.
- `gate_check.py` YAML loading + trust validation: reusable for
  contracts.yaml loading and trust enforcement.
- `cross_repo.py` `build_cross_repo_context()`: assembles joint diff text;
  contract spec assembly can follow the same pattern.

### Established Patterns
- Prompt section injection: conventions_digest and post_image are string
  params appended to the prompt with section headers.
- Trust gate: gate.yaml requires `.code-forge/trusted` file before use.
- Error handling: graceful degradation (empty + warning) matches the
  existing pattern for missing tools.yaml (detect_and_init fallback).

### Integration Points
- `cli.py` `_run()`: loads gate.yaml, assembles post_image/conventions;
  contract spec loading slots in after trust check, before build_l1_provider.
- `cross_repo.py` `_thread_fn()`: per-thread L1 provider creation; contract
  spec injection happens at the build_l1_provider call site.
- `factories.py` `_provider()` inner function: where the prompt is
  assembled; new `## Contract Reference` section inserted here.

</code_context>

<specifics>
## Specific Ideas

- The primary use case is OVS reviewing against kernel YNL specs: when
  reviewing OVS code that implements netlink operations, the kernel's YNL
  YAML spec (which defines the protocol contract) appears in the reviewer's
  context so it can check field names, attribute types, and operation
  semantics against the authoritative source.
- A second use case is pNFS: reviewing pNFS client code against the pNFS
  layout spec (docs/pnfs.rst) in the kernel tree.

</specifics>

<deferred>
## Deferred Ideas

- **Per-spec summarizer backend config**: allow contracts.yaml to specify a
  different LLM for summarization (e.g., fast model for summary, slow for
  review). Deferred to avoid config complexity in v1.
- **Automatic spec discovery**: detect sibling repos and their specs without
  manual contracts.yaml. Would require code-review-graph integration.
  Belongs in Phase 27 or later.
- **Spec diff detection**: when the spec itself changed since last review,
  highlight the delta. Future enhancement.

</deferred>

---

*Phase: 26-cross-repo-contract-context*
*Context gathered: 2026-06-21*

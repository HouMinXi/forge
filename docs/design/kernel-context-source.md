# Kernel observations as a context source

Status: implemented. This note records why the source exists and where the
shipped code differs from the design written on 2026-09-13. Configuration,
trust, and the path rules live in `docs/configuration.md` under "Kernel
observations"; this file does not repeat them.

## What it is for

A kernel review needs three facts the code graph does not index: the value of
a `CONFIG_*` symbol in a named defconfig, whether a device-tree node touched by
the diff is referenced elsewhere, and which `#ifdef` guard surrounds a changed
line. Without them the reviewer either pulls the whole tree into context or
fills the gap from memory.

These facts go in through the existing `ContextSource` seam (`gather()`), not
through a new review pass. A fourth pass would have meant a new name in
`_PASS_NAMES`, a new receipt shape, a clean-round count of four instead of
three, and a fourth model call per review, all to carry a few lines of config
state. The seam was already there.

## What was deliberately not done

- No fourth entry in the pass list, and no `passes/pass3-kernel.md`. The CLI
  loads three pass files and the state machine knows three names.
- `render_blast_radius` is untouched. Kernel rows render through the generic
  context-source table, not the blast-radius one.
- Kernel rows never reach the falsifier. It only reads the
  `removed-symbol-readers` source.
- Nothing turns the source on by itself. A userspace C project can contain a
  `Kbuild` file, so the presence of one is not evidence of a kernel tree.
- The source never scans `arch/*/configs/` to guess a defconfig. A wrong
  `y`/`n` misleads the reviewer more than no value at all.

With the source disabled, the L1 prompt is byte-for-byte unchanged and
`tests/test_l1_prompt_byte_identity.py` stays green.

## Where the implementation departed from the 2026-09-13 design

The design is the commit `3ab63c143` on `feat/kernel-context-source-spec`.
The code landed in `src/code_forge/kernel_context.py` and went further than
that text in several places:

- **Its own module.** The design put `KernelContextSource` in
  `context_sources.py` next to the other two sources. It got a separate module
  instead, because the config parsing and the path checks are larger than the
  source itself.
- **The source name is `kernel_context`,** not `kernel`.
- **Enabling requires a defconfig.** The design allowed `enabled: true` with no
  defconfig and emitted `unset (no defconfig)` rows. The shipped validator
  rejects that combination: an enabled source with an empty path is a
  configuration error, not a source that runs and reports nothing.
- **Unknown keys are rejected.** The design allowed them for forward
  compatibility. `validate_kernel_context` raises on any key outside
  `enabled`, `defconfig`, `max_rows`, `max_chars`.
- **Budgets are bounded.** `max_rows` must be between 1 and 200, `max_chars`
  between 512 and 32000. The design only asked for positive integers.
- **The defconfig path is checked, not just read.** It must be relative to the
  repository, contain no `..`, and resolve to a regular file under the
  workspace root. Symlinks are refused rather than followed. The file must be
  UTF-8 and at most 1 MiB. The design only checked that the file exists.
- **Trust is a separate gate.** Enabling the source or changing its path needs
  `code-forge trust`. An untrusted section is treated as inactive during
  review even when `enabled` is true. The design had no trust step.
- **Only the single-repository CLI path runs it.** A request with non-empty
  `siblings`, or one aimed at an inline or subagent outlet, is rejected while
  the source is enabled. The design did not mention sibling repositories.

## Scope of what a row actually says

The notice prepended to the rendered table states the limit directly: the rows
are observations of the patch text and the named config file, and the
effective build configuration is unknown. The source does not run Kconfig,
merge config fragments, or preprocess device trees. A `y` in the named file is
not a claim about what the build produced.

Rows come only from lines the diff adds or removes. A diff larger than 4 MiB
is cut on a `diff --git` boundary before parsing, so a truncated patch never
produces a partial hunk with an invented line count. One unreadable input
becomes a diagnostic row; an exception in `facts()` lands in `gather().errors`
and leaves the other sources intact.

`snapshot_sha()` returns `None`. The source reads the working tree, on the
same terms as `RemovedSymbolReaders`: the diff under review has already been
applied, and uncommitted files can be part of what is read. Pinning a snapshot
would skip the source on nearly every kernel tree, whose graph database lags
HEAD.

## Tests

`tests/test_kernel_context_source.py` covers the source.
`tests/test_kernel_context_boundaries.py`,
`tests/test_kernel_context_trust.py`, `tests/test_kernel_context_cli.py`,
`tests/test_kernel_context_integration.py`,
`tests/test_kernel_context_mcp.py`, and
`tests/test_kernel_context_review_contracts.py` cover the path checks, the
trust binding, and the CLI behaviour. The case that matters most for the
original promise is the byte-identity one: with the source off, the recorded
L1 prompt digests do not move.

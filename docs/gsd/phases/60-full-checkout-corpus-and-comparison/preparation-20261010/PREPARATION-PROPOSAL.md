> Published planning snapshot, 2026-10-18. Source statements below retain their
> original dates and acceptance limits; historical "current" claims are not current-main
> verification. Private identity labels and unavailable evidence are explained in the
> [cloud start index](../../../README.md). Publication grants no execution admission.

# EVAL60-01 preparation proposal

Status: UNACCEPTED PROPOSAL. This is a checkable preparation sequence for root and independent plan review, not an admitted implementation or a completed phase. No paid comparison follows from this document.

## Decision and current evidence

ROADMAP.md:1164-1173 separates EVAL60-01 corpus preparation from EVAL60-02 measurement. REQUIREMENTS.md:33-36 requires real checkouts, correct bug/fix direction, applicability and context before measuring quality. The Phase 57 corpus at tests/eval/swebench/corpus.yaml contains 150 original entries, paired as 75 HOLD bug and 75 PASS clean reviews. Its PROVENANCE.json says its base_files were reconstructed from patch hunk context, not full source.

The present local inventory is run-02/INVENTORY.json, produced by inventory.py. It independently rereads the 2,096,679-byte official Parquet payload, 75 saved selected rows, the manifest and all diff bytes. The selected official revision is c104f840cc67f8b6eec6f759ebc8b2693d585d4a; the exact original historical generator revision is unproven. All 75 selected raw rows and paired patch directions match the frozen corpus in this observed scope. The original 150-entry order and denominator are retained.

One pair, astropy__astropy-13033, has a local original commit object and full B/F exports under the prior phase60-one-pair/run-01 directory. Current readback found 1,884 files in each export, aac5ec576b94c4e4ba90b38eddc8e24cb1d52b54 for B and 8724ea0099a430bdd8db50fd7fce06ca008f49c0 for derived F, exact file/blob hashes and modes, and successful clean-on-B and bug-on-F applicability checks. The prior ROOT-READBACK.json:1-20 accepted this one source pair for context only. F is a derived tree, not an upstream fix commit. Defect-label correctness and graph context remain untested.

The other 74 pairs (148 entries) have official base commits in saved rows but no locally admitted complete checkout in the checked Phase 60 asset scope. Their exact names are in run-02/SUMMARY.json and run-02/INVENTORY.json. There are zero presently invalid entries under the checks performed. This is not a global claim that their source objects are unavailable. Historical fixture exports and copied evidence are not substitutes for new authenticated per-pair source identities.

## Alternatives and scope

Do nothing and document the gap avoids acquisition but leaves the comparison corpus without 148 real-source entries. Reuse the existing hunk-only base_files preserves diff replay but cannot answer full-context questions. Bulk clone all eleven repositories now would spend unknown resources and compound a wrong identity or export method. The proposed next step is a separately reviewed, finite source-qualification batch using the frozen 75 raw rows, with one repository/commit at a time and an explicit unavailable ledger.

No product abstraction is justified by this preparation. The consumers are the existing corpus runner, a future matched baseline/investigation input builder, and the EVAL60-01 reader who must audit source identity. Demand for a general checkout service has not been established.

## Phase 0: freeze a reviewed acquisition work order

- Create an isolated owned worktree/cache before any new source acquisition. Do not mutate shared refs, index, global Git config, service state or current source exports.
- Freeze the exact corpus/provenance, official Parquet, binding and per-row SHA-256 values, repository URL, base commit, clean/bug diff bytes, and original manifest order. These values come from run-02/INVENTORY.json and the named physical source files, not from a report summary alone.
- Freeze finite per-repository and aggregate wall, process, transfer, retained-disk, file-count and blob-size limits before a request. The one-pair BOUNDS.json is evidence for one prior experiment, not an automatic budget for ten more repositories. Unknown network wire bytes remain null unless independently metered.
- Require an independently reviewed plan and helper diff. The existing pair.py was qualified for one frozen Astropy pair; its identity helper and export mode-reader limitations in phase60-one-pair-review/REPORT.md:70-72 must be addressed before generalizing it. An ignored planning script is not accepted merely because its local lint and controls pass.

STOP if a frozen input changes, the requested commit is absent, source metadata is ambiguous, a cap is reached, a repository needs foreign code or filters, or an export cannot be proven complete. Preserve the exact failed instance in the original ledger. No replacement row or current HEAD fallback is allowed.

## Source qualification contract

The following signatures describe the proposed evidence boundary, not implemented product APIs.

- bind_original(entry_name: str, manifest_entry: dict, official_row: dict | None) -> BoundEntry | Unavailable. Source: original corpus.yaml/provenance plus the exact selected row from the pinned official Parquet. Empty, duplicate, missing or changed IDs/patch bytes return Unavailable with a reason. The row supplies the repository and original base commit; a missing upstream fix commit stays None.
- qualify_pair(bound: BoundEntry, object_store: Path, limits: FrozenLimits) -> PairReceipt | Unavailable. Source: a fetched original commit object whose SHA-1 equals the official base_commit, the frozen clean patch, and its exact reversed bug patch. Refuse missing/unsupported objects, symlinks, gitlinks, LFS pointers, unsafe paths, mode drift, fuzzy/offset application, unexpected changed paths or budget exhaustion. None or partial outputs never become a clean receipt.
- inventory_context(receipt: PairReceipt, requested_paths: list[str]) -> ContextReceipt. Source: verified B/F tree objects and exact exported bytes. Record path, mode, Git object ID, byte hash, line count and availability for touched and unchanged surrounding files. Empty path sets, missing blobs and graph absence are explicit; no foreign project code is imported, built or tested merely to build the corpus.
- account_entry(entry_name: str, pair: PairReceipt | Unavailable) -> EntryReceipt. Preserve one record per original manifest entry with bug F -> B and clean B -> F direction, expected coordinates, applicability and any unavailable reason. A correct coordinate location is not itself proof of a correct defect label. Keep every invalid or missing entry in the denominator.

Each successful pair must prove the original B commit and tree, derive F with the official clean patch, show the existing bug patch returns F to B exactly, validate both post-image identities and line coordinates, and retain a neighboring unchanged source witness. Record upstream fix commit as None unless the official source separately proves one. Private object/cache reuse must still select each entry's exact original commit.

## Staged execution after admission

1. Recheck the accepted Astropy pair against the then-current physical inputs as a known answer. A wrong base, patch byte, direction, source byte, mode and missing context must each fail; restoration must pass. Preserve literal fault differences and any result flip.
2. Pilot the next original pair in manifest order that has a source object available within frozen bounds. A genuine missing object stays unavailable; the pilot choice must not be selected by review quality. Root independently reads raw commands, tree inventories, outcome and resource accounting before broader reuse.
3. Qualify remaining pairs one repository/commit at a time. Save append-only per-attempt results, exact input pins and failures. Report attempted, source-present, invalid and unavailable counts separately, with all 150 original entry names represented exactly once. An aborted attempt does not silently disappear.
4. Root decides EVAL60-01 acceptance from the complete ledger and ROADMAP.md:1170. Explicit unavailable entries satisfy accounting honesty but do not imply that their real context was reconstructed; the usable comparison denominator is the actually qualified matched subset.

Only after qualified Phase 59 capabilities and an independently reviewed measurement plan may EVAL60-02 run. That plan must freeze matched bug/fix and failure inputs, scoring, denominators, call/token/time and unknown-cost accounting, scripts and acceptance before results. No existing diagnostic window becomes a threshold selector. No quality or superiority claim is present here.

## Review and hygiene gate

This proposal has not passed the GSD internal/external plan checkpoints. The diagnostic inventory script has local lint, byte readback and altered-input controls, but no Forge three-cycle source-review credit. Independent review is required before treating it as a gate or reusing it for acquisition. Root owns admission and any STATE/ROADMAP change.

The later implementation work order must close the repository hygiene checklist with evidence: (1) branch ref at intended commit; (2) no unintended tracked files and review evidence under planning; (3) owned worktrees and branches cleaned after delivery; (4) local main and required Gitea head checked when a product delivery occurs; (5) memory changes only on direct user request under current higher-priority instruction; (6) non-ASCII check on changed authored files; (7) no staged/uncommitted leftovers; and (8) deploy targets explicitly marked not applicable to corpus-only preparation unless product deployment is separately authorized. None of these is claimed complete by this proposal.

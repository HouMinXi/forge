> Published planning snapshot, 2026-10-18. Source statements below retain their
> original dates and acceptance limits; historical "current" claims are not current-main
> verification. Private identity labels and unavailable evidence are explained in the
> [cloud start index](../../README.md). Publication grants no execution admission.

# Current source admission audit

Date:2026-10-04. Canonical main/origin12b735fcefac91685dc231b1cd1434588b194a8e was fetched and inspected. The immutable canonical graph has726File nodes; every physical SHA256 matches. Root evidence is .planning/takeover/continuation-fixes-20261004-root/SOURCE-AUDIT.json (full file hashes, actual call edges, referenced-path inventory and proposed output allocation). This audit provides source binding, not independent plan approval or runtime qualification.

Existing concrete source roles and interfaces are mapped in59-PATTERNS.md and59-RESEARCH.md; their baseline remains current. No proposed investigation/journal module currently exists. Missing references to source/test files produced by earlier plans are dependency contracts and must be verified against the producing plan and depends_on graph, not replaced with empty stubs. Missing future closeout artifacts similarly require genuine producer verification.

One actual missing prerequisite affected all18drafts: this SOURCE-AUDIT file had never been produced by the timed-out planner. It is now based on a fresh physical graph/file audit. The previous planner's timeout, missingFINAL and unexplained aggregate ref digest difference remain unresolved historical provenance; creating this audit does not convert that run into successful planning.

Raw-copy boundary: factories.py:41-43 calls deepcopy for dicts; ordinary provider calls it at547 before validation. Grouped provider delegates ordinary handling. Outlet C imports the same helper and calls it at outlet_c.py:130, then imports _raw_response_data at158/calls160. The shared helper must enforce raw-audit admission before optional copying for every caller; a guard only after _raw_response_data(None) misses the first dict copy. Any helper behavior change must include the outlet consumer in regression impact even if no outlet production edit is needed.

Keep actual JSON null distinct from refusal: a declined hostile value must never be converted into an admitted null artifact. Use an explicit admitted/present value contract or unique internal refusal sentinel, consumed before serialization, and test this at the shared snapshot and actual factory append boundaries. Preserve healthy dict salvage, exact admitted values and existing validator behavior; do not serialize a sentinel or introduce audit-only repr/deepcopy calls on declined values.

Every draft remains unadmitted until the corrected set receives independent plan checks, including requirements coverage, dependency ordering, actual entrypoint wiring, finite budgets, qualified useful recipe and historical phase59acceptance. New code requires the established tests/three-review/merge/dual-host gates.

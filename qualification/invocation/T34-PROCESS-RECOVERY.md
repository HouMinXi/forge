# T34: terminal sibling after writer process death

This is an owner/storage qualification slice of [the original T34 matrix](https://github.com/HouMinXi/forge/issues/68#issuecomment-6599263807), not full T34 acceptance.

## Executed boundary

Base `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`, Linux/btrfs, Python 3.13.12 and pytest 9.1.1. Sixteen controlled combinations passed: terminal sibling completed/failed, SIGKILL/healthy finalize, and intact/raw/usage/error oracle controls. One existing pytest configuration warning concerns `parametrize_long_str_id_strategy`.

Call map: isolated writer → `InvocationOwner.create` → input manifest → two sibling operations/leases → entered → first acquired/observe/finish ACK → readiness pipe → controller SIGKILL and reap → independent reader → `AttemptRecorder.reopen_scoped` plus independent journal/raw checks.

The writer uses the actual v2 recorder and supported-filesystem admission. Synthetic bytes and usage are supplied directly; no model, HTTP or external service is called. Successful real fsync calls are observed separately for acquired, observe and finish. Raw publication is matched by device/inode across its temporary-name rename. Readiness is sent only after the ACKs and synchronization checks. Only the owned writer is killed; it creates no descendants. The independent reader does not inherit an owner or process memory and verifies that reads do not modify retained files.

A sibling's exact raw bytes/hash, measured usage and FINISH error class survive. The unfinished sibling remains unknown, without fabricated raw or measured zero. The killed run remains active with no final marker. `audit_complete=True` here means the retained prefix is structurally complete; it does not mean the round returned, passed or finalized.

The healthy branch explicitly completes the second lease and finalizes. Both branches first pass an independent reader before optional corruption. Controls flip one raw byte or change usage/error in a correctly framed journal; a subsequent reader must fail at the specific field oracle, not merely return a generic nonzero exit. These are evidence-oracle controls, not claims of production-site omission qualification.

## Not established

- Actual review/StateMachine round non-pass after parent termination. No production round consumer of this reopened snapshot was exercised.
- Transport arrival, provider receipt, ordinary CLI qualification, ACK reconstruction or automatic recovery permission.
- Physical power loss, kernel crash, storage hardware behavior, or cross-host durability. Process SIGKILL does not flush or invalidate kernel caches.
- The rest of T01–T38, R03, or complete #68 acceptance.

The initial cloud attempt was blocked at real admission because its filesystem was overlay, not a supported ext4/btrfs profile. It did not reach the crash stimulus and is not a product RED. No profile override was used; execution moved to an isolated clone on the user's approved btrfs computer without modifying the original checkout.

## Controlled ordinary LOCAL round gate extension

A separate `test_invocation_t34_round_gate.py` now exercises actual
`StateMachine.run`, the ordinary warning-mode audit host, native v2 storage,
receipt writing, state persistence and the public `verify.run_verify` consumer.
L0/L2/E2E are explicit empty-result seams; HTTP is a bounded synthetic response
at the existing opener. A network/process audit tripwire forbids unexpected
external operations after the writer starts. This is not an installed command,
real provider, CI-mode, strict-mode or physical-power-loss qualification.

The previous-PASS control earns three clean cycles through the real machine
and receipts, then checks the real verifier succeeds. The next round uses the
real `_invoke_api` stack with three ordinary L1 siblings. A barrier ensures all
three have entered the controlled transport. Qodo obtains a complete response,
returns measured usage (7 input / 9 output) and has a completed native audit
attempt before readiness; the other two remain blocked in the controlled
transport. Only then does the test kill and reap the writer.

A fresh process loads the actual persisted state, which is PENDING and not
converged, with latest host action `unavailable`. The real verifier rejects the
old earned window even though its three historical clean cycles remain.
The terminal SARIF helper explicitly refuses a pending LOCAL run rather than
producing PASS; it is not a recovery interface. Independently reopened audit
records retain Qodo's exact raw and null error class, while the other siblings
remain unknown. This round uses successful Qodo; the earlier storage-only
matrix separately exercises a non-null failed-sibling error class. Ordinary
wire usage projection rules are unchanged; no journal-to-accounting credit is
invented.

Five combinations cover absent/present prior PASS with termination/healthy
completion, plus a state-persistence omission control. The latter disables
only the current machine instance's `_persist_state`; it must expose the old
PASS to the independent verifier after termination. This is a deliberate
negative control of the state-publication boundary, not a production defect
and not an exhaustive per-site omission matrix. Restored-source fixtures use
fresh directories. Healthy completion returns PASS with a prior earned window
and ESCALATED after only one new clean cycle without one, preserving the
unchanged three-cycle threshold.

All data is synthetic, and test processes own no descendant processes. The
fixture directory is on the checkout filesystem but outside Git working trees;
the original user checkout and credentials are not modified. The gate result
is bound to this LOCAL host-receipt route, not generalized to every review mode
or to all remaining #68 acceptance groups.

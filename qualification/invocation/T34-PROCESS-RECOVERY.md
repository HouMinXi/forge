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

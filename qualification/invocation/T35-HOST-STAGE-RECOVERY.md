# T35: buffered-host SEAL / ADOPT return boundaries

Base `94ac89d864207a4ccfa1d6ca66fc5a55d6a5f596`. Test-only extension of
#497's direct-owner evidence to the real default buffered host. No production,
strict-policy, #493 or #33 change.

Writer → HostAuditSession → ProcessAuditIo worker → OwnerCaptureStage → native
recorder SEAL/ADOPT → caller handoff. Synthetic bytes pass through capture and
finish without HTTP. Each wrapper calls the actual recorder method first and
observes admitted/non-uncertain internal ACK plus successful native journal
fsync during that call. Raw inode sync evidence covers the capture lifecycle;
it does not imply SEAL/ADOPT wrote another raw file.

Before the wrapper returns, readiness verifies worker identity and absence of
host adoption/accepted/settled credit, attempt adoption, owner refs and owner
settlement. Controller kills only its writer and reaps it before independent
reading. Healthy controls release the same pause and complete FINISH without
run-finalize.

| Killed boundary | Independent evidence |
| --- | --- |
| SEAL internally acknowledged | sealed/unadopted stage, verified checkpoint, zero unknown tail; no scoped ref, dispatch/attempt unknown; incomplete run |
| ADOPT internally acknowledged | one settled_acquired and adopted stage; precisely bound ref and entered_api_send dispatch; unknown unfinished attempt; active valid prefix |

Both scoped and stage readers bind run/attempt/artifact/layer/observation
identities and fixed synthetic raw bytes/hash/length. Healthy FINISH fields
are checked exactly. Usage stays unknown. All files remain byte-identical
across reads; no final marker exists. No diagnostic snapshot reconstructs
caller ACK, authorizes a send/adoption/retry or credits a review round.

An unadopted fixture mislabelled adopted must fail at adoption-oracle; omitting
the pause must fail cutpoint-oracle without readiness. These are oracle
sensitivity controls, not every production omission site. Writer and reader
forbid network/descendant processes. Waits and cleanup are bounded, and the
original user checkout and credentials are untouched.

This is default-host controlled process death, not physical power loss,
kernel crash, strict-mode qualification, external provider, installed CLI,
whole-review gate or complete T35/#68 acceptance. The original SIGKILL does
not invalidate kernel caches. #497 retains the separate direct-owner
start/raw/finish evidence; this pair closes only the identified buffered
SEAL/ADOPT return-before-handoff combination.

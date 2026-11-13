# #441 execution binding review-support candidate

NOT production wiring. NOT an approved schema or G1 acceptance.

Files: execution_binding.py and test_execution_binding.py.
Run in this directory: python -m unittest -v test_execution_binding

ExecutionContext is issued by the host once per new execution. HostBoundRunner
requires CURRENT context and discovery on every call, admits before delegation,
and returns a deep copied result so caller list/nested-dict mutation cannot
change the delegate through the returned value. Existing error messages are
exposed as a tuple. No actual/cache origin is inferred from findings.

This is an internal adapter proposal, NOT AdvisoryAxisRunner-compatible production
wiring: the new keyword arguments require a reviewed host call site. No SQL,
provider, State, gating, receipt or audit/owner code is changed. A fresh delegate
must be owned exclusively; this helper cannot prevent an external owner from
mutating a separately retained delegate. It is thread-confined.

DiscoveryContext must include ALL actual locator dependencies. The host capture
function, provider default-location environment inputs, production consumers,
persistent root, strict record decoder and published/not_published/unknown writer
are not implemented. Ambient environment may change between capture and use;
production must use a frozen configuration or reject changed inputs. The adapter
alone does not enforce that contract or detect graph/registry content changes.

Local Python 3.12.8 measured course: healthy 8 tests PASS; mismatch guard disabled
in memory: 8 test methods, 7 unittest failure records (including subtests and a
parent assertion), 0 errors/skips; restored 8 tests PASS. This is a controlled
mutation of the candidate, NOT a pre-existing Forge failure. No Forge pytest,
SQLite query-count control, CI or T01-T20 production acceptance is claimed.

Schema remains a candidate: field/resource bounds, repository encoding/privacy,
entry lifecycles, no-overwrite publication and failure delivery need G0 decisions.
#441 and #33 remain open; parent capability boxes are not completion credit.

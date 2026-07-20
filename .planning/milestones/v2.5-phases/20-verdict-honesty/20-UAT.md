---
status: complete
phase: 20-verdict-honesty
source: [20-01-SUMMARY.md, 20-02-SUMMARY.md, 20-03-SUMMARY.md]
started: 2026-06-13T00:00:00Z
updated: 2026-06-13T04:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. smoke-run creates receipt file
expected: Run `code-forge smoke-run --surface nftables -- echo ok` in a git repo. File `.code-forge/smoke-receipts/smoke-receipt-nftables.json` is created with status=VERIFIED, surface="nftables", exit_code=0, and a non-empty diff_sha256 field.
result: pass
evidence: CLI run confirmed -- status=VERIFIED, surface=nftables, exit_code=0, diff_sha256=f7527656a4e298ff...

### 2. Smoke status always displayed during review
expected: Run `code-forge review` on any diff. The stderr output includes a `--- Smoke Status ---` section, even if no smoke receipts exist and the LLM finds no runtime surfaces. The section never silently disappears.
result: pass
evidence: |
  CLI run confirmed -- `--- Smoke Status ---` appeared even when all 3 static
  review passes failed with infra errors. Section rendered regardless.
  Unit tests: 6/6 (tests/test_runtime_machine.py -k smoke_status).

### 3. UNVERIFIED surfaces shown in review
expected: Review a diff touching runtime side effects. With no receipts, smoke status shows "NOT VERIFIED: [<surface>]" for detected surfaces.
result: pass
evidence: |
  `code-forge review --backend deepseek` on firewall.sh (nft + systemctl):
    "smoke: 0/2 surfaces verified; NOT VERIFIED: [nftables rules, systemd units]"

### 4. Verified surfaces shown after smoke-run
expected: After smoke-run for a surface, review shows it as verified with fingerprint.
result: pass
evidence: |
  Ran: code-forge smoke-run --surface "nftables rules" -- echo ok
  (receipt stored as nftables-rules due to filename sanitization)
  Then: code-forge review --backend deepseek
  Output: "smoke: 1/2 surfaces verified; NOT VERIFIED: [systemd units] (verified: nftables rules[42002ed1])"
  Surface name normalization fix (d895ce3) ensures "nftables-rules" receipt matches
  LLM surface "nftables rules" via hyphen/underscore -> space equivalence.

### 5. RUNTIME advisory never blocks verdict
expected: RUNTIME findings never change PASS/HOLD verdict or reset cycle counter.
result: pass
evidence: |
  Unit tests: 7/7 (tests/test_runtime_machine.py -k advisory).
  CLI: review exited HOLD from static findings; RUNTIME advisory had zero effect on verdict.

## Summary

total: 5
passed: 5
issues: 0
blocked: 0
pending: 0
skipped: 0

## Gaps

[none]

## Bugs Found and Fixed During UAT

ce4242f  fix/runtime: _parse_llm_response list-wrapped response (mimo-pro format)
1217d39  fix/cli: RuntimeRunner not inheriting --backend from review command
d895ce3  fix/runtime: hyphen/underscore/space equivalence in surface name matching

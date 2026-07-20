# Phase 32: Per-Change Intent Contract - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-28
**Phase:** 32-per-change-intent-contract
**Areas discussed:** File format, contracts.yaml interaction, confirmation bias protection, verification strategy, security guards, error handling, CLI experience, test boundaries, injection scope, summarization/caching, user guidance, summarization implementation, test organization, template installation

---

## File Format (D-32-01)

| Option | Description | Selected |
|--------|-------------|----------|
| Plain text/Markdown | Direct read_text() injection, zero parse overhead | |
| YAML structured | Reuse contracts.yaml schema via contract_loader.py | |
| Claude decides | Based on codebase and UX analysis | x |

**User's choice:** Claude decides
**Claude's decision:** Plain text/Markdown -- contract_spec slot is raw string injection (cli.py:614-617), no parsing needed.

---

## Interaction with contracts.yaml (D-32-02)

| Option | Description | Selected |
|--------|-------------|----------|
| Merge (concatenate) | Both inject into prompt; long-term + short-term complement | x |
| --contract overrides | Ignore contracts.yaml when --contract present | |
| Claude decides | Scenario analysis | |

**User's choice:** Merge (concatenate)

---

## Confirmation Bias Protection (D-32-03)

| Option | Description | Selected |
|--------|-------------|----------|
| Prompt injection warning | Fixed directive appended to contract block | |
| Input validation (reject keywords) | Scan for safe/correct/verified, warn or reject | |
| Both layers | Prompt warning + input scan | |
| Claude decides | Based on complexity and ROI | x |

**User's choice:** Claude decides
**Claude's decision:** Prompt injection only -- arXiv 2603.18740 is a prompt framing issue; keyword scanning has high FP rate.

---

## Verification Strategy (D-32-04)

| Option | Description | Selected |
|--------|-------------|----------|
| Automated tests only | Mock LLM, verify prompt injection | |
| Real backend smoke test only | End-to-end with CN backend | |
| Both | Automated + real backend | x |
| Claude decides | Engineering practice analysis | |

**User's choice:** Both

---

## Security Guards (D-32-05)

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse existing limits | Size + binary check, no path traversal (CLI arg) | x |
| Full protection | Size + binary + path traversal (restrict to cwd) | |
| Claude decides | Threat model analysis | |

**User's choice:** Reuse existing limits

---

## Error Handling (D-32-06)

| Option | Description | Selected |
|--------|-------------|----------|
| Hard fail (exit 1) | CliError on any file issue; fail-closed | x |
| Warn and continue | stderr warning, review without contract | |
| Claude decides | Based on forge error patterns | |

**User's choice:** Hard fail (exit 1)

---

## CLI Experience (D-32-07)

| Option | Description | Selected |
|--------|-------------|----------|
| File path only | --contract path/to/file.md | |
| File + stdin | --contract FILE or --contract - | x |
| Claude decides | CLI best practices | |

**User's choice:** File + stdin

---

## Test Boundaries (D-32-08)

| Option | Description | Selected |
|--------|-------------|----------|
| Full coverage | All boundaries: empty, oversized, binary, missing, stdin, merge, bias directive | x |
| Core paths only | Normal + file-not-found + merge | |
| Claude decides | Based on forge test patterns | |

**User's choice:** Full coverage

---

## Injection Scope (D-32-09)

| Option | Description | Selected |
|--------|-------------|----------|
| Both Outlets | Outlet B + C via existing contract_spec param | x |
| Outlet C only | Subagent path only | |
| Claude decides | Based on code architecture | |

**User's choice:** Both Outlets

---

## Summarization/Caching (D-32-10)

| Option | Description | Selected |
|--------|-------------|----------|
| Direct injection, no summarize | Per-change is short, inject raw | |
| Large file LLM summarize | >4KB gets summarized, no cache | x |
| Claude decides | Scenario analysis | |

**User's choice:** Large file LLM summarize

---

## User Guidance (D-32-11)

| Option | Description | Selected |
|--------|-------------|----------|
| Docs + help text | Guidance in --contract help and README | |
| Template file | .code-forge/contract-template.md via init | |
| Both | Help text guidance + template file | x |

**User's choice:** Both (1+2)

---

## Summarization Implementation (D-32-12)

| Option | Description | Selected |
|--------|-------------|----------|
| Extract public function | Make _summarize_spec() public, reuse | |
| Inline simplified version | Inline summarizer in cli.py, don't touch contract_loader | x |
| Claude decides | Code structure analysis | |

**User's choice:** Inline simplified version

---

## Test Organization (D-32-13)

| Option | Description | Selected |
|--------|-------------|----------|
| New test_contract_flag.py | Dedicated file for --contract tests | x |
| Merge into test_cli.py | Add TestContractFlag class to existing | |
| Claude decides | Based on forge test structure | |

**User's choice:** New test_contract_flag.py

---

## Template Installation (D-32-14)

| Option | Description | Selected |
|--------|-------------|----------|
| code-forge init generates | Template created during init in .code-forge/ | x |
| Documentation reference only | No file, just README instructions | |
| Claude decides | Based on forge init flow | |

**User's choice:** code-forge init generates

---

## Claude's Discretion

- **D-32-01** (file format): plain text/Markdown -- contract_spec is raw string
- **D-32-03** (bias protection): prompt-only -- arXiv finding is prompt framing
- **D-32-05** (security sizing): 64KB -- per-change docs are short-lived

## Deferred Ideas

None -- discussion stayed within phase scope.

# humantest -- customer-machine E2E scripts

Self-contained scripts a customer (or any human tester) runs on their own
machine to validate forge end to end. No file copy from the dev box, no
API keys: each script clones the public repo, installs into a throwaway
virtualenv, runs the unit suite, smokes the CLI, then exercises a real
`code-forge review` against a mock LLM backend served from localhost.

## Scripts

| Script | Platform | Shell |
|--------|----------|-------|
| `forge_mac_e2e.sh` | macOS (also runs on Linux) | stock bash 3.2+ |
| `forge_win_e2e.ps1` | Windows 10/11 | Windows PowerShell 5.1 or PowerShell 7 |

## What each script does

1. **S0** environment report (OS, locale/codepage, git version).
2. **S1** prerequisites: git + Python 3.12+ (prints the install hint if missing).
3. **S2** `git clone` of the repo (override with `FORGE_REPO_URL` / `FORGE_BRANCH`).
4. **S3** fresh virtualenv; `pip install -e .[mcp,vertex]` plus test deps
   (`pytest pytest-asyncio jsonschema code-review-graph`).
5. **S4** full unit suite. If the `claude` CLI binary is absent, the 43
   tests that shell out to it are deselected (environmental, not code).
6. **S5** CLI smoke in a fresh demo repo: `init` (PASS/FAIL), `doctor` and
   `detect` (informational).
7. **S6** mock-file diagnosis, clean scenario: a mock Anthropic-format LLM
   server returns zero findings; expect verdict PASS (exit 0).
8. **S7** mock-file diagnosis, finding scenario: the mock returns one P2
   finding. CI semantics: L1 (LLM) findings enter as UNCERTAIN awaiting
   human disposition; the CI verdict fails only on CONFIRMED findings or
   coverage gaps. Expect exit 0 WITH the finding in the report (source
   L1, uncertain=1).
9. **S7b** deterministic FAIL path: an undefined-name defect is appended;
   every installed linter confirms it (pylint E0602, ruff/flake8 F821),
   no LLM involved. Expect verdict FAIL (exit 1) with confirmed >= 1.
10. **S8** optional real-backend diagnosis, gated on FORGE_REAL_API_KEY
    being exported (shared out-of-band, never written to a file). Skipped
    with an INFO record when no key is set. The tree still carries the
    S7b defect, so L0 drives an expected exit 1; the assertion accepts
    any verdict exit (0 or 1) and fails only on infra errors (2+).

Everything lives under a `mktemp`-style temp directory; nothing outside it
is touched. The customer sends back exactly one report file whose path is
printed at the end.

## Handing to a customer

    # macOS
    curl -LO https://raw.githubusercontent.com/HouMinXi/forge/<branch>/humantest/forge_mac_e2e.sh
    bash forge_mac_e2e.sh

    # Windows (PowerShell)
    Invoke-WebRequest -OutFile forge_win_e2e.ps1 https://raw.githubusercontent.com/HouMinXi/forge/<branch>/humantest/forge_win_e2e.ps1
    powershell -ExecutionPolicy Bypass -File forge_win_e2e.ps1

To test an unmerged branch, set `FORGE_BRANCH` before running.

## Expected numbers (main branch)

- full environment (claude CLI present): `2738 passed, 8 skipped`
- without claude CLI: `2695 passed, 8 skipped, 43 deselected`

These move as tests are added; treat OVERALL PASS/FAIL as the signal and
the numbers as reference points.

## Verification status

- `forge_mac_e2e.sh`: shellcheck-clean. First genuine macOS run 2026-07-12:
  OVERALL FAIL (4 root causes, all fixed by mac-wave1). Second genuine macOS
  run 2026-07-13: OVERALL FAIL again, root cause this time was the E2E
  script itself, not forge -- see below. Linux py3.12: 2695 passed / 8
  skipped / 43 deselected (2738 / 8 with claude on PATH). Linux py3.13:
  2695 passed / 8 skipped / 43 deselected. Mac numbers: pending customer
  re-run.
- Earlier "validated by a full end-to-end run on Linux, OVERALL PASS" was
  run on a dev machine that already had ruff/pylint/flake8/pytest installed
  globally, so it never actually exercised this script's own PATH handling.
  The script invokes the venv's binaries by absolute path but never puts
  the venv's bin directory on PATH, so any code that resolves a bare
  command name via PATH (forge's own toolchain auto-detect, and a unit
  test's simulated pre-commit hook shelling out to "python3") silently
  falls through to whatever the OS ships. On a genuinely clean machine
  that finds nothing, producing the exact "No toolchain detected" /
  "No module named pytest" failures the customer saw. Fixed by exporting
  `PATH="$VENV/bin:$PATH"` once, right after S3. Reproduced and verified
  with the venv's bin directory deliberately excluded from PATH (matching
  a clean machine), both before the fix (fails identically to the customer
  report) and after (S4 and S6 pass).
- Second S7 root cause, found while verifying the PATH fix above: S6
  running before S7 in the same demo directory left behind a
  `.code-forge/mutation-result.json` with `status: "error"` (mutmut is
  an optional dev dependency this script never installs), which forge's
  CI mode read back on the *next* review and used to force that next
  verdict to FAIL regardless of its own findings -- and never deleted,
  so it kept poisoning every review after it. First suspected the mock
  response's missing "surfaces" key (a real but separate RUNTIME-axis
  mock-fidelity gap); a controlled re-test with that key added still
  failed under a genuinely stripped PATH, which disproved that theory
  and led to the actual file. Fixed in forge itself (not a script
  workaround): a crashed/skipped mutation run is now recorded as a
  DISMISSED finding on the same run, and the result file is always
  consumed once read, so it can no longer affect a later, unrelated
  review. Reproduced and verified both ways: before the fix, S6 exit 0
  then S7 exit 1 with `infra_errors` containing "mutation error: mutmut
  not installed"; after the fix, S6 exit 0 then S7 exit 0 with no
  leftover mutation-result.json, confirmed under a genuinely stripped
  PATH matching a clean machine (not just this dev box, which already
  has mutmut installed and would have masked the bug).
- `forge_win_e2e.ps1`: written to mirror the mac flow; PowerShell parser
  not available on the authoring box, so first verification happens on a
  real Windows machine. Run it on an internal Windows box before handing
  to a customer.

## Notes

- The mock server (embedded in each script) speaks the Anthropic messages
  format and returns a fixed reviewer JSON, so the diagnosis path
  (backend -> parse -> falsify -> verdict) is exercised deterministically
  with zero cost and zero secrets.
- `--allow-main` is passed to `code-forge review` because the demo repo is
  a throwaway main tree; forge otherwise enforces worktree discipline.
- `code-forge trust` runs after every gate.yaml write: repo-supplied
  backends are ignored until the gate.yaml hash is trusted, and the hash
  changes whenever the mock port changes.
- The Windows script deliberately does NOT set `PYTHONUTF8`: passing under
  the machine's real locale codepage is part of what it validates.

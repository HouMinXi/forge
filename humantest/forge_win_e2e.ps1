# forge Windows customer E2E test
# ================================
# Full-flow validation on a customer Windows machine, no file copy and
# no API keys needed:
#   clone -> install -> unit tests -> CLI smoke -> mock-file code diagnosis
#
# Usage (customer runs exactly one command in PowerShell):
#   powershell -ExecutionPolicy Bypass -File forge_win_e2e.ps1
#
# Optional overrides (set before running):
#   $env:FORGE_REPO_URL = "https://github.com/HouMinXi/forge.git"
#   $env:FORGE_BRANCH   = "main"
#
# Prerequisites on the Windows machine:
#   - git (winget install Git.Git)
#   - Python 3.12+ (winget install Python.Python.3.13)
#   - network access to github.com and pypi.org
#
# Everything runs inside a fresh temp directory. Nothing outside it is
# touched. When done, send back the single report file whose path is
# printed at the end.
#
# Deliberately does NOT set PYTHONUTF8: on CJK-locale Windows the suite
# must pass under the real locale codepage (GBK etc.) -- that is part
# of what this test validates.
#
# Compatible with Windows PowerShell 5.1 (stock) and PowerShell 7.

$ErrorActionPreference = "Continue"

$RepoUrl = if ($env:FORGE_REPO_URL) { $env:FORGE_REPO_URL } else { "https://github.com/HouMinXi/forge.git" }
$Branch  = if ($env:FORGE_BRANCH)   { $env:FORGE_BRANCH }   else { "main" }

$WorkDir = Join-Path $env:TEMP ("forge-e2e-" + [System.IO.Path]::GetRandomFileName().Replace(".", ""))
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$Report  = Join-Path $WorkDir "forge_win_e2e_report.txt"
$Src     = Join-Path $WorkDir "forge"
$Demo    = Join-Path $WorkDir "mock-demo"
$Venv    = Join-Path $WorkDir "venv"
$MockProcs = @()

Start-Transcript -Path $Report | Out-Null

$StepNames   = @()
$StepResults = @()
$script:Failed = 0

function Record($result, $name) {
    $script:StepResults += $result
    $script:StepNames   += $name
    if ($result -eq "FAIL") { $script:Failed = 1 }
    Write-Host ""
    Write-Host "==> [$result] $name"
    Write-Host ""
}

function Banner($title) {
    Write-Host ""
    Write-Host "======================================================="
    Write-Host "  $title"
    Write-Host "======================================================="
}

function Finish {
    foreach ($p in $script:MockProcs) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
    Banner "SUMMARY"
    for ($i = 0; $i -lt $script:StepNames.Count; $i++) {
        Write-Host ("  {0,-4} {1}" -f $script:StepResults[$i], $script:StepNames[$i])
    }
    Write-Host ""
    if ($script:Failed -eq 0) { Write-Host "OVERALL: PASS" } else { Write-Host "OVERALL: FAIL (see steps above)" }
    Write-Host ""
    Write-Host "Please send back this single file:"
    Write-Host "  $Report"
    Write-Host "(supporting logs are in $WorkDir)"
    Stop-Transcript | Out-Null
    exit $script:Failed
}

# ---------------------------------------------------------------------
Banner "S0: environment report"
Write-Host ("windows: " + [System.Environment]::OSVersion.VersionString)
Write-Host ("powershell: " + $PSVersionTable.PSVersion)
Write-Host ("codepage: " + [System.Text.Encoding]::Default.WebName + " / chcp " + (chcp 2>$null))
git --version 2>&1 | Write-Host
Write-Host "workdir: $WorkDir"
Record "INFO" "S0 environment report"

# ---------------------------------------------------------------------
Banner "S1: prerequisites (git + Python >= 3.12)"
$PyBin = $null
$candidates = @(
    @("py", "-3.14"), @("py", "-3.13"), @("py", "-3.12"),
    @("python", $null), @("python3", $null)
)
foreach ($cand in $candidates) {
    $exe = $cand[0]; $flag = $cand[1]
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        if ($flag) { & $exe $flag -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null }
        else       { & $exe       -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null }
        if ($LASTEXITCODE -eq 0) {
            if ($flag) { $PyBin = @($exe, $flag) } else { $PyBin = @($exe) }
            break
        }
    }
}
$GitOk = [bool](Get-Command git -ErrorAction SilentlyContinue)
if (-not $PyBin -or -not $GitOk) {
    Write-Host "MISSING PREREQUISITE:"
    Write-Host ("  git found:      " + $GitOk)
    Write-Host ("  python >= 3.12: " + $(if ($PyBin) { "yes" } else { "NOT FOUND (try: winget install Python.Python.3.13)" }))
    Record "FAIL" "S1 prerequisites"
    Finish
}
$pyver = & $PyBin[0] $PyBin[1..($PyBin.Count-1)] --version 2>&1
Write-Host "using python: $($PyBin -join ' ') ($pyver)"
Record "PASS" "S1 prerequisites"

# ---------------------------------------------------------------------
Banner "S2: git clone $RepoUrl ($Branch)"
git clone --branch $Branch $RepoUrl $Src
if ($LASTEXITCODE -eq 0) {
    Push-Location $Src; git log --oneline -1; Pop-Location
    Record "PASS" "S2 clone"
} else {
    Record "FAIL" "S2 clone"
    Finish
}

# ---------------------------------------------------------------------
Banner "S3: virtualenv + install (forge + test dependencies)"
# pytest-asyncio: the mcp/lock/sampling tests are async (@pytest.mark.asyncio).
# code-review-graph: pulls tree-sitter-language-pack; without it the
#   cross-repo-impact and dead-code tests fail instead of skipping.
& $PyBin[0] $PyBin[1..($PyBin.Count-1)] -m venv $Venv
$VPy = Join-Path $Venv "Scripts\python.exe"
$VForge = Join-Path $Venv "Scripts\code-forge.exe"
$installOk = $false
if (Test-Path $VPy) {
    & $VPy -m pip install --quiet --upgrade pip
    Push-Location $Src
    & $VPy -m pip install --quiet -e ".[mcp,vertex]"
    $rc1 = $LASTEXITCODE
    Pop-Location
    & $VPy -m pip install --quiet pytest pytest-asyncio jsonschema code-review-graph
    if ($rc1 -eq 0 -and $LASTEXITCODE -eq 0) { $installOk = $true }
}
if ($installOk) {
    & $VForge --version
    Record "PASS" "S3 install"
} else {
    Record "FAIL" "S3 install"
    Finish
}

# ---------------------------------------------------------------------
Banner "S4: unit test suite (takes ~5-10 minutes)"
# 21 tests in these two classes invoke the external 'claude' CLI binary.
# On machines without it they fail for environmental (not code) reasons,
# so they are deselected when the binary is absent.
#   with claude on PATH:    expect 2734 passed, 8 skipped (main branch)
#   without claude on PATH: expect 2713 passed, 8 skipped
$PytestArgs = @()
if (Get-Command claude -ErrorAction SilentlyContinue) {
    Write-Host "claude CLI found on PATH -- running the FULL suite"
} else {
    Write-Host "claude CLI not found -- deselecting the 21 claude-CLI-dependent tests"
    $PytestArgs = @(
        "--deselect", "tests/test_llm_invoke.py::TestLLMInvoke",
        "--deselect", "tests/test_llm_invoke.py::TestSubprocessCleanup"
    )
}
Push-Location $Src
& $VPy -B -m pytest tests/ -q @PytestArgs
$rc = $LASTEXITCODE
Pop-Location
if ($rc -eq 0) { Record "PASS" "S4 unit tests" } else { Record "FAIL" "S4 unit tests" }

# ---------------------------------------------------------------------
Banner "S5: CLI smoke in a fresh demo repo (init / doctor / detect)"
New-Item -ItemType Directory -Path $Demo | Out-Null
Set-Location $Demo
git init -q .
# Docstrings matter: forge's L0 layer runs any system linters found by
# 'detect' (pylint flags missing docstrings as confirmed findings).
@'
"""Mock target for forge e2e diagnosis."""


def greet(name):
    """Return a greeting for name."""
    return "hello " + name
'@ | Set-Content -Encoding UTF8 mock_target.py
# Ignore forge's own artifacts: the coverage axis enumerates untracked
# files via 'git ls-files --others --exclude-standard', so without this
# every .code-forge/ file (gate.yaml, state.json, receipts) is flagged
# "no review layer examined this file" and CI fails on coverage gaps.
".code-forge/" | Set-Content -Encoding ascii .gitignore
git add mock_target.py .gitignore
git -c user.name="forge-e2e" -c user.email="forge-e2e@example.com" commit -q -m "seed mock target"

& $VForge init
if ($LASTEXITCODE -eq 0) { Record "PASS" "S5a code-forge init" } else { Record "FAIL" "S5a code-forge init" }

& $VForge doctor
Record "INFO" "S5b code-forge doctor (informational; non-zero expected without a real backend)"

& $VForge detect
Record "INFO" "S5c code-forge detect (informational)"

# ---------------------------------------------------------------------
Banner "S6: mock file -> code diagnosis (mock LLM backend, CLEAN scenario)"
# Append a new function to the committed file: this working-tree change
# is the diff that forge reviews.
Add-Content -Encoding UTF8 mock_target.py @'


def divide(a, b):
    """Return a divided by b."""
    return a / b
'@

# Local mock LLM server speaking the Anthropic messages format.
@'
"""Minimal mock of an Anthropic-format LLM endpoint for forge e2e."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

resp_file, port = sys.argv[1], int(sys.argv[2])
with open(resp_file, encoding="utf-8") as f:
    reviewer_json = f.read()
body = json.dumps({
    "content": [{"type": "text", "text": reviewer_json}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 10},
}).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _reply
    do_GET = _reply

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'@ | Set-Content -Encoding UTF8 (Join-Path $WorkDir "mock_server.py")

function Get-FreePort {
    & $VPy -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

function Write-Gate($port) {
    New-Item -ItemType Directory -Force -Path ".code-forge" | Out-Null
    @"
outlet: subprocess
backends:
  mock-local:
    type: api
    format: anthropic
    base_url: http://127.0.0.1:$port
    api_key_env: FORGE_MOCK_KEY
    model: mock-1
    max_tokens: 16384
    default: true
"@ | Set-Content -Encoding UTF8 ".code-forge\gate.yaml"
    # Repo-supplied backends are untrusted until 'code-forge trust' records
    # the gate.yaml hash; re-trust after every gate.yaml rewrite.
    & $VForge trust
}

$env:FORGE_MOCK_KEY = "mock-key-for-e2e"

# Scenario 1: reviewer returns zero findings -> verdict PASS (exit 0).
@'
{"findings": [], "code_excerpts": [{"file": "mock_target.py", "start_line": 9, "end_line": 11, "content": "def divide(a, b):\n    \"\"\"Return a divided by b.\"\"\"\n    return a / b"}]}
'@ | Set-Content -Encoding UTF8 (Join-Path $WorkDir "reviewer_clean.json")

$Port1 = Get-FreePort
$p = Start-Process -FilePath $VPy -ArgumentList @((Join-Path $WorkDir "mock_server.py"), (Join-Path $WorkDir "reviewer_clean.json"), $Port1) -PassThru -WindowStyle Hidden
$MockProcs += $p
Start-Sleep -Seconds 2
Write-Gate $Port1

# --allow-main: forge refuses main-tree reviews by default (worktree
# discipline); the demo repo is a throwaway main tree, so opt out.
# NOTE: no --falsification-engine stub here. The stub engine disables the
# whole L1 semantic layer (l1_active = engine != "stub"), which would mean
# the mock backend is never consulted -- the exact path this step tests.
& $VForge review --mode ci --allow-main --backend mock-local *> (Join-Path $WorkDir "diag_clean.log")
$rc = $LASTEXITCODE
if ($rc -eq 0) {
    Write-Host "clean scenario: exit 0 (PASS verdict) as expected"
    Get-Content (Join-Path $WorkDir "diag_clean.log") -Tail 5
    Record "PASS" "S6 mock diagnosis: clean change -> PASS verdict"
} else {
    Write-Host "clean scenario: unexpected exit $rc; last 40 lines:"
    Get-Content (Join-Path $WorkDir "diag_clean.log") -Tail 40
    Record "FAIL" "S6 mock diagnosis: clean change -> PASS verdict"
}

# ---------------------------------------------------------------------
Banner "S7: mock file -> code diagnosis (L1 finding surfaced as UNCERTAIN)"
# Scenario 2: the mock reviewer reports one P2 finding. CI semantics:
# L1 (LLM) findings enter as UNCERTAIN and are left for human
# disposition -- the CI verdict fails only on CONFIRMED findings
# (deterministic L0 linters / infra errors) or coverage gaps
# (machine.py: verdict = FAIL if confirmed > 0 or coverage_gaps > 0).
# So the expectation here is exit 0 WITH the finding present in the
# report: source L1, disposition UNCERTAIN.
@'
{"findings": [{"file": "mock_target.py", "line": 11, "severity": "P2", "description": "divide() lacks a zero-divisor guard: b == 0 raises ZeroDivisionError to the caller"}], "code_excerpts": [{"file": "mock_target.py", "start_line": 9, "end_line": 11, "content": "def divide(a, b):\n    \"\"\"Return a divided by b.\"\"\"\n    return a / b"}]}
'@ | Set-Content -Encoding UTF8 (Join-Path $WorkDir "reviewer_finding.json")

$Port2 = Get-FreePort
$p = Start-Process -FilePath $VPy -ArgumentList @((Join-Path $WorkDir "mock_server.py"), (Join-Path $WorkDir "reviewer_finding.json"), $Port2) -PassThru -WindowStyle Hidden
$MockProcs += $p
Start-Sleep -Seconds 2
Write-Gate $Port2

& $VForge review --mode ci --allow-main --backend mock-local *> (Join-Path $WorkDir "diag_finding.log")
$rc = $LASTEXITCODE
$found = Select-String -Path (Join-Path $WorkDir "diag_finding.log") -Pattern "ZeroDivisionError" -Quiet
$isL1 = Select-String -Path (Join-Path $WorkDir "diag_finding.log") -Pattern '"source": "L1"' -SimpleMatch -Quiet
$isUncertain = Select-String -Path (Join-Path $WorkDir "diag_finding.log") -Pattern "uncertain=1" -Quiet
if ($rc -eq 0 -and $found -and $isL1 -and $isUncertain) {
    Write-Host "finding scenario: exit 0 and the L1 finding is in the report as UNCERTAIN:"
    Select-String -Path (Join-Path $WorkDir "diag_finding.log") -Pattern "ZeroDivisionError" | Select-Object -First 3
    Record "PASS" "S7 mock diagnosis: L1 finding surfaced (UNCERTAIN, human disposition)"
} else {
    Write-Host "finding scenario: exit $rc (expected 0 + L1 finding in report); last 40 lines:"
    Get-Content (Join-Path $WorkDir "diag_finding.log") -Tail 40
    Record "FAIL" "S7 mock diagnosis: L1 finding surfaced (UNCERTAIN, human disposition)"
}

# ---------------------------------------------------------------------
Banner "S7b: deterministic L0 defect -> CONFIRMED -> FAIL verdict (exit 1)"
# The FAIL path is owned by the deterministic L0 layer: an undefined
# name is confirmed by every installed linter (pylint E0602, ruff and
# flake8 F821) with no LLM involved, so CI must exit 1.
Add-Content -Encoding UTF8 mock_target.py @'


def use_undefined():
    """Trigger a deterministic L0 linter finding."""
    return undefined_name
'@

$Port3 = Get-FreePort
$p = Start-Process -FilePath $VPy -ArgumentList @((Join-Path $WorkDir "mock_server.py"), (Join-Path $WorkDir "reviewer_clean.json"), $Port3) -PassThru -WindowStyle Hidden
$MockProcs += $p
Start-Sleep -Seconds 2
Write-Gate $Port3

& $VForge review --mode ci --allow-main --backend mock-local *> (Join-Path $WorkDir "diag_l0.log")
$rc = $LASTEXITCODE
$hasUndef = Select-String -Path (Join-Path $WorkDir "diag_l0.log") -Pattern "undefined" -Quiet
$hasConfirmed = Select-String -Path (Join-Path $WorkDir "diag_l0.log") -Pattern "confirmed=[1-9]" -Quiet
if ($rc -eq 1 -and $hasUndef -and $hasConfirmed) {
    Write-Host "L0 scenario: exit 1 (FAIL verdict) with confirmed linter finding(s):"
    Select-String -Path (Join-Path $WorkDir "diag_l0.log") -Pattern "FAIL findings=" | Select-Object -First 1
    Record "PASS" "S7b L0 defect: undefined name -> CONFIRMED -> FAIL verdict"
} else {
    Write-Host "L0 scenario: exit $rc (expected 1); last 40 lines:"
    Get-Content (Join-Path $WorkDir "diag_l0.log") -Tail 40
    Record "FAIL" "S7b L0 defect: undefined name -> CONFIRMED -> FAIL verdict"
}

# ---------------------------------------------------------------------
Banner "S8: OPTIONAL real-backend diagnosis (runs only if a key is provided)"
# To enable, the tester sets a key (shared out-of-band, never stored in
# any file) before running:
#   $env:FORGE_REAL_API_KEY = "sk-..."
# Optional overrides (defaults target Xiaomi MiMo UltraSpeed):
#   $env:FORGE_REAL_BASE_URL = "https://api.xiaomimimo.com/anthropic"
#   $env:FORGE_REAL_MODEL    = "mimo-v2.5-pro-ultraspeed"
#   $env:FORGE_REAL_FORMAT   = "anthropic"
# The working tree still carries the S7b undefined-name defect, so the
# expected verdict is FAIL (exit 1) driven by the deterministic L0 layer;
# what the real model adds on top is not deterministic. The assertion is
# therefore mechanical: exit code must be a verdict (0 PASS / 1 FAIL),
# not an infra error (2+). Transient 429s are retried by forge itself.
if ($env:FORGE_REAL_API_KEY) {
    $RealUrl    = if ($env:FORGE_REAL_BASE_URL) { $env:FORGE_REAL_BASE_URL } else { "https://api.xiaomimimo.com/anthropic" }
    $RealModel  = if ($env:FORGE_REAL_MODEL)    { $env:FORGE_REAL_MODEL }    else { "mimo-v2.5-pro-ultraspeed" }
    $RealFormat = if ($env:FORGE_REAL_FORMAT)   { $env:FORGE_REAL_FORMAT }   else { "anthropic" }
    Write-Host "real backend: $RealUrl ($RealModel, $RealFormat)"
    New-Item -ItemType Directory -Force -Path ".code-forge" | Out-Null
    @"
outlet: subprocess
backends:
  real-remote:
    type: api
    format: $RealFormat
    base_url: $RealUrl
    api_key_env: FORGE_REAL_API_KEY
    model: $RealModel
    max_tokens: 16384
    default: true
"@ | Set-Content -Encoding UTF8 ".code-forge\gate.yaml"
    & $VForge trust
    & $VForge review --mode ci --allow-main --backend real-remote *> (Join-Path $WorkDir "diag_real.log")
    $rc = $LASTEXITCODE
    Write-Host "real-backend review exit: $rc; last 10 lines:"
    Get-Content (Join-Path $WorkDir "diag_real.log") -Tail 10
    if ($rc -le 1) {
        Record "PASS" "S8 real-backend diagnosis reached a verdict (exit $rc)"
    } else {
        Record "FAIL" "S8 real-backend diagnosis infra error (exit $rc)"
    }
} else {
    Write-Host "FORGE_REAL_API_KEY not set -- skipping real-backend scenario"
    Record "INFO" "S8 real-backend diagnosis skipped (no key provided)"
}

# ---------------------------------------------------------------------
Finish

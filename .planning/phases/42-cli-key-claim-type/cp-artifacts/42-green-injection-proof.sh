#!/bin/bash
# Phase 42 GREEN exit verifier -- E10 / E13 bug-injection proofs.
# Run BY THE PM, in a detached verification worktree, against the merged code.
# Each injection is applied, the targeted tests are run, the failure signature
# is captured, and the file is reverted before the next injection.
#
# Usage:  bash 42-green-injection-proof.sh <worktree-path>
#
# E10 requires four DISTINCT signatures on the claim_type wiring.
# E13 requires the two new F8 guard branches to be injected SEPARATELY
# (Golden Rule 2 is per-site).
set -u

WT="${1:?usage: $0 <worktree-path>}"
cd "$WT" || exit 1

M=src/code_forge/machine.py
C=src/code_forge/cli.py
BEH="tests/test_machine_ledger.py::test_write_ledger_derives_claim_type_from_source"
T13="tests/test_claim_type.py::test_machine_py_wiring_derive_claim_type"
FF=tests/test_fast_fail.py

# Print the one-line verdict plus the exact failing assertion for a target.
run() {
    PYTHONPATH=src python -m pytest "$@" -q --no-header \
        -p no:cacheprovider 2>&1
}

verdict() {   # verdict <label> <pytest-output>
    local out="$2"
    if printf '%s' "$out" | /usr/bin/grep -q "^E \+assert\|FAILED\|failed"; then
        printf '  %-28s RED   %s\n' "$1" \
            "$(printf '%s' "$out" | /usr/bin/grep -m1 '^E ' | cut -c1-88)"
    else
        printf '  %-28s GREEN\n' "$1"
    fi
}

revert() { git checkout -- "$1"; }

banner() { echo; echo "=== $* ==="; }

# --- guard: the tree must be clean before we start, or a leftover
# --- injection from an aborted run would silently corrupt every result.
if [ -n "$(git status --porcelain -- "$M" "$C")" ]; then
    echo "ABORT: $M or $C already modified; refusing to inject." >&2
    exit 1
fi

banner "BASELINE (no injection) -- all targets must be GREEN"
verdict "behavioural" "$(run "$BEH")"
verdict "test13-sourcetext" "$(run "$T13")"
verdict "fast-fail(all)" "$(run "$FF")"

banner "INJ1  re-hardcode output: axis_claim=ct.type -> \"review\""
/usr/bin/sed -i 's/axis_claim=ct\.type,/axis_claim="review",/' "$M"
verdict "behavioural" "$(run "$BEH")"
verdict "test13-sourcetext" "$(run "$T13")"
revert "$M"

banner "INJ2  hardcode argument: derive_claim_type(f.source) -> (\"L1\")"
/usr/bin/sed -i 's/derive_claim_type(f\.source)/derive_claim_type("L1")/' "$M"
verdict "behavioural" "$(run "$BEH")"
verdict "test13-sourcetext" "$(run "$T13")"
revert "$M"

banner "INJ3  drop the version_sensitive write"
/usr/bin/sed -i '/version_sensitive=ct\.version_sensitive,/d' "$M"
verdict "behavioural" "$(run "$BEH")"
verdict "test13-sourcetext" "$(run "$T13")"
revert "$M"

banner "INJ4  MIRROR: derive_claim_type(f.source) -> (\"L0\")"
/usr/bin/sed -i 's/derive_claim_type(f\.source)/derive_claim_type("L0")/' "$M"
verdict "behavioural" "$(run "$BEH")"
verdict "test13-sourcetext" "$(run "$T13")"
revert "$M"

banner "INJ5  F8 site A: disable the api_key_file branch only"
/usr/bin/sed -i 's/^    elif backend\.api_key_file:/    elif False and backend.api_key_file:/' "$C"
verdict "fastfail -k api_key_file" "$(run "$FF" -k "file")"
verdict "fastfail -k vertex" "$(run "$FF" -k "credentials_path")"
revert "$C"

banner "INJ6  F8 site B: disable the vertex credentials_path branch only"
/usr/bin/sed -i 's/^    if backend\.format == "vertex" and backend\.credentials_path:/    if False and backend.format == "vertex" and backend.credentials_path:/' "$C"
verdict "fastfail -k api_key_file" "$(run "$FF" -k "file")"
verdict "fastfail -k vertex" "$(run "$FF" -k "credentials_path")"
revert "$C"

banner "FINAL: tree restored?"
git status --porcelain -- "$M" "$C" | /usr/bin/grep . && echo "DIRTY -- investigate" \
    || echo "clean"

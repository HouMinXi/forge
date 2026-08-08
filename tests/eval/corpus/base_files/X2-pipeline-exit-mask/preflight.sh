#!/bin/bash
# Pre-commit preflight. Every check must print PASS or FAIL and set rc.
set -u

rc=0
CHANGED=$(git diff --cached --name-only --diff-filter=AM)

check_trailing_ws() {
    if git diff --cached --check >/dev/null 2>&1; then
        echo "PASS trailing-whitespace"
    else
        echo "FAIL trailing-whitespace"; rc=1
    fi
}

check_shebang() {
    local missing=0
    for f in $CHANGED; do
        case "$f" in
            *.sh) head -1 "$f" | grep -q '^#!' || missing=1 ;;
        esac
    done
    if [ "$missing" -eq 0 ]; then
        echo "PASS shebang"
    else
        echo "FAIL shebang"; rc=1
    fi
}

check_trailing_ws
check_shebang
exit "$rc"

#!/usr/bin/env bash
set -euo pipefail

FORGE_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="${HOME}/.claude/skills"

SKILLS=(forge qodo-review code-review-expert adversarial-qe kernel-fp-verify smoke-test)

echo "Forge installer"
echo "  Source: ${FORGE_DIR}/skills/"
echo "  Target: ${SKILLS_DIR}/"
echo ""

mkdir -p "${SKILLS_DIR}"

for skill in "${SKILLS[@]}"; do
    src="${FORGE_DIR}/skills/${skill}"
    dst="${SKILLS_DIR}/${skill}"

    if [ ! -d "${src}" ]; then
        echo "  SKIP  ${skill} (source not found)"
        continue
    fi

    if [ -L "${dst}" ]; then
        existing=$(readlink -f "${dst}" 2>/dev/null || echo "?")
        if [ "${existing}" = "${src}" ]; then
            echo "  OK    ${skill} (already linked)"
            continue
        fi
        rm "${dst}"
        echo "  UPDATE ${skill} (relinked)"
    elif [ -d "${dst}" ]; then
        echo "  WARN  ${skill} exists as directory, skipping"
        echo "        Remove ${dst} manually to install"
        continue
    fi

    ln -s "${src}" "${dst}"
    echo "  LINK  ${skill}"
done

echo ""
echo "Skills installed. To set up hooks:"
echo "  1. Copy hooks/ scripts to ~/.claude/hooks/"
echo "  2. Add settings-snippet.json entries to ~/.claude/settings.json"
echo "  See hooks/README.md for details."

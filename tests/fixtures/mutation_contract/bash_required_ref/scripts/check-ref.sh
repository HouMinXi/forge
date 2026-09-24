#!/bin/bash
# Require a nonempty ref argument. A missing guard would accept empty.
set -u
ref="${1-}"
if [ -z "$ref" ]; then
    echo "empty ref" >&2
    exit 2
fi
echo "ok $ref"
exit 0

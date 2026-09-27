#!/bin/bash
# Config path must stay under /etc/app. Dropping the prefix check
# would accept a path outside that tree.
set -u
path="${1-}"
case "$path" in
    /etc/app/*) ;;
    *)
        echo "path outside /etc/app" >&2
        exit 3
        ;;
esac
echo "ok $path"
exit 0

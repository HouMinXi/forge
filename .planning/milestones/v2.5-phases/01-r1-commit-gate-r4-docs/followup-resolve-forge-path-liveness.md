# Follow-up: resolve_forge_path liveness check

**Found by:** host EC-8 verification (2026-05-25)
**Severity:** LOW (existing bug, not introduced by Phase 1)
**Blocking:** No

## Problem

`resolve_forge_path()` in `install_hooks.py` checks `shutil.which('forge')`
and `os.access(path, os.X_OK)` but does not verify the found binary can
actually run forge. A broken `~/.local/bin/forge` wrapper (e.g., from a
failed pipx install) passes both checks but crashes on every commit.

## Suggested Fix

After resolving the path, run `<forge_path> --version` and verify it exits 0
with output matching `forge `. If it fails, fall back to sys.executable.

## Scope

This is a pre-existing limitation, not a Phase 1 regression. Track as a
minor improvement for the next forge maintenance cycle.

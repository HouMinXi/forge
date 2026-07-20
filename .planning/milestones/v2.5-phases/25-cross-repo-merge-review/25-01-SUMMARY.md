# Phase 25-01 Summary

**Wave 1: siblings section schema + validation**
**Commit:** 8953d54 (merged to main)
**Date:** reconstructed 2026-06-19

> Reconstructed post-hoc from the merged commit. The original execution SUMMARY was
> never persisted to the main .planning/ working tree (it lived only in the now-removed
> execution worktree). Content below is grounded in the merged diff, not a live log.

## What changed

src/code_forge/gate.schema.json: added the `siblings` section to the gate.yaml schema
  (a list of repo/ref entries) so a cross-repo review unit can be declared in config.

src/code_forge/gate_check.py: added validate_siblings(siblings, *, gate_yaml_dir,
  primary_language=...) -- validates each sibling entry (repo path resolvable relative
  to the gate.yaml dir, ref present, local-only in v1: remote https:// rejected),
  returning the normalized list or raising ValueError (fail-closed).

tests/test_schema_corpus.py: schema-corpus coverage for the siblings section, including
  remote-URL rejection and malformed-entry cases.

## Must-haves verified (against merged code)

- gate.yaml schema accepts a `siblings` list: YES (gate.schema.json)
- validate_siblings rejects remote (https://) siblings, v1 local-only: YES
  (later exercised by test_remote_url_rejected in Wave 3)
- validation uses the gate.yaml directory as the resolution base (narrow base): YES
  (gate_yaml_dir parameter; consumed with the narrow base by Waves 2-3)
- 443 insertions, schema + validation + corpus tests; no runtime dispatch yet

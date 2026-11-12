# G12 candidate evidence: three codec boundaries

This is bounded evidence for the published [W1 summary](https://github.com/HouMinXi/forge/issues/441#issuecomment-6745701302), not recovery of the original 78 tests, formal G02/G12 approval, or completion of #33/#441. The original test bytes and expanded identities remain unknown. Product baseline: `07a194830db454165aec271b209be4b21646bd0c` (including #472). The earlier local qualification used `08c1465e`; current evidence is rerun rather than inherited. No production behavior changes.

| Published clause | Actual pytest family in `tests/test_advisory_diagnostics.py` | Independent oracle | Single source change and expected failure |
| --- | --- | --- | --- |
| Duplicate root/nested keys | `test_duplicate_keys_in_otherwise_valid_document_rejected` (root, repository, diagnostic) | Ordinary JSON decoding of duplicate and original bytes produces the same legal document; the original also decodes successfully. Only duplication changes. | Remove only `_pairs`' duplicate guard: all three fail with `DID NOT RAISE`. |
| Approved byte limit and one beyond | `test_wire_byte_budget_on_otherwise_valid_document` (4096, 4097) | Literal lengths and real JSON whitespace preserve identical legal content and an independently held observation. | Remove only decoder length comparison: only 4097 fails with `DID NOT RAISE`. Separately change `>` to `>=`: only 4096 fails at positive decoding with `invalid_schema`. These values describe current v1, not new schema approval. |
| External execution/repository/source/diff/sequence matching | `test_decoder_rejects_each_valid_foreign_expected_context` (five explicit fields) | Construct legal JSON directly from a held context, prove positive decode, then change exactly one valid external expected field without changing payload. Require exact `context_mismatch`. | Remove only decoder `context != expected` comparison: all five fail with `DID NOT RAISE`. |

Every experiment first passed unchanged, used an isolated source copy, and restored byte-exact production bytes before passing the same tests again. Setup errors, skips and unrelated failures do not qualify. The dedicated local harness checked JUnit failure counts and messages, and retained per-run logs, nodeids, commands and SHA-256 values. Python 3.14.10 / pytest 9.2.0 was used; no hosted CI or paid provider was invoked.

The new five tests isolate decoding from publication/locator validation. Previously removing that comparison still caused three failures in the 316-case storage module: source and diff expected-context cases plus a stale-context case. Thus existing tests already detected the overall omission; this patch strengthens per-field decoder evidence rather than claiming a previously undetected product bug. Valid foreign execution/repository/sequence values can be rejected earlier by locator validation, so those filesystem tests alone do not qualify the decoder check.

## Deliberate limits

These controls do not qualify filesystem readers, held-descriptor identity, publication outcomes, recovery, native host concurrency, durable receipt retrieval, or every W1 schema/resource check. The byte controls are decoder-only. The context controls prove valid mismatches, not all invalid types or UUID rules. No test count is transferred from the historical port or added across repeated runs.

This slice supplements the existing [acceptance map](cross-repo-diagnostics-v1-acceptance.md). Remaining work includes exact other-clause oracles and omission controls, original-port disposition or explicit versioned replacement approval, G04/G09 consumer scope, T14 recovery scope, G11 asynchronous cleanup limits and same-revision final independent acceptance. #33 and #441 stay open; #400 is unchanged.

## Reproduction commands

Use an owned disposable source copy of the pinned revision with this test patch,
Python 3.14.10 and the project's pytest 9.2.0 dependencies. From that copy's root:

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_advisory_diagnostics.py::test_duplicate_keys_in_otherwise_valid_document_rejected \
  tests/test_advisory_diagnostics.py::test_wire_byte_budget_on_otherwise_valid_document \
  tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context
```

This unchanged group has ten cases. For each independent experiment, preserve
the exact original `_advisory_diagnostics.py` bytes, make only the source change
in the table, and run only that row's family with the same command flags. For the
two byte experiments, start from the original bytes separately. Check the exact
failing parameter(s) and assertion described above, not just a nonzero exit.
Restore and compare the original bytes before rerunning the same family. Never
carry a source omission into a production commit. The baseline must pass first;
collection/setup/timeout errors stop qualification rather than count as RED.

The candidate was also composed on main
`8b0b88ec6717fa4a0bf9aad40068ba8c67c45bd7` after #474. Its parallel identity
additions are retained. The diagnostic production modules and this test delta
are unchanged; composition qualification is recorded separately from the
single-boundary experiments above.

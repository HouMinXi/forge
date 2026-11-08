# Explicit Python return contracts (experimental)

This opt-in command checks one complete caller-specified obligation against a committed Git snapshot: the selected function, given these concrete primitive arguments, must return this exact primitive value. A violation is `CONFIRMED`; a satisfied obligation is `DISMISSED`. These words apply only to that obligation, not an arbitrary finding about the same source.

For a committed `sample.py` containing `def value():` followed by `pass`:

```sh
python -m code_forge.return_contract \
  --repository /absolute/repository/root \
  --revision FULL_COMMIT_ID \
  --path sample.py --function value --arguments '{}' --expected '7'
```

This reports `CONFIRMED`: implicit `None` violates the explicit expected value `7`. Changing `--expected` to `null` reports `DISMISSED`. The caller supplies expected behavior from actual requirements; the tool does not infer requirements from model prose or establish that the chosen contract is appropriate.

## Boundaries

- The snapshot is the complete regular-file blob at the exact supplied commit, not dirty working-tree bytes. Output binds repository root, revision, path, blob, raw-source SHA-256, function, arguments, expected/actual values, rule and Python parser identity.
- Acquisition disables Git environment redirection, replacement objects, lazy fetch and external transports, including repository-specific protocol allowances. Paths are literal. Missing objects are acquisition failures, not permission to fetch. Symlinks and submodules are unsupported.
- Evaluation is static over a small admitted Python subset. Reviewed code is never imported or executed. External calls, aliases, decorators, rebindings, classes, generators and unsupported syntax do not gain a verdict. Source/input budgets restrict accepted programs; this is not a hostile-parser sandbox or general Python interpreter.
- Inputs are `null`, booleans, integers of at most 256 bits and strings of at most 1024 characters. Equality is type-exact: `true` and `1` are not interchangeable. The checker does not prove performance, effects, application call reachability or library behavior.
- `UNKNOWN` means unsupported or insufficient semantics; `CHECK_FAILED` means source acquisition failed. Neither means the obligation is satisfied. CLI exit codes are 0 satisfied, 1 violated, 2 unknown/invalid request, and 3 acquisition failure.
- Results are not credentials. No API accepts serialized results as proof. They cannot grant normal-review clean credit or release arbitrary L1 findings. Normal Forge review guards remain unchanged. This is a standalone complete contract path, **not resolution of all six #415 prose cases** or normal-review integration.

The Python API is `code_forge.return_contract.check_return_contract`. The host/user must choose its input contract, not automatically copy model response fields. Re-run the check for a different revision or changed contract; a displayed outcome grants no authority for another goal.

# Receipt format: `reviewer_response_sha256`

Each accepted pass writes `.code-forge/receipts/receipt-c<cycle>p<pass>.json`. This page documents the field added for #222. It doesn't cover the other fields.

| Field | Type | Present when |
|---|---|---|
| `reviewer_response_sha256` | 64-char lowercase hex | the pass's raw reviewer response was retained (accepted text) |

**Value.** SHA-256 of that cycle's raw reviewer response for the pass, after normalization:
- JSON, bare or inside a ```` ```json ```` fence, is re-serialized with sorted keys, compact separators, and whitespace runs inside every string collapsed to one space.
- Non-JSON text has all whitespace runs collapsed.
- Several responses for one pass (grouped review) are hashed individually, sorted, joined with `\n`, and hashed again.

Reformatting or re-indenting a reused response doesn't change its hash. Changing any word does. The hash is evidence that the answer was produced again, not proof of an independent review.

**Use in verify check 7 (hardened path).** Excerpt line-set Jaccard overlap above 0.8 between two of the last cycles still fails, unless:
- every receipt of both cycles carries a valid `reviewer_response_sha256`, **and**
- no hash appears in both cycles.

A small diff whose unfixed finding is honestly re-quoted every cycle therefore passes. Copied or reused answers fail. A receipt without the field (written before #222) keeps the previous behaviour, so the overlap alone decides. The legacy, non-hardened path is unchanged.

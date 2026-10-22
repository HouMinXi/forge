# Receipt format: `reviewer_response_sha256`

Each accepted pass writes `.code-forge/receipts/receipt-c<cycle>p<pass>.json`. This page documents the field added for #222. It doesn't cover the other fields.

| Field | Type | Present when |
|---|---|---|
| `reviewer_response_sha256` | 64-char lowercase hex | the pass's raw reviewer response was retained (accepted text) |
| `reviewer_response_shingles` | sorted list of 16-char lowercase hex | same as above (#240) |

**Value.** SHA-256 of that cycle's raw reviewer response for the pass, after normalization:
- JSON, bare or inside a ```` ```json ```` fence, is re-serialized with sorted keys, compact separators, and whitespace runs inside every string collapsed to one space.
- Non-JSON text has all whitespace runs collapsed.
- Several responses for one pass (grouped review) are hashed individually, sorted, joined with `\n`, and hashed again.

Reformatting or re-indenting a reused response doesn't change its hash. Changing any word does. The hash is evidence that the answer was produced again, not proof of an independent review.

**Shingles (#240).** The normalized response (as above, so JSON key order and layout don't matter) is casefolded and split into tokens: maximal runs of Unicode word characters (`\w+`). Punctuation, quotes and JSON syntax only separate tokens. Each window of 3 consecutive tokens is one shingle; a response with fewer than 3 tokens is a single shingle, and an empty one has none. Each shingle is stored as the first 16 hex digits of the SHA-256 of its tokens joined by one space, so no response text is kept. A grouped pass stores the union over its responses.

**Use in verify check 7 (hardened path).** Excerpt line-set Jaccard overlap above 0.8 between two of the last cycles still fails, unless:
- every receipt of both cycles carries a valid `reviewer_response_sha256`, **and**
- no hash appears in both cycles, **and**
- no response of one cycle is a near duplicate of any response of the other: shingle Jaccard ≥ 0.9 (two empty sets count as 1.0). A copy with a nonce key, one changed word or a case change is caught this way.

A small diff whose unfixed finding is honestly re-quoted every cycle therefore passes, provided each cycle's answer is written afresh. Copied, reused or lightly edited answers fail.

**Known trade-off (#240 option 2).** An honest re-review of the same unfixed finding that comes out near-identical (≥ 0.9) can't be told apart from a lightly edited reuse, so it fails check 7. That brings back part of the #222 false positive. Very short responses go the other way: one changed word moves many of their few shingles, so a short edited copy can fall below 0.9 and only exact replay catches it. A receipt missing either field, or with a malformed value, keeps the previous behaviour, so the overlap alone decides. The legacy, non-hardened path is unchanged.

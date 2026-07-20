# J1 Deliverable 1 -- forge real review workload

For: H0b gpu-win qwen endpoint benchmark (tok/s floor re-validation
and p95 first-token). From: forge group, 2026-07-11. Ref: fleet
dispatch J1 (charter s1 ACCEPT clause).

## What this is

Nine REAL forge L1 review-pass prompts. Not synthetic: each prompt is
the exact string forge's pipeline sends to a backend (template
verbatim from the pipeline's prompt builder; see make_workload.py),
wrapped around a real merged diff from forge main history.

| size   | diff source (forge main)                | prompts | ~tokens each |
|--------|------------------------------------------|---------|--------------|
| small  | llm body-parse fix (7011ade, 1 commit)   | 3       | ~1.7K        |
| medium | wall-deadline enforcement (4c5f46d)      | 3       | ~3.7K        |
| large  | multi-language support (11-commit merge) | 3       | ~14.3K       |

Three passes per size: qodo / expert / adversarial (differ only in
the role line; token mass is the diff). manifest.tsv has exact
byte/token numbers.

## How to replay

Send each prompt file as a single user message, non-stream or stream
(forge consumes both; production config uses stream=true). The
response contract is a JSON envelope (findings + code_excerpts), so
expect output in the 500-4000 token range depending on diff size --
measure generation tok/s on that output, and p95 first-token across
all nine.

Representative mix for the floor: weight medium highest (typical
phase commit), small for quick fixes, large for merge-time review.
All nine at >=15 tok/s generation = floor satisfied on real load.

## Regenerate / extend

python3 make_workload.py <small.diff> <medium.diff> <large.diff>
The three source diffs ship alongside (diff_*.diff). To add sizes,
extract any real diff from forge main and pass it in.

## Redaction status

Scanned for API-key patterns, bearer tokens, and LAN IPs: zero hits.
Diff content is forge source code (public-intent).

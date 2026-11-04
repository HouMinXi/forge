# Public diagnostic evidence boundary

All collection modes in `strict-linux-admission.yml` publish only a bounded
summary. The static and fixed-metadata jobs use the exact workspace files
`forge-static-public/summary.json` and `forge-fixed-metadata-public/summary.json`.
Ordinary dependency and file-view jobs retain their exact `public-summary.json`
files. This applies equally to public and private repositories; there is no
visibility-based raw-receipt upload. The conflict-only job publishes its fixed,
data-free STOP receipt.

The publication helpers project recognized receipts onto fixed schemas. They
keep only the selected mode, finite observation status and failure category,
`qualification=NOT_RUN`, and bounded aggregate counts when available. Static
schema v2 also includes sorted, unique problem codes from 14 reviewed literals;
these are necessary-observation categories, not raw diagnostics or root causes.
Unknown or malformed problem codes fail closed without publishing a partial
list. Historical static v1 artifacts have no problem-code field. The helpers never
copy arbitrary keys, strings, context, unit identifiers, paths, dependency
edges, PIDs, file hashes, raw stdout, raw stderr, or exception messages.
Unrecognized, missing, malformed, oversized, duplicate-key or unavailable input
produces a fixed UNKNOWN summary, never a fallback upload of the original file.

The collector remains unchanged. Raw JSON, stdout and stderr stay in a private
runner temporary directory, and shell tracing is disabled. The entire shell
starts with stderr suppressed, then sends shell-generated diagnostics to a
private file. Signal termination and failed redirections cannot bypass the
child-process redirections and expose a PID, command or path in public logs. Artifact upload has
one exact summary-file path, not the directory or a wildcard. The diagnostic's
nonzero exit status remains a job failure even if summarization succeeds. A
summary failure also fails the step; interruption before summary creation can
leave no artifact. The static/fixed projections also bind the collector exit
code, so a success receipt followed by a failed or killed process is published
as STOP. Their uploads require an explicit readiness flag, a nonempty regular
non-symlink summary, and a successful byte-count check at most 1024 bytes. The
ordinary/view projections retain their separate observation-only contract:
their artifact status reflects the receipt, while the job preserves any later
collector failure. No raw log fallback is permitted.

This deliberately removes public access to detailed topology, process and file
provenance. The aggregate artifact is not sufficient to reconstruct or fully
audit the original observation. Obtaining or sharing a private raw copy requires
a separate authorized destination and transfer; this workflow does not provide
one. No old artifact is deleted by this change, and it cannot retract copies
already downloaded. Temporary runner files are not a supported private archive.

The projection is not an admission validator or protection against malicious
workflow code, an attacker controlling the runner, or a hostile repository
change that replaces the publication helper. Existing exact-source review,
branch/mode fencing, privilege approvals and native-run restrictions still apply.
No dispatch, sudo operation or service invocation is added by this change.

Offline tests inject secret-looking unit names, paths, dependency values and
PIDs into successful and failed receipts. They execute the actual workflow shell
with its collector replaced by a deterministic local fixture, across all four
modes and multiple exit codes; no native diagnostic is run. Tests assert no raw
canary reaches stdout/stderr or the only upload destination, and cover malformed,
missing, duplicate-key, oversized, deeply nested, symlink and FIFO inputs.

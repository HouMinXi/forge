# Installed-wheel publication boundary

The job retains the downloadable original wheel and the existing partial runtime
controls. It still provisions the declared dependency wheels and runs
`scripts/qualify_installed_wheel.py` offline, with its original validation,
15-control roster, subprocess timeouts, and nonzero failure behavior. The harness,
its environment, and dependency ranges are unchanged. Its successful exit is not
native mutation, ABA, reload, phase, or full-suite coverage acceptance.

## Private execution and fixed summary

Setup, qualification, release verification, and summary generation redirect
stdout, stderr, shell diagnostics, command output, and tracebacks before doing
work. Each creates an owner-only directory under the runner's temporary directory.
The harness output stays outside the checkout. The job never uploads its command
logs, build proof, runtime proof, test report, or private directories. This workflow
does not promise that private files have been erased or that failed subprocesses
have been cleaned up.

An `always()` step projects GitHub's provisioning outcome, qualification outcome,
and current job status into a closed summary. It does not read private evidence,
parse a printed `PASS` line, import the harness, or rerun qualification. `PASS`
requires all three runner outcomes to be `success`. Recognized failure or
cancellation is `STOP`; incomplete or unrecognized outcomes are `UNKNOWN`. Every
field is a fixed label or enum. The scope is always `PARTIAL_RUNTIME_ONLY`, and raw
evidence is always `NOT_PUBLISHED`.

The helper writes a private candidate. An independent stdlib gate validates its
exit and entire parsed JSON against a separately constructed expected envelope.
It rejects extra fields, duplicate keys, numbers, malformed/oversized input,
nonregular files, symlinks, and changes during reading. Only newly serialized
constants reach the public summary. Existing output paths fail closed. A killed
helper, shell error, timeout, or interrupted write cannot satisfy the summary
upload's successful-step condition. Publishing `STOP` cannot turn qualification
failure into success. A release-verification failure also makes the job fail and
prevents a passing summary.

## Preserve the exact qualified wheel

The `installed-wheel-evidence` artifact still provides the wheel download, but no
raw proof files. `scripts/installed_wheel_publication.py` is a separate release
check, not an additional qualification or an alternative to the original controls.
It runs only after successful setup and qualification. It requires:

1. Exactly one expected wheel in the unique fresh private qualification directory.
   Directory traversal uses no-follow directory descriptors. Files must be regular,
   bounded, and observed stable while reading; symlinks/FIFOs and extra artifacts
   fail closed. The private build proof must name the same HEAD commit and wheel,
   and its original wheel digest must match the buffered original bytes.
2. A complete expected payload from Git HEAD blobs: every allowed packaged source
   and resource, the license, and README. Unsupported source layouts fail closed.
   Generated METADATA, WHEEL, entry points, top-level names, and canonical RECORD
   are reconstructed exactly from these public inputs and reviewed fixed strings.
   Arbitrary extra metadata, installation records, logs, and untracked files cannot
   enter the expected archive. The current reviewed generator is exactly
   `setuptools (84.2.1)` from the official PyPI distribution. No dependency pin was
   added; an unreviewed backend format/version safely stops wheel publication until
   the release check is reviewed and updated.
3. Exact expected ZIP bytes, including all names, member order, local and central
   headers, offsets, permissions, flags, compression, RECORD hashes/sizes, and end
   records. A comparator ZIP is constructed in memory using only the approved
   content and metadata. The original must equal it byte for byte. This rejects
   alternative DEFLATE encodings, padding, comments, extra fields, hidden members,
   prefixes, trailers, or inconsistent headers even when extraction looks normal.
   Different compression-library serialization can cause a safe rejection.

The one approved variable metadata category is the six generated dist-info ZIP
members' build-clock timestamps. Official wheel/setuptools construction obtains
these from file mtimes or `time.time()`, converted to UTC DOS timestamps. They
contain no path, PID, or host identity and are treated as public build metadata.
The workflow records private whole-second qualification start/finish bounds. The
check accepts only valid calendar timestamps inside that interval, allowing the
single second lost by DOS two-second truncation, with a maximum 15-minute duration
and RECORD no earlier than the other generated members. The interval must also
be fresh in the current 15-minute job window, with no future finish time. Source-member timestamps
must equal the public HEAD commit time, with the format's 1980 minimum and DOS
truncation. The gate does not claim generated timestamps are source-derived or
constant. No SOURCE_DATE_EPOCH or other harness environment change was added.

The comparator ZIP is never published or substituted. After whole-file equality,
the verifier writes the buffered ORIGINAL wheel to a fresh owner-only public
artifact directory and checks the copy byte for byte. Only that exact filename is
uploaded, after release verification and the preceding job steps succeed. Failure,
timeout, or kill prevents upload even if a partial public file remains. The public
summary is uploaded separately. Neither artifact carries the private proof digest
or a hash of unrelated private evidence.

## Validation and limits

Publication tests execute the actual YAML shell only with harmless setup/harness
substitutes. Wheel tests use synthetic archives to inject private canaries into
payloads, metadata, ZIP headers/comments/extras, and trailing data, and exercise
clock, proof-binding, filesystem, error, and kill cases. These tests do not run
native controls, services, namespaces, or real installed-wheel qualification.
A separately authorized local build using official free PyPI tools was inspected
and passed the full original-byte comparator; that is build/publication evidence,
not a claim that its installed runtime controls ran locally.

The CLI requires the workflow's exact public directory name, the current checkout
working directory, and the absolute runner temporary directory without symlinked
ancestors. Internal test APIs accept caller-controlled fixture paths. Git source
reads are trusted-repository operations: their size checks happen after subprocess
capture, not as a streaming memory-isolation guarantee.

This boundary assumes the reviewed public repository source, workflow/scripts,
GitHub's outcome reporting, pinned actions, and trusted official build tools. It is
not a defense against malicious reviewed source, a compromised runner, or arbitrary
replacement of the verifier. Platform-generated checkout/setup/action logs remain
outside its control. No private evidence is intentionally supplied to those
actions. Future source layouts/backend formats require a reviewed gate update,
rather than silent metadata acceptance or removal of the wheel download.

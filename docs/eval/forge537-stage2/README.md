# Forge #537 Stage 2: provider smoke archive

## Stage 2 disposition

Stage 2 is closed. Keep **A** as the default. Do not rerun the provider smoke
or the four-case pilot, and do not start **C₀**. The commands in this archive
are offline checks with synthetic replies and mock transport.

The later, separate four-case workstation pilot had 24 validated replies.
After deduplication, both arms caught the same three known defects and
neither reported a finding on the clean case. B used 2,192 fewer input
tokens (15.0%) but 7,790 more output tokens, a net increase of 5,598
(16.2%). These usage counts describe synthetic cases. They do not establish
cost or recall on real changes. The paired runner was separate from this PR.

See the [locked assessment and reveal](https://github.com/HouMinXi/forge/pull/547#issuecomment-7036791249)
and [recorded decision](https://github.com/HouMinXi/forge/pull/547#stage-2-disposition).
Production review continues under the frozen
[Stage 1 contract](https://github.com/HouMinXi/forge/issues/537#issuecomment-6999075145).

## What the original smoke measured

The workstation smoke ran Forge in `ci` mode with the subprocess outlet.
Its `l1-only-smoke` result records fixture validation. Forge PASS and LOCAL
three-clean-round acceptance require separate review evidence.

- Control **A** rendered the shared L1–L5 card block. Variant **B** removed
  only cards outside the assigned pass. Rules, 1/2/2 assignments, ordering,
  source evidence, grouping, and the response contract stayed fixed. The
  comparison checked each B prompt against its A counterpart after removing
  only those cards.
- The input was one generated, committed Python change in a disposable
  repository. The arms began with separate Forge state and used Stage 1
  merge `a72dc5a990194494c875fac7b783436b30ed272d` as the control.
- The fixture allowed one attempt per pass and at most six model POST
  attempts at Forge's HTTP request boundary. The cap counted failed
  attempts and refused retries, redirects, and other AI requests. Its
  coverage ended at Forge's transport before any gateway-internal routing.
- Both arms used the historical `agnes-cn` alias and one output-limit field
  of 131,072 tokens per request. The fixture checked the wire body
  independently of the profile. B could start after all three A replies
  cleared the L1 factory schema and excerpt checks.
- The recorded metadata covered admitted attempts, validated slots, per-pass
  usage including cache when reported, and elapsed time per arm. Absent
  usage appears as `null`. The transport guard could refuse requests from
  stages outside L1.

## Offline verification

From this archive checkout, use Git, Python 3.12+, Ruff, and the dependencies
for the focused tests below. Use a network-disabled environment with provider
credentials absent and custom Git hooks and tracing disabled. The frozen
overlay is test input for the disposable checkout.

```sh
tool="$PWD/docs/eval/forge537-stage2"
scratch=$(mktemp -d)
control="$scratch/forge"
git worktree add --detach "$control" a72dc5a990194494c875fac7b783436b30ed272d
git -C "$control" apply --check "$tool/experiment-overlay.patch"
git -C "$control" apply "$tool/experiment-overlay.patch"

(cd "$control" && env -u FORGE_EXPERIMENT_537_ARM \
  PYTHONPATH="$control/src:$control:$tool" python -m pytest -q \
  tests/test_lens_prompts.py \
  "$tool/test_overlay.py" "$tool/test_factory_transport.py")
```

After inspection, reverse the overlay in that disposable worktree with
`git -C "$control" apply -R "$tool/experiment-overlay.patch"`, then use
`git worktree remove "$control"` if Git reports a clean worktree.

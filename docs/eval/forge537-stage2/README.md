# Forge #537 Stage 2: local provider smoke

Status: draft experiment, not a production feature or a merge recommendation.
The experiment exercises a real configured provider when its operator asks for
one run. It runs **on your workstation**, but the inner Forge mode is explicitly
`ci` with the subprocess outlet. This is not a Forge LOCAL three-clean-round
run, a hosted GitHub Actions measurement, or a comparison with MCP.

## What the experiment measures

- Control **A** renders the shared L1–L5 card block. Variant **B** removes only
  cards not assigned to that pass. Rules, 1/2/2 assignments, ordering, source
  evidence, grouping, and response contract stay fixed. Before each B send,
  verify equality against the corresponding A prompt after only that deletion.
- The candidate is one generated, committed Python change in a disposable
  repository, not the current checkout or a real project diff. The two arms
  start with separate Forge state. This version pins
  `a72dc5a990194494c875fac7b783436b30ed272d`, the Stage 1 merge, to avoid
  moving the control when main changes.
- Each arm gets one attempt per pass. Allow at most six model POST attempts
  from Forge to the explicitly pinned HTTPS host and path; count failed
  attempts and refuse retries, redirects, and other AI requests. This is a
  guard at Forge's stdlib HTTP request boundary, not a general machine network
  policy or a cap on gateway-internal routing.
- Both arms use the `agnes-cn` alias/model and one output-limit field of
  exactly 131,072 tokens per request. The wire check is independent of the
  profile check. Start B only if all three A replies cleared the fixture's
  L1 factory schema and excerpt checks. A rejected pass can still have usage.
- Capture only aggregate metadata: admitted attempts, validated slots,
  per-pass usage (including cache when reported), and per-arm elapsed time.
  Missing usage is `null`, not zero. A `l1-only-smoke` result is not a
  Forge PASS or receipt; non-L1 stages can fail because their requests are
  intentionally refused. There is no quality or billing conclusion from one
  synthetic sample. Do not count this toward LOCAL acceptance or change a gate.

## Prepare a disposable checkout

Run from this PR checkout with Git, Python 3.12+, Ruff, and Forge's declared
development dependencies available. Use an environment without custom Git
hooks or trace/debug settings. The patch is experiment input, not a source
change in this PR. Leave it out of your ordinary checkout.

```sh
tool="$PWD/docs/eval/forge537-stage2"
scratch=$(mktemp -d)
control="$scratch/forge"
git worktree add --detach "$control" a72dc5a990194494c875fac7b783436b30ed272d
git -C "$control" apply --check "$tool/experiment-overlay.patch"
git -C "$control" apply "$tool/experiment-overlay.patch"
export PYTHONPATH="$control/src:$control:$tool"
```

Offline checks, from the control worktree:

```sh
(cd "$control" && env -u FORGE_EXPERIMENT_537_ARM python -m pytest -q \
  tests/test_lens_prompts.py \
  "$tool/test_overlay.py" "$tool/test_transport_cap.py" "$tool/test_factory_transport.py")
```

## Use your local approved Omni route

Start from a terminal where the credential is already loaded. Do not print
it, paste it into the PR, or put it into a config file. Use a separate
`config.yaml` under a private directory and set `FORGE_CONFIG_DIR` to that
directory for the two commands below. Reuse your **own verified HTTPS** Omni
base URL (ending in `/v1`, no trailing slash). The exact same URL must be
supplied as `--expected-base-url`. Do not replace an unsupported route with
another destination or disable TLS verification.

Example configuration, without any secret value:

```yaml
backends:
  agnes-cn:
    type: api
    format: openai
    model: agnes-cn
    base_url: https://omni.example.invalid/v1
    api_key_env: OMNIROUTE_API_KEY
    max_tokens: 131072
    stream: true
    timeout_s: 2400
    headers:
      x-omniroute-no-cache: "true"
      x-omniroute-no-memory: "1"
      x-omniroute-compression: "off"
```

Set `OMNI_BASE_URL` to the approved URL from the example config after
substituting your actual URL. The first command only checks local config and
credential presence; `ready-no-sends` is not an authentication result. If
you already have an authenticated model-list check for the same origin, use
it to verify the account and alias before spending requests.

```sh
python "$tool/run_smoke.py" --expected-base-url "$OMNI_BASE_URL"
```

Only if preflight reports `ready-no-sends` and you want to spend the agreed
budget, run this **once**. Do not retry automatically after failure or
incomplete results, and do not switch models.

```sh
python "$tool/run_smoke.py" --expected-base-url "$OMNI_BASE_URL" --execute
```

Share only the one-line JSON summary. No prompt, reply, credential, config
path, or endpoint is needed. After inspection, remove just this disposable
worktree. Reverse the experiment patch with
`git -C "$control" apply -R "$tool/experiment-overlay.patch"`, then use
`git worktree remove "$control"`. Do not force removal if Git reports any
other changes.

## Stage 2 disposition

Keep **A** as the default. Do not start **C₀** or rerun the four pilot cases.
The commands above document how the original smoke was run; they are not
a request to repeat the experiment. No production review settings change.

The later, separate four-case workstation pilot had 24 validated replies.
After deduplication, both arms caught the same three known defects and
neither reported a finding on the clean case. B used 2,192 fewer input
tokens (15.0%) but 7,790 more output tokens, a net increase of 5,598
(16.2%). These are usage counts for synthetic cases, not a cost estimate
or evidence of equivalent recall on real changes. The paired runner is
not included in this PR.

See the [locked assessment and reveal](https://github.com/HouMinXi/forge/pull/547#issuecomment-7036791249)
and [recorded decision](https://github.com/HouMinXi/forge/pull/547#stage-2-disposition).
Neither the smoke nor the pilot qualifies a Forge review, changes the
frozen [Stage 1 contract](https://github.com/HouMinXi/forge/issues/537#issuecomment-6999075145),
or enables a new production route.

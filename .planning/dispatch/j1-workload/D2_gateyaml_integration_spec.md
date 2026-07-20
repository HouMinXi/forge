# J1 Deliverable 2 -- qwen backend gate.yaml integration spec

From: forge group, 2026-07-11. Ref: fleet dispatch J1. This is the
contract the gpu-win qwen endpoint must satisfy to be wired into
forge's gate.yaml at H2 (forge Zone C change, under forge's own
review pipeline, behind a pre-frozen canary floor).

## 1. Model id

PLACEHOLDER until ashare reports the VERIFIED real id from the
serving stack (H0b step (a)). forge will write exactly that string;
"qwen3.6" is owner shorthand and will NOT be hardcoded. The endpoint
must accept OpenAI-format chat completions (forge backend entry will
be `type: api, format: openai`).

## 2. Timeout and retry semantics (what the review loop assumes)

Current production values forge will apply to the qwen entry:

    timeout_s: 600            # per-request wall clock, per pass
    max_completion_tokens: 32768
    stream: true              # see section 4

Retry behavior (pipeline-fixed, not configurable per backend):
5 attempts max, exponential backoff starting at 2.0s. Retryable
classes include HTTP errors, timeouts, AND garbled/non-JSON response
bodies (forge wraps body-parse failures as retryable).

Lock-yield implication: gpu-win serving can be yielded to training
(mutex per gpu-switch design). Worst case forge tolerates per pass:
600s x 5 attempts. If the endpoint is yielded LONGER than ~10
minutes, forge review on qwen fails loudly (correct behavior -- see
fallback). If yield events are expected to be frequent, the endpoint
should return a fast, distinguishable refusal (connection refused or
503) rather than accepting and hanging: fast-fail lets forge burn
seconds instead of minutes per attempt.

## 3. Fallback ordering

Honest statement: forge has NO automatic runtime fallback chain
today. gate.yaml holds named backends with one `default: true`;
failover is a config-level switch (operator or wrapper flips the
default), not silent per-request rerouting. The ordering forge
declares for qwen-down/yielded:

    1. qwen-gpuwin        (once accepted at H2)
    2. review-route       (OmniRoute aggregate, current default)
    3. deepseek-direct    (direct API, VPN-independent of OmniRoute
                           host but cross-Pacific)

A failed review run names the backend and the failure class in its
error (post body-parse-wrap this is reliable), so the switch decision
is informed. If the fleet wants automatic failover, that is a forge
feature request routed as a contract -- not assumed here.

## 4. Stream discipline (lesson pre-paid by OmniRoute)

The endpoint MUST do one of:
  (a) honor `stream: false` with a plain JSON body, or
  (b) document itself as SSE-always, in which case forge configures
      `stream: true` from day one.
OmniRoute silently answered SSE to non-stream requests; every naive
JSON client failed until gate.yaml grew `stream: true`. Do not
reproduce that discovery cost: state the endpoint's stream contract
in the H0b done-report.

## 5. Acceptance hook

Endpoint is accepted when: the nine D1 workload prompts (see
README.md alongside) replay at >=15 tok/s generation each, p95
first-token reported, through the SAME request shape forge uses
(openai chat completions, stream per section 4, 32K completion cap).
forge PM signs off against those numbers, then H2 wiring proceeds
under forge's own review pipeline with the canary floor frozen
BEFORE the first dual-run result exists.

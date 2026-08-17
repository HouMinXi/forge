# Streaming backends report zero tokens

Found while probing Bonsai (llama.cpp on the LAN) as a free review backend.
It is the first entry in gate.yaml with `stream: true`, and the first time
that path ran against a real server.

## What is wrong

OpenAI's SSE protocol sends no usage block unless the request asks for it.
Nothing in the codebase ever asked -- `grep -rn stream_options src/` had
zero hits -- so every streaming pass reported 0 in / 0 out.

Measured against llama.cpp, same prompt, same model:

    without stream_options    stream ok, NO usage
    with    stream_options    usage PRESENT
                              prompt_tokens 15, completion_tokens 105

`_read_sse` was already written to pick up `chunk["usage"]`. The counts
simply never arrived.

## Why it stayed hidden

Zero reads as "nothing to report", not as "never sent".

`factories.py` prints the per-pass line only when a count is nonzero, so a
streaming backend printed nothing at all -- no missing number on screen,
just a line that quietly stopped existing. Downstream, `_round_input_tokens`
accumulated zero and `cost_per_pass` recorded `0 // 3`.

The per-pass token digits are the cached-replay detector. Turning on
streaming disabled it silently.

## Compatibility

`stream_options` is a newer OpenAI field, so the risk was a gateway
rejecting it. Probed 2026-08-09:

| path | without | with |
| --- | --- | --- |
| Bonsai direct (llama.cpp) | no usage | usage present |
| OmniRoute -> sn-deepseek-flash | usage present | usage present, no error |

OmniRoute already supplies usage on its own, so the field is harmless
redundancy there and required for llama.cpp. No path fails with it.

anthropic and vertex reject `stream: true` up front with a message naming
the working alternative, so neither needed a change.

Noted in passing, out of scope here: `onmi-opus4.6` now answers "Unable to
determine provider for model" through the gateway. That is a gate.yaml
entry that has gone stale, unrelated to streaming.

## Bug-injection

| Injection | Result |
| --- | --- |
| drop `stream_options` entirely | caught |
| send it unconditionally, streaming or not | caught |
| ask for `include_usage: False` | caught |
| `_read_sse` drops the usage chunk | caught |

## Real path

A review-shaped prompt through `llm_invoke` against Bonsai, before and
after, same diff and same returned JSON (`findings`, `code_excerpts`):

    before    0 in /    0 out   67.4s
    after   694 in / 3576 out   69.4s

Output dwarfs input because Bonsai is a thinking model and `_read_sse`
discards `reasoning_content`. Those tokens are spent either way, so the
old behaviour under-counted a thinking model exactly where it costs most.

## Checks

    py_compile   OK
    ruff         All checks passed
    non-ASCII    0
    tests        249 passed in tests/test_llm_invoke.py
    full suite   see stream_full.txt

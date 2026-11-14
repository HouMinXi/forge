# T06: configured retry budget above five

Base: `e6f85d9e0f61be5a704fdfdbc6bf18fd62cbfc31`.

The original [T06 row](https://github.com/HouMinXi/forge/issues/68#issuecomment-6599263807)
requires M=1 and a supported M>5 without attempt M+1. The
[budget evidence clarification](https://github.com/HouMinXi/forge/issues/68#issuecomment-6599427156)
also requires an unread spare response and one logical retry operation.

`tests/test_invocation_t06_retry_budget.py` retains M=1/3 and adds M=6,
each for exhausted 429 responses and success on the last allowed response.
The real invocation loop and host admission use the original configured M;
host quota stays 8, greater than M, so host refusal cannot masquerade as the
model retry bound. Request/timeout/backoff policy and production code are not
changed. Every physical attempt has a distinct identity and raw reference,
with the original logical identity, ordered parent chain and terminal outcome.

A real extra scripted response is available at index M. The oracle requires
exactly M transport entries and no read, close or cursor advancement on that
spare. The negative control intentionally configures seven attempts against
six synthetic 429 responses plus a successful spare under host quota eight.
It must actually enter the seventh transport call and read that response,
then fail the six-attempt spare oracle. This is an oracle sensitivity control,
not a production mutation or a claim that Forge sends beyond its configuration.

Evidence is bounded to mock HTTP and a synthetic recorder with real host/owner
admission. It does not establish native durability, external provider billing,
mixed correction/continuation/redirect or resumed-review budgets, all consumer
wiring, or completion of #68. The existing original-budget and usage assertions
remain applicable; unknown failed-response usage is not replaced with zero.

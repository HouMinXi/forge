# Ordinary reply JSON depth

Ordinary model replies have an explicit maximum of **256 nested containers**.
An object or array at the root counts as one container. Empty objects and
arrays count; sibling containers do not accumulate depth. Brackets and braces
inside strings, including escaped quotes and backslashes, do not count.

This is a compatibility tightening. Python 3.12 and 3.14 have different JSON
decoder recursion behavior; acceptance must not depend on that incidental
threshold. Replies deeper than 256 containers can now be refused even if the
interpreter could decode them.

The ordinary model parser checks depth before each decoding/quote-repair
attempt. The iterative scan stops at an invalid JSON prefix so an earlier
unescaped source quote can still be repaired; the repaired text is scanned
again. Strict rereads of ordinary reply text and fallback extraction of
candidate objects also check before decoding. Depth refusal cannot salvage a
shallower object from inside the refused reply. Existing correction retries,
usage accounting, raw audit capture and audit failure blocking remain in place.
Other syntax and duplicate-key checks remain the decoder's responsibility.

CLI transport envelopes are still decoded under their existing contract.
String-valued results are checked before their ordinary reply decode; bare or
object-valued results already decoded by the transport reader are checked
iteratively after unwrapping and before returning the reply. This does not
place the ordinary reply limit on outer-envelope metadata or event wrappers.

The `strict_response` runtime path is unchanged, including intentionally lazy
handling of deeply nested metadata. This is not a universal JSON limit or a
promise that arbitrary nesting is accepted by every interpreter. The separate
512-container MCP SARIF projection and ShellCheck limits are unchanged.

Tests in `tests/test_reply_json_depth.py` cover 255/256/257 boundaries, strings,
empty containers, broad siblings, pre-decoder rejection, fallback, CLI wrapping,
correction retries, and audit blocking. Audit fixtures simulate host settlement;
they are not evidence of filesystem storage admission or stock-runner isolation.

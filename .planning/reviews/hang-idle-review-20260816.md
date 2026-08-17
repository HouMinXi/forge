# fix/hang-idle-timeout review evidence, 2026-08-16

Branch fix/hang-idle-timeout (d0b8ece on origin/main @ 835115d):
llm_invoke._read_with_deadline installs a 900s idle bound (clamped to
the remaining deadline) on the raw socket, converts silent-connection
timeouts to the existing error shape, and reports the value actually
installed.

## Rounds (deepseek-nocache)

- R1 (2ff097a): FAIL confirmed=2 -- retryable=False contradicted the
  comment. Fixed: comment aligned with the no-same-call-retry policy
  (same as the total read deadline; a fresh round retries the pass).
- R1b (2ff097a+comment): FAIL confirmed=2 -- idle 900 vs the 600s API
  cap made the idle bound dead code on capped paths. Fixed: clamp
  min(900, remaining); best-effort _sock documented as a layer above
  the deadline join.
- R1c (c71429e): FAIL confirmed=3 -- error message reported the 900s
  constant instead of the clamped value; settimeout failures silent;
  unused test import. Fixed: message reports idle_installed, warning
  logged, import removed (d0b8ece). Python<3.10 TimeoutError claims
  dismissed twice (requires-python >= 3.12).
- R1d (d0b8ece): PASS confirmed=0.
- R2 (d0b8ece): FAIL confirmed=2 -- all three findings are third-round
  repeats of already-disposed themes: (1) private _sock, commented as
  best-effort with the deadline join as the guard; (2) Python<3.10
  socket.timeout, dismissed on requires-python; (3) idle-vs-cap,
  fixed by the min() clamp. Per rule (b), substance-free repeats do
  not block.

## Freeze decision

Oscillation between PASS and FAIL with no new substance is the
recorded deepseek 3+ round trap. The review is frozen at R1d PASS +
R2 repeats-disposed. Remaining real finding (private _sock) is a
documented best-effort layer, not a defect: when the socket is not
exposed, the deadline join still bounds the read.

All changes bug-injection verified: settimeout install, TimeoutError
conversion, min() clamp, message-installed-value.
tests/test_llm_invoke.py 256 pass.

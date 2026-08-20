# Review assignment (Gemini): runtime semantics — if this compiles and runs, what breaks?

Read the shared briefing first:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/cp-artifacts/cp1b-r1-briefing.md

Then review the plan:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/54-01-PLAN.md

You have file access to the repo at /home/houminxi/code/forge — read the real
source before asserting anything.

Your angle (play to your strength, ignore the rest):
1. RUNTIME SEMANTICS — mentally execute the plan's prescribed code:
   a. Import graph: llm_invoke.py:29 does `from .backend import ...` at
      module level; the plan has backend.py import llm_invoke FUNCTION-locally
      inside probe_backend_live, and doctor.py import the helper
      function-locally. Trace the actual import order at process start and on
      first doctor call. Is there ANY path (tests included, conftest import
      order included) where the cycle still bites?
   b. Timeout chain: effective_invoke_timeout_s priorities
      (llm_invoke.py:558-592) vs the probe's replace(cfg, timeout_s=60).
      What ACTUALLY bounds the call: connect timeout, read timeout, total?
      Where does a hung-but-connected backend get killed?
   c. Ordering: _TruncatedResponse catch (:1485) before attempt check
      (:1497) — with max_tokens=32 + a JSON-demanding prompt, enumerate the
      response shapes that still trigger continuation or no_json, and what
      the probe reports for each.
   d. Frozen dataclass replace() with the zeroed fields — verify each
      zeroing has the claimed effect in the request-builder code paths
      (thinking at :283 area, effort at :290-296, caps at :269-280).
   e. Mock realities: the plan's prescribed patch targets
      (code_forge.backend.probe_backend_live, user_config_path, record_trust)
      — do they exist at those module paths AFTER the plan's own import
      style is applied?
2. ENV/CONCURRENCY — os.environ read inside _run_trust; the probe loop is
   serial over backends; doctor's has_fail accumulation. Any host-state or
   ordering dependence that survives the plan's guards?

Verify every claim against the real files. Follow the briefing's output
contract exactly, ending with `SCORECARD: B=<n> H=<n> M=<n> L=<n>`.

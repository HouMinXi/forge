# Cross-Model Review Complementarity

Different AI models have complementary strengths in code review  --  no single model catches everything alone.

For high-correctness-bar artifacts (assertion libraries, test frameworks, security primitives), cross-model review provides coverage no single model achieves:

- **Design-focused models**: Trade-off analysis, architecture coherence, API naming conventions
- **Semantic-focused models**: Edge cases, specification compliance, error message precision, variable scope violations
- **Pattern-focused models**: Performance issues, dead code detection, /proc/filesystem overhead

## Evidence

The bash smoke-test primitives library went through 12 design iterations (two-model adversarial review) plus 3 rounds of implementation review with a third model. Each round found issues the others missed:

- Semantic model caught `local` scope violations and `jq -e` exit semantics that the design model skipped
- Pattern model flagged /proc glob performance that wasn't caught during design
- Design model identified naming conventions and API design issues others overlooked

## Recommendation

For infrastructure-level code (libraries, frameworks, primitives), run at least one review pass with a different model than the one that wrote the code. Do not assume a single model review is sufficient  --  they see different things.

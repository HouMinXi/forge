# Experimental declaration facts

This is the first, **unconnected** implementation slice of the semantic-dependency design following #369 and #415. `code_forge.declaration_facts` has no caller in the review pipeline. It does not change a disposition, clean-round counter, CI credit, model prompt or permission. It is not a fix for #415's prose-classification false positives.

## The supported question

Given complete Python source bytes and a caller-fixed goal, does the unique, undecorated top-level function declaration at the specified line contain a parameter with the specified name?

The goal includes the expected source SHA-256, function name, declaration line, parameter name and expected presence. The checker computes the actual digest, parses the entire supplied input and enumerates the whole parameter structure. It preserves positional-only, positional-or-keyword, variable-positional, keyword-only and variable-keyword categories. Its diagnostic result carries that structure, declaration range, source digest, fixed goal, rule version and parser identity.

- `SUPPORTED` and `REFUTED` apply **only** to this syntax-level parameter-membership proposition.
- `UNKNOWN` covers mismatched source, unsupported/ambiguous declarations, invalid goals and bounded-input refusals.
- `CHECK_FAILED` means the source could not be parsed. It is not evidence that a parameter is absent.
- Process interruption and `MemoryError` propagate rather than being called semantic uncertainty.

The source's expected digest and the goal must ultimately come from an independent source-snapshot owner. In this first slice they are caller inputs. A matching digest is byte identity, not proof of source provenance, file completeness or that the caller selected the correct repository/version. Constructing or deserializing the result class grants no authority.

## What this does not prove

A parameter declared positional-only does not support keyword invocation. A `**kwargs` declaration does not declare every arbitrary keyword name. Syntax parameter membership does not establish runtime keyword acceptance, optionality, successful execution, actual binding of an imported/aliased/reassigned name, valid defaults, or absence of downstream external dependencies. Parsing an AST does not establish that the entire module compiles or runs successfully. A later assignment or conditional definition may replace the runtime binding without erasing the inspected syntax declaration; the result still describes that declaration, not the final callable.

The checker does not read files, import reviewed code, execute defaults/decorators, run model commands, browse documentation, resolve aliases, or interpret explanation text. Class methods, conditional declarations and duplicate top-level declarations of the requested name are unsupported. Decorated declarations are conservatively unsupported. This is not a Python runtime signature oracle or a PowerShell parameter checker.

## Input bounds

The checker limits source bytes, token count, delimiter depth and AST node count. Tokenization is only a resource preflight; the AST parser determines the syntax facts. The input limits are not a process sandbox or a guarantee of CPU/memory containment. This unconnected utility is not a replacement for the execution isolation needed by a future hostile-input host service.

## Why this is not yet a verdict dependency solver

A model can name one true fact while omitting the premise driving its conclusion. Its proposed fact list is not a complete dependency inventory. A trustworthy future consumer must use host-defined rules with fixed conclusions and complete premises, not `complete=true`, `local=true`, a second-model endorsement or a quote/conditional exemption.

This module deliberately has no API that converts a result to `Disposition`. One supported fact must not dismiss or confirm an arbitrary natural-language finding. Quotation and reasoning text are absent from the checker input, so they cannot grant or remove authority.

## Subsequent independent stages

1. Bind a snapshot provider through the CLI, cross-repository and Outlet C factory paths. Preserve repository/version ownership, reject path escapes and stale snapshots, and keep per-invocation immutable state for parallel falsification.
2. Run the checker in shadow mode with no review-credit effects. Measure correct target binding, unsupported cases and failures against real findings; establish parser isolation if the host input threat model requires it.
3. Design narrowly typed, host-origin findings and rule-complete consumers. Only a registered rule with all host-checked premises can derive its fixed conclusion. Keep arbitrary or mixed prose unresolved, and invalidate persisted results on source/checker changes.

Those stages are not delivered here. Existing location-citation checks and display provenance do not supply their authority. #415's prose-classification limitations remain separate; this work does not resolve them.

## References and validation boundary

- [Python 3.12 AST documentation](https://docs.python.org/3.12/library/ast.html): argument structure, parser behavior and resource caveats.
- [Python 3.12 tokenize documentation](https://docs.python.org/3.12/library/tokenize.html): source encoding and tokenizer contract.
- The tests exercise pure parsing, source/goal mismatch, parameter categories, malformed/unsupported input, explicit resource refusals, exception propagation and non-execution controls. They are not real-provider efficacy or a complete hostile-parser qualification.

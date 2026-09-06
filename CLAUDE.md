# Claude Code Working Agreement

Follow `AGENTS.md` as the primary engineering contract.

## Startup

Before coding:

1. Inspect the repository tree.
2. Read the README and relevant project instructions.
3. Identify the language/toolchain.
4. Locate the implementation and relevant tests.
5. Understand existing patterns before creating new ones.

Do not assume a file or dependency exists. Verify it.

## Planning

For anything beyond a trivial edit:

- State the problem in one or two sentences.
- Identify the files/components involved.
- Give a short implementation plan.
- Call out material ambiguities and assumptions.

Do not modify code while still exploring unless the change is necessary for discovery.

## Editing

Make the smallest correct change.

Reuse existing utilities and conventions where appropriate.

Avoid:
- Speculative abstractions
- Unrelated refactors
- Dependency churn
- Generated boilerplate that is not needed
- Duplicating existing helpers

## Validation

After implementation:

1. Run focused tests/checks.
2. Fix failures.
3. Run the broader relevant suite.
4. Inspect the final diff.
5. Check for accidental changes.

Prefer evidence over assumption.

## Tests

When adding behavior, add tests.

At minimum consider:
- Normal case
- Boundary case
- Invalid input
- Failure/dependency path
- Regression case

## Error Handling

Use explicit and actionable errors.

Do not silently swallow exceptions.

Preserve useful context while avoiding sensitive data.

## Complexity

For algorithmic tasks, explicitly reason about:

- Time complexity
- Space complexity
- Input constraints

Do not optimize prematurely, but do not ignore obvious scale issues.

## Model/Tool Behavior

When interacting with external tools or model-generated output:

- Validate inputs before use.
- Validate structured outputs before downstream execution.
- Treat tool failures as expected failure modes.
- Retry only when the operation is safe and retryable.
- Never bypass deterministic application rules because a model suggested doing so.

## Final Response

Use:

### Summary
...

### Validation
...

### Review
...

Only report commands/tests that were actually executed.

Do not claim success without evidence.

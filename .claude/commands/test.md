# Test

Inspect the current implementation and determine what tests are missing.

Consider:

- Happy path
- Boundary conditions
- Invalid inputs
- Failure paths
- Dependency/tool failures
- Retry/idempotency behavior where relevant
- Regression coverage
- Large-input behavior where relevant

Add only the necessary tests.

Then run:
1. The most targeted tests.
2. The broader relevant suite.

Report exactly which commands were run and the results.

Do not weaken existing tests just to make the implementation pass.

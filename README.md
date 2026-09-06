# Agentic Coding Setup

This package contains a domain-agnostic coding-agent setup.

## Projects

- [`handoff_copilot/`](handoff_copilot/README.md) — ER Shift-Handoff Copilot,
  a multi-agent system (FastAPI, LangGraph, Pinecone RAG, a real A2A service,
  OpenTelemetry, guardrails, evals). See
  [`handoff_copilot/docs/DESIGN.md`](handoff_copilot/docs/DESIGN.md) for the
  problem statement, architecture, and design decisions.

## Files

- `AGENTS.md` — primary engineering behavior and safety contract.
- `CLAUDE.md` — Claude Code-specific working agreement.
- `.claude/commands/plan.md` — structured planning command.
- `.claude/commands/review.md` — senior-engineer code review command.
- `.claude/commands/test.md` — test-generation and validation command.

## Intended workflow

Use the setup as a disciplined loop:

Understand
→ Inspect
→ Plan
→ Implement
→ Test
→ Review
→ Refine
→ Validate

Example sequence:

1. Start the agent.
2. Let it inspect the repository.
3. Use `/plan` for a non-trivial task.
4. Discuss assumptions/ambiguities with the user.
5. Implement incrementally.
6. Use `/test` while developing.
7. Use `/review` before the final answer.
8. Inspect the final diff.
9. Run the relevant tests.
10. Make a small logical commit when appropriate.

## Why this is domain-agnostic

The setup intentionally does not contain:
- Payment-system implementations
- Rate limiter implementations
- Feature flag implementations
- Queue implementations
- Pre-written solutions
- Question-pattern detectors





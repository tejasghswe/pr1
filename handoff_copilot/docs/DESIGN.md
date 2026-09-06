# ER Shift-Handoff Copilot — Design Document

## 1. Problem Statement

Shift handoffs in an ER are a well-known patient-safety risk: the outgoing
doctor knows the patient, the incoming doctor doesn't, and the only transfer
mechanism is a rushed verbal summary plus a chart the incoming doctor rarely
has time to fully re-read. Critical details — a trending lab, a drug
interaction, an allergy noted three notes ago — are easy to miss under time
pressure.

**Goal:** while a shift is in progress, let the outgoing doctor log short
freeform notes on a patient. At shift change, automatically retrieve that
patient's history, synthesize it into a structured SBAR (Situation,
Background, Assessment, Recommendation) brief, surface anything clinically
critical as an explicit flag, and let the incoming doctor ask grounded,
cited follow-up questions instead of re-reading the whole chart.

The system has to be trustworthy enough for that setting, which drives most
of the design below: everything the model produces is checked against
something deterministic before it reaches a doctor, and every failure mode
(a slow dependency, a hallucinated drug name, injected text in a note) has an
explicit, safe handling path rather than being assumed away.

### Non-goals (explicitly out of scope)

- Real EHR integration, multi-patient dashboards, auth/multi-tenant support.
- General-purpose PII de-identification (NER-based) — see [Limitations](#8-known-limitations--tradeoffs).
- A production-grade clinical drug-interaction database — the interaction
  table here is illustrative, not a substitute for a licensed one.

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + `async def` everywhere | Native async I/O; `asyncio.gather` used where two calls have no data dependency on each other |
| Validation / structured output | Pydantic v2 | API request/response schemas *and* the contract for LLM structured output (`with_structured_output`) and the A2A message payload |
| Vector store | Pinecone, **integrated (server-side) embeddings** | Only `ANTHROPIC_API_KEY`/`PINECONE_API_KEY` were available — Pinecone's hosted embedding model (`llama-text-embed-v2`) avoids needing a third API key/provider just for embeddings |
| Orchestration | LangChain (`ChatAnthropic`, prompt templates, structured output) + LangGraph (`StateGraph`) | LangChain for the model/prompt plumbing, LangGraph for the actual control flow (retry loop, node sequencing) |
| LLM | Anthropic Claude (`claude-sonnet-5` default, configurable) | Already the only configured provider in this environment |
| Distributed boundary | `a2a-sdk` (real Agent2Agent protocol, JSON-RPC) | The Red-Flag checker runs as an independent FastAPI+A2A process, called over the network with a real timeout/retry/degrade contract |
| Observability | OpenTelemetry (`opentelemetry-sdk`, manual instrumentation) | Spans around FastAPI requests, each LangGraph node, and the A2A call; trace context is propagated across the A2A hop |
| Streaming UI feedback | Server-Sent Events (`sse-starlette`) + vanilla JS/HTML/CSS frontend | No build step; the frontend is a demo surface, not a product |
| Tests | `pytest` + `pytest-asyncio`, all agents/network stubbed via `monkeypatch` | Tests exercise orchestration/rule logic, never real Pinecone/Anthropic/A2A |
| Evals | Custom harness (`evals/run_evals.py`) against a 12-case golden set | Rule-based faithfulness/recall scoring is authoritative; an LLM-as-judge score is advisory only |

## 3. Architecture

```
                    ┌─────────────────────────────────────────┐
Dr. A --POST /notes-->  FastAPI app (app/)                    │
                    │   - scrub PII, flag prompt injection     │
                    │   - Pinecone upsert (per-patient ns)     │
                    └─────────────────────────────────────────┘

Dr. B --GET /handoff/{id}--> SSE stream of AG-UI-style events
                    │
                    ▼
          LangGraph:  retrieve -> generate -> guardrail -> respond
                              │        │           ↑  (retry on fail,
                              │        │           │   bounded)
                              ▼        ▼           │
                        Summarizer  Red-Flag ──────┘
                        (LangChain/  Client
                         Anthropic)     │
                                        │ A2A (JSON-RPC over HTTP,
                                        │ timeout + 1 retry + degrade)
                                        ▼
                          redflag_service/  (separate process, port 9001)
                          deterministic rules: vitals, drug interactions,
                          allergy conflicts, pending-critical results
```

### Request flow: `POST /notes`

1. Validate against `PatientNote` (Pydantic): `patient_id`, `author`,
   `note_type`, `text` (1–4000 chars).
2. `security.detect_prompt_injection` scans for known injection phrasing.
   **Flag-and-log only, never reject** — see the rationale in
   [§6 Security](#6-security--guardrails).
3. `security.scrub_pii` redacts SSNs, emails, phone numbers, and long
   numeric IDs before the text goes anywhere near Pinecone or an LLM prompt.
4. A `note_id` (UUID) is assigned and the note is upserted into Pinecone
   under a namespace equal to the `patient_id`, with author/type/timestamp
   as metadata.

### Request flow: `GET /handoff/{patient_id}` (SSE)

Runs the LangGraph below, streaming an `AgentEvent` for every step start/
completion/error and every red flag found, then a final `result` event with
the full `HandoffResponse`.

**Graph nodes:**

| Node | Does | Depends on |
|---|---|---|
| `retrieve` | RAG query against the patient's Pinecone namespace for a broad, handoff-shaped query | — |
| `generate` | Runs `summarizer.summarize()` and `redflag_client.check_red_flags()` **concurrently** via `asyncio.gather` — neither depends on the other's output, only on `retrieve`'s | `retrieve` |
| `guardrail` | Checks the summary for invented drugs/doses against the source notes | `generate` (or `resummarize`) |
| `resummarize` | Re-runs the summarizer with the guardrail's rejection reasons fed back as context | `guardrail` (conditional edge, only on failure) |
| `respond` | Assembles the final `HandoffResponse`, closes the event stream | `guardrail` (conditional edge, on pass or attempts exhausted) |

The retry loop is bounded by `settings.max_summarize_attempts` (default 2) —
it cannot spin forever; if the summarizer keeps failing the guardrail, the
graph still returns a response, just with `guardrail.passed = False` so the
doctor can see that.

### Request flow: `POST /ask`

A lighter-weight sibling of the handoff flow: retrieve notes relevant to the
specific question, generate a grounded answer (same "cite the notes you were
actually given" contract as the summarizer), run it through the same
guardrail. Not streamed — it's a single round-trip, not a multi-step process
worth narrating step-by-step.

## 4. Agent Design

Three small agents, each with one job:

- **Retriever** (`app/agents/retriever.py`): thin wrapper over
  `vectorstore.search_notes`, adds an OTel span. No business logic.
- **Summarizer** (`app/agents/summarizer.py`): the only agent allowed to
  produce free prose. Constrained via:
  - A system prompt that explicitly frames the notes as untrusted data,
    not instructions (see [§6](#6-security--guardrails)).
  - Structured output (`with_structured_output`) to a Pydantic model
    containing *only* the four SBAR text fields — no citation field. That
    means citations can't be hallucinated, because the model is never
    asked to produce them.
  - `source_note_ids` is set in code, not by the model: it's always exactly
    the list of notes actually passed into the prompt.
- **Red-Flag Checker** (`redflag_service/`): deliberately **not** an LLM
  agent. It's a pure rule engine (regex + static interaction/allergy
  tables) exposed over A2A. See [§5](#5-the-a2a-boundary) and
  [§6](#6-security--guardrails) for why.

## 5. The A2A Boundary

The Red-Flag checker is the project's one deliberate distributed-systems
boundary: a real second process, reachable only over the network, using the
actual [A2A protocol](https://a2a-protocol.org) (`a2a-sdk`) rather than a
fake stand-in.

That buys real failure modes, and the client (`app/agents/redflag_client.py`)
handles them explicitly rather than assuming the network always works:

- **Timeout**: `settings.redflag_timeout_seconds` (default 5s) on the httpx
  client backing the A2A call.
- **Retry**: one bounded retry (`settings.redflag_max_retries`, default 1)
  with a short backoff — enough to ride out a transient blip, not enough to
  hang the request.
- **Graceful degrade**: if every attempt fails, the orchestrator does *not*
  fail the whole handoff. It returns a synthetic `service_degraded` flag
  telling the doctor to manually review meds/allergies/vitals, and the SBAR
  summary still gets generated and delivered. A missing safety check is
  itself something the doctor needs to know about — silently proceeding, or
  blocking the whole handoff, are both worse than saying "this check
  didn't run."
- **Malformed-response protection**: the client validates whatever comes
  back against the shared `RedFlag` Pydantic schema before using it — a
  cross-process payload is untrusted input, same as anything from a user.

**Trace propagation across the boundary**: `a2a-sdk`'s client interceptor
hook (`ClientCallInterceptor`) didn't expose raw HTTP headers in the
installed version, so instead of fighting the library for transport-level
header injection, the orchestrator embeds a W3C `traceparent` directly in
the JSON payload it already sends (`RedFlagCheckRequest.trace_context`). The
red-flag service extracts it and starts its span as a child of that context.
Pragmatic, but genuinely correct: both processes' spans land in the same
trace, which is the actual goal.

## 6. Security & Guardrails

Free-text clinical notes are the attack surface. Three separate, deliberately
rule-based (not model-judged) mechanisms, because an LLM should never be the
thing deciding whether its own — or another LLM's — output is safe:

1. **PII scrubbing** (`security.scrub_pii`) — regex redaction of SSNs,
   emails, phone numbers, and long numeric IDs (≥9 digits, so ages/doses/
   vitals are untouched), applied *before* anything reaches Pinecone or a
   prompt.
2. **Prompt-injection handling** — two layers:
   - *Detection* (`security.detect_prompt_injection`): regex-matches known
     injection phrasing on ingest, logs and flags it on the OTel span. It
     does **not** reject the note. A false positive here means silently
     dropping real clinical documentation, which is a worse safety outcome
     than the residual risk — this project treats overly-aggressive
     filtering of medical records as unacceptable.
   - *Neutralization* (the actual defense): every prompt that includes note
     text wraps it in an explicit preamble (`security.UNTRUSTED_DATA_PREAMBLE`)
     telling the model the notes are data to summarize, never instructions
     to follow, regardless of what they contain.
3. **Output guardrail** (`security.validate_summary_against_sources`) —
   after the summarizer runs, every medication and dose mentioned in its
   output is checked against the literal source note text (case-insensitive,
   modulo a small brand/generic synonym table — see below). A miss fails the
   guardrail and triggers the bounded retry described in §3. This is what
   actually caught two real hallucination-shaped issues during eval
   development:
   - The model routinely expands informal note shorthand to formal names
     (source: "nitro", summary: "nitroglycerin") — the *same* drug, so this
     was made an explicit synonym-equivalence check rather than a false
     positive.
   - Before that fix, the guardrail correctly rejected the too-strict
     mismatch and forced a retry — proof the mechanism works, not just that
     the bug existed.

## 7. Observability

Manual OpenTelemetry instrumentation (`telemetry.py`), not auto-instrumentation
contrib packages — deliberately, so it's obvious exactly what's traced:
spans around each FastAPI route handler, each LangGraph node
(`retriever.retrieve_notes`, `summarizer.summarize`, `a2a.check_red_flags`,
the guardrail check), and inside the red-flag service around rule
evaluation. A `SimpleSpanProcessor` + `ConsoleSpanExporter` is the default
(swap in `OTEL_EXPORTER_OTLP_ENDPOINT` for a real collector) — batching was
deliberately avoided for the console path since a background batching thread
outliving a short-lived process (e.g. a test run) is what causes noisy
shutdown errors, and local dev visibility doesn't need the throughput.

## 8. Evals

`evals/run_evals.py` runs the real Summarizer against a 12-case synthetic
golden set (`evals/golden_set.json`), bypassing Pinecone and the A2A hop so
it's fast and network-flake-free. Two scores, kept separate on purpose:

- **Faithfulness** — same deterministic guardrail as production. Authoritative.
- **Recall** — did the rule engine catch every red-flag type a case expects? Authoritative.
- **LLM-as-judge completeness (1-5)** — collected, printed, but **never** used
  to decide pass/fail. An LLM grading its own kind of output is not a
  correctness signal this project treats as ground truth.

Current golden-set score: **12/12 faithfulness, 12/12 recall**. It got there
by finding and forcing fixes to two real bugs (see §6) — the harness earned
its place in the build rather than being a formality.

## 9. Known Limitations / Tradeoffs

| Limitation | Why accepted | What a production version would need |
|---|---|---|
| PII scrubbing is regex-based | Matches this project's size; deterministic and testable | NER-based de-identification (e.g. a clinical NLP model) |
| Red-flag rules are a hand-written table | Demonstrates the pattern; keeps the safety-critical path deterministic and inspectable | A maintained clinical drug-interaction/allergy-class database |
| A2A trace context rides in the app payload, not transport headers | `a2a-sdk`'s installed client interceptor didn't expose raw headers | Either a header-level interceptor once the SDK supports it, or an OTel-native A2A integration |
| `/ask` isn't streamed | Single grounded round-trip doesn't need step-by-step narration | N/A — deliberate scope choice, not a gap |
| `EventBus` is in-process/per-request | Fine for a single demo user | A multi-user deployment needs a broker-backed stream, not an in-process `asyncio.Queue` |
| Single patient / no auth / no real EHR | Explicit non-goal (see §1) | Out of scope by design |

## 10. API Reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/notes` | `POST` | Log a freeform note for a patient (validated, PII-scrubbed, embedded into Pinecone) |
| `/handoff/{patient_id}` | `GET` (SSE) | Stream the handoff generation: step events, red flags as they're found, then the final `HandoffResponse` |
| `/ask` | `POST` | Ask a grounded, cited follow-up question about one patient |
| `/docs` | `GET` | Auto-generated OpenAPI/Swagger UI |
| `/` | `GET` | The demo frontend |

See [README.md](../README.md) for setup and run instructions.

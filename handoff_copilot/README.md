# ER Shift-Handoff Copilot

Doctors log short freeform notes on a patient during their shift. When the
next shift starts, a small multi-agent system retrieves the patient's
history, generates a structured SBAR handoff brief, flags anything clinically
critical, and lets the incoming doctor ask grounded, cited follow-up
questions — all traced, evaluated, and guarded against bad or hallucinated
output.

Single patient, no real EHR integration, synthetic data — a small project
that still exercises every piece for real (RAG, multi-agent orchestration,
a genuine cross-process A2A call, tracing, guardrails, evals).

See [docs/DESIGN.md](docs/DESIGN.md) for the full problem statement, tech
stack rationale, architecture, agent/guardrail design, and known limitations.
This README covers setup and day-to-day usage.

## Architecture

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

- **Retriever**: RAG over Pinecone, one namespace per patient, using
  Pinecone's *integrated* (server-side) embedding model — no separate
  embedding provider needed.
- **Summarizer**: LangChain (`ChatAnthropic` + `with_structured_output`)
  produces the SBAR prose; citations (`source_note_ids`) are assembled in
  code from the notes actually retrieved, never taken from the model's own
  claims.
- **Red-Flag Checker**: a *separate process* speaking the real A2A protocol
  (`a2a-sdk`). 100% deterministic rules — no LLM in the loop — because this
  is the safety net the incoming doctor needs to be able to trust without
  re-reading the chart.
- **Guardrail**: after summarizing, every drug/dose mentioned is checked
  against the source notes (modulo a small brand/generic synonym table). A
  failure triggers one bounded retry of the summarizer with the rejection
  reason fed back as context.
- **OpenTelemetry**: manual spans around FastAPI requests, each LangGraph
  node, and the A2A call. Trace context crosses the A2A process boundary via
  a W3C traceparent embedded in the JSON payload (the A2A client library
  doesn't expose raw HTTP headers for this), so both processes' spans land
  in one trace.

## Setup

```bash
cd handoff_copilot
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Set `ANTHROPIC_API_KEY` and `PINECONE_API_KEY` in the `.env` file at the repo
root (one level up from this directory — see `.env.example` there).

### Windows + TLS-inspecting antivirus (AVG, Kaspersky, Zscaler, etc.)

If Pinecone calls fail with `CERTIFICATE_VERIFY_FAILED`, your security
software is re-signing HTTPS traffic with a locally-installed root
certificate that Python's `certifi` bundle doesn't know about. This project
works around it in `app/certs.py` by building a merged CA bundle (certifi +
the Windows certificate store) — it runs automatically, nothing to do. See
that file's docstring for why we don't use `pip-system-certs` instead (it
conflicts with the `anthropic` SDK's own TLS handling).

## Running it

Two processes:

```bash
# Terminal 1 — the red-flag A2A service
python -m redflag_service.server

# Terminal 2 — the orchestrator (also serves the frontend at /)
python -m uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000/ for the demo UI, or use the API directly
(docs at http://127.0.0.1:8000/docs):

```bash
curl -X POST http://127.0.0.1:8000/notes -H "Content-Type: application/json" -d '{
  "patient_id": "bed-12", "author": "dr_a", "note_type": "progress",
  "text": "68M, chest pain resolved, gave nitro, trending troponin, BP 190/125, HR 140"
}'

curl -N http://127.0.0.1:8000/handoff/bed-12   # streams AG-UI-style events, then the final brief

curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" -d '{
  "patient_id": "bed-12", "question": "any allergy concerns?"
}'
```

Stop the red-flag service and repeat the `/handoff` call to see the graceful
degrade path: the handoff still completes, with a `service_degraded` flag
telling the doctor to review meds/allergies/vitals manually.

## Tests

```bash
python -m pytest -q
```

45 tests, all offline — the LLM, Pinecone, and A2A calls are stubbed via
`monkeypatch` (see `tests/test_graph.py` for the orchestration/retry-loop
tests, `tests/test_redflag_rules.py` for the deterministic rule engine).

## Evals

```bash
python -m evals.run_evals
```

Runs the real Summarizer (one Anthropic call per case) and the rule engine
against `evals/golden_set.json` (12 synthetic timelines), bypassing Pinecone
and the A2A hop entirely so it's fast and network-flake-free. Reports two
scores, kept deliberately separate:

- **Faithfulness** — the same deterministic guardrail used in production:
  did the summary invent a drug/dose not in the source notes?
- **Recall** — did the rule engine catch every red-flag type the case
  expects?

An LLM-as-judge completeness rating (1-5) is also collected but is
*advisory only* — it never decides pass/fail, per the rule that an LLM must
not be the final source of truth for its own correctness.

This harness earned its keep during development: it caught two real bugs
that unit tests missed — a greedy allergy-name regex that swallowed trailing
words ("penicillin per chart" instead of "penicillin"), and a guardrail
false-positive on the note-shorthand-to-formal-name expansion the summarizer
does by default (source says "nitro", summary says "nitroglycerin" — the
same drug, but not a verbatim match). Both are fixed in the current code;
the golden set now scores 12/12 on both faithfulness and recall.

## Known limitations / assumptions

- **Vector store**: Pinecone with integrated (server-side) embeddings, not a
  separately-hosted embedding model — the project only had Pinecone/Anthropic
  keys available, and Pinecone's integrated inference avoids needing a third
  provider.
- **PII scrubbing** is regex-based (SSN, email, phone, long numeric IDs), not
  general NER — a deliberate scope tradeoff, not general-purpose de-identification.
  Prompt injection detection is flag-and-log-only, not reject-on-match: a false
  positive silently dropping real clinical documentation is worse than the
  residual risk, given note text is always wrapped as clearly-delimited
  untrusted data in every LLM prompt regardless.
- **Red-flag coverage** is whatever's in `redflag_service/rules.py`'s pattern
  and interaction tables — real-world coverage would need a maintained
  clinical drug-interaction/allergy-class database, not a hand-written list.
- **A2A trace propagation** rides in the application payload (a `trace_context`
  field), not real A2A/HTTP headers — `a2a-sdk`'s client interceptor hook
  didn't expose raw transport headers in the installed version, so this was
  the pragmatic way to get one correlated trace across the process boundary.
- **/ask is not streamed** (unlike `/handoff`) — it's a single grounded
  Q&A round-trip, which didn't need the AG-UI step-by-step treatment.
- Single-process in-memory `EventBus` per handoff request — fine for one
  demo user; a multi-user deployment would need a per-request-scoped stream
  that doesn't rely on an in-process queue.

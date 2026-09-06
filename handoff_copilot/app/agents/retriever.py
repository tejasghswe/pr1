"""Retriever agent: RAG over a patient's namespace in Pinecone."""
from __future__ import annotations

from app import vectorstore
from app.config import settings
from telemetry import get_tracer
from schemas import StoredNote

_tracer = get_tracer("er-shift-handoff.retriever")

# Used to seed a broad, representative retrieval when generating a full
# handoff (as opposed to answering a specific follow-up question).
HANDOFF_QUERY = (
    "current status, active problems, vital signs, medications given, "
    "allergies, pending or critical lab results, family communication, plan"
)


async def retrieve_notes(patient_id: str, query: str, top_k: int | None = None) -> list[StoredNote]:
    with _tracer.start_as_current_span("retriever.retrieve_notes") as span:
        span.set_attribute("patient_id", patient_id)
        span.set_attribute("query", query)
        notes = await vectorstore.search_notes(
            patient_id, query, top_k or settings.retrieval_top_k
        )
        span.set_attribute("notes_found", len(notes))
        return notes

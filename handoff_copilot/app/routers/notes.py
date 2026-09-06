from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter

from app import vectorstore
from app.security import detect_prompt_injection, scrub_pii
from telemetry import get_tracer
from schemas import PatientNote, StoredNote

router = APIRouter()
logger = logging.getLogger("handoff.notes")
_tracer = get_tracer("er-shift-handoff.notes")


@router.post("/notes", response_model=StoredNote, status_code=201)
async def create_note(note: PatientNote) -> StoredNote:
    with _tracer.start_as_current_span("notes.create") as span:
        span.set_attribute("patient_id", note.patient_id)

        injected = detect_prompt_injection(note.text)
        if injected:
            logger.warning(
                "Note flagged for possible prompt injection: patient_id=%s phrases=%s",
                note.patient_id, injected,
            )
            span.set_attribute("injection_flagged", True)

        clean_text = scrub_pii(note.text)

        stored = StoredNote(
            note_id=str(uuid.uuid4()),
            patient_id=note.patient_id,
            author=note.author,
            note_type=note.note_type,
            text=clean_text,
            created_at=datetime.now(timezone.utc),
        )
        await vectorstore.upsert_note(stored)
        return stored

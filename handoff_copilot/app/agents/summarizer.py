"""Summarizer agent: turns retrieved notes into an SBAR summary.

Citations (source_note_ids) are assembled in code from the notes we actually
passed to the model, never taken from the model's own output — an LLM
asserting "I cited note X" is not evidence that it did, and AGENTS.md is
explicit that an LLM must not be the final source of truth for that kind of
bookkeeping.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.llm import get_chat_model
from app.security import UNTRUSTED_DATA_PREAMBLE
from telemetry import get_tracer
from schemas import SBARSummary, StoredNote

_tracer = get_tracer("er-shift-handoff.summarizer")

_SYSTEM_PROMPT = f"""You are an ER shift-handoff assistant. Produce a concise \
SBAR (Situation, Background, Assessment, Recommendation) summary for the \
incoming doctor, grounded strictly in the notes provided.

{UNTRUSTED_DATA_PREAMBLE}

Rules:
- Only mention medications, doses, vitals, or results that literally appear \
in the notes below. Do not infer a dose that isn't stated.
- If information for a section is missing, say so plainly instead of \
guessing.
- Keep each section to 1-3 sentences.
"""

_HUMAN_TEMPLATE = """Patient notes (chronological):
{notes_block}
{feedback_block}
Produce the SBAR summary now."""


class _RawSBARFields(BaseModel):
    """What we let the model invent: prose only, no citations."""

    situation: str = Field(description="What is happening with the patient right now.")
    background: str = Field(description="Relevant history and context from prior notes.")
    assessment: str = Field(description="Clinical assessment based on the notes.")
    recommendation: str = Field(description="Recommended next steps for the incoming doctor.")


def _format_notes(notes: list[StoredNote]) -> str:
    if not notes:
        return "(no notes on file for this patient)"
    lines = []
    for note in notes:
        lines.append(
            f"--- NOTE {note.note_id} | {note.author} | {note.note_type.value} | "
            f"{note.created_at.isoformat()} ---\n{note.text}"
        )
    return "\n".join(lines)


async def summarize(notes: list[StoredNote], feedback: list[str] | None = None) -> SBARSummary:
    with _tracer.start_as_current_span("summarizer.summarize") as span:
        span.set_attribute("notes_count", len(notes))

        if not notes:
            return SBARSummary(
                situation="No prior notes on file for this patient.",
                background="No prior notes on file for this patient.",
                assessment="Insufficient data to assess.",
                recommendation="Confirm patient identity and begin a fresh assessment.",
                source_note_ids=[],
            )

        feedback_block = ""
        if feedback:
            feedback_block = (
                "\nYour previous attempt was rejected for these reasons — "
                "regenerate without repeating them:\n- " + "\n- ".join(feedback) + "\n"
            )

        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("human", _HUMAN_TEMPLATE)]
        )
        model = get_chat_model().with_structured_output(_RawSBARFields)
        chain = prompt | model

        raw: _RawSBARFields = await chain.ainvoke(
            {"notes_block": _format_notes(notes), "feedback_block": feedback_block}
        )

        summary = SBARSummary(
            situation=raw.situation,
            background=raw.background,
            assessment=raw.assessment,
            recommendation=raw.recommendation,
            source_note_ids=[note.note_id for note in notes],
        )
        span.set_attribute("source_note_ids", ",".join(summary.source_note_ids))
        return summary

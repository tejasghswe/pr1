"""Answers a specific follow-up question grounded in retrieved notes, e.g.
"any allergy concerns?" — same faithfulness contract as the summarizer:
citations are the notes actually retrieved and passed to the model, not
whatever the model claims it used.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.agents.retriever import retrieve_notes
from app.llm import get_chat_model
from app.security import UNTRUSTED_DATA_PREAMBLE, validate_summary_against_sources
from telemetry import get_tracer
from schemas import AskResponse, StoredNote

_tracer = get_tracer("er-shift-handoff.ask")

_SYSTEM_PROMPT = f"""You are an ER shift-handoff assistant answering a \
follow-up question from the incoming doctor about one patient.

{UNTRUSTED_DATA_PREAMBLE}

Rules:
- Answer only from the notes provided below.
- Only mention medications, doses, vitals, or results that literally appear \
in the notes.
- If the notes don't contain enough information to answer, say so plainly.
- Be concise: 1-4 sentences.
"""

_HUMAN_TEMPLATE = """Patient notes:
{notes_block}

Question: {question}"""


class _RawAskFields(BaseModel):
    answer: str = Field(description="The grounded answer to the doctor's question.")


def _format_notes(notes: list[StoredNote]) -> str:
    if not notes:
        return "(no notes on file for this patient)"
    return "\n".join(
        f"--- NOTE {n.note_id} | {n.author} | {n.note_type.value} | {n.created_at.isoformat()} ---\n{n.text}"
        for n in notes
    )


async def answer_question(patient_id: str, question: str) -> AskResponse:
    with _tracer.start_as_current_span("ask.answer_question") as span:
        span.set_attribute("patient_id", patient_id)

        notes = await retrieve_notes(patient_id, question)

        if not notes:
            answer_text = "No notes on file for this patient to answer that from."
            return AskResponse(
                answer=answer_text,
                citations=[],
                guardrail=validate_summary_against_sources(answer_text, []),
            )

        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("human", _HUMAN_TEMPLATE)]
        )
        model = get_chat_model().with_structured_output(_RawAskFields)
        chain = prompt | model

        raw: _RawAskFields = await chain.ainvoke(
            {"notes_block": _format_notes(notes), "question": question}
        )

        source_texts = [n.text for n in notes]
        guardrail = validate_summary_against_sources(raw.answer, source_texts)
        span.set_attribute("guardrail_passed", guardrail.passed)

        return AskResponse(
            answer=raw.answer,
            citations=[n.note_id for n in notes],
            guardrail=guardrail,
        )

"""Shared Pydantic contracts used by the orchestrator, the red-flag A2A
service, and the eval harness. Keeping these in one place means the A2A
message payload between the two processes is validated against the exact
same schema on both ends instead of two hand-maintained copies drifting
apart.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NoteType(str, Enum):
    PROGRESS = "progress"
    NURSING = "nursing"
    VITALS = "vitals"
    LABS = "labs"
    FAMILY = "family"
    OTHER = "other"


class PatientNote(BaseModel):
    """A freeform note as submitted by a doctor via POST /notes."""

    patient_id: str = Field(min_length=1, max_length=64)
    author: str = Field(min_length=1, max_length=128)
    note_type: NoteType = NoteType.PROGRESS
    text: str = Field(min_length=1, max_length=4000)


class StoredNote(BaseModel):
    """A note as persisted in the vector store (post scrub, with an id)."""

    note_id: str
    patient_id: str
    author: str
    note_type: NoteType
    text: str
    created_at: datetime = Field(default_factory=_utcnow)


class RedFlagSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RedFlagType(str, Enum):
    VITAL_SIGN = "vital_sign"
    DRUG_INTERACTION = "drug_interaction"
    ALLERGY_CONFLICT = "allergy_conflict"
    PENDING_CRITICAL_RESULT = "pending_critical_result"
    SERVICE_DEGRADED = "service_degraded"


class RedFlag(BaseModel):
    type: RedFlagType
    severity: RedFlagSeverity
    description: str
    evidence: str = Field(
        description=(
            "The verbatim text that triggered this flag, quoted from the source "
            "note(s) (for cross-note correlations, e.g. a drug interaction, "
            "this may quote more than one note's matched text)."
        )
    )
    source_note_ids: list[str] = Field(default_factory=list)


class SBARSummary(BaseModel):
    situation: str
    background: str
    assessment: str
    recommendation: str
    source_note_ids: list[str] = Field(default_factory=list)


class GuardrailResult(BaseModel):
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class AgentEventType(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    ERROR = "error"
    FLAG = "flag"


class AgentEvent(BaseModel):
    """One AG-UI-style step event streamed to the frontend."""

    step: str
    type: AgentEventType
    detail: str
    timestamp: datetime = Field(default_factory=_utcnow)


class HandoffResponse(BaseModel):
    patient_id: str
    summary: SBARSummary
    red_flags: list[RedFlag]
    red_flag_service_degraded: bool = False
    guardrail: GuardrailResult


class AskRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=500)


class AskResponse(BaseModel):
    answer: str
    citations: list[str]
    guardrail: GuardrailResult


class RedFlagCheckRequest(BaseModel):
    """Payload sent over A2A from the orchestrator to the red-flag service."""

    patient_id: str
    notes: list[StoredNote]
    trace_context: dict[str, str] = Field(default_factory=dict)


class RedFlagCheckResponse(BaseModel):
    red_flags: list[RedFlag]

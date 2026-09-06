"""Tests the LangGraph wiring itself: the guardrail retry loop, the retry
bound, and how a degraded red-flag service surfaces in the final response.
All three agents are stubbed so this never makes a real Pinecone/Anthropic/
A2A call — it's testing orchestration logic, not the agents.
"""
from datetime import datetime, timezone

import pytest

from app import graph as graph_module
from app.agents import redflag_client, retriever, summarizer
from app.config import settings
from app.events import EventBus
from schemas import NoteType, RedFlag, RedFlagSeverity, RedFlagType, SBARSummary, StoredNote

_NOTE = StoredNote(
    note_id="n1", patient_id="p1", author="dr_a", note_type=NoteType.PROGRESS,
    text="68M chest pain resolved", created_at=datetime.now(timezone.utc),
)


def _summary(text: str = "stable") -> SBARSummary:
    return SBARSummary(situation=text, background=text, assessment=text, recommendation=text)


@pytest.fixture(autouse=True)
def stub_retriever(monkeypatch):
    async def fake_retrieve(patient_id, query, top_k=None):
        return [_NOTE]
    monkeypatch.setattr(retriever, "retrieve_notes", fake_retrieve)


@pytest.fixture(autouse=True)
def stub_redflag_no_flags(monkeypatch):
    async def fake_check(patient_id, notes):
        return [], False
    monkeypatch.setattr(redflag_client, "check_red_flags", fake_check)


async def test_passing_summary_does_not_retry(monkeypatch):
    calls = []

    async def fake_summarize(notes, feedback=None):
        calls.append(feedback)
        return _summary("Patient is stable and resting comfortably.")

    monkeypatch.setattr(summarizer, "summarize", fake_summarize)

    events = EventBus()
    response = await graph_module.run_handoff("p1", "query", events)

    assert response.guardrail.passed
    assert len(calls) == 1  # no retry needed


async def test_guardrail_failure_triggers_one_retry_then_passes(monkeypatch):
    calls = []

    async def fake_summarize(notes, feedback=None):
        calls.append(feedback)
        if len(calls) == 1:
            return _summary("Gave aspirin for chest pain.")  # not in source notes -> guardrail fails
        return _summary("Patient is stable.")  # clean on retry

    monkeypatch.setattr(summarizer, "summarize", fake_summarize)

    events = EventBus()
    response = await graph_module.run_handoff("p1", "query", events)

    assert len(calls) == 2
    assert calls[1] is not None and any("aspirin" in r for r in calls[1])
    assert response.guardrail.passed


async def test_guardrail_gives_up_after_max_attempts(monkeypatch):
    calls = []

    async def always_hallucinating(notes, feedback=None):
        calls.append(feedback)
        return _summary("Gave aspirin for chest pain.")

    monkeypatch.setattr(summarizer, "summarize", always_hallucinating)

    events = EventBus()
    response = await graph_module.run_handoff("p1", "query", events)

    # Retries are bounded by settings.max_summarize_attempts, not infinite.
    assert len(calls) == settings.max_summarize_attempts
    assert not response.guardrail.passed
    assert response.summary is not None  # still returns a response, doesn't hang/crash


async def test_degraded_redflag_service_surfaces_in_response(monkeypatch):
    async def fake_summarize(notes, feedback=None):
        return _summary("Patient is stable.")
    monkeypatch.setattr(summarizer, "summarize", fake_summarize)

    async def fake_check_degraded(patient_id, notes):
        degraded_flag = RedFlag(
            type=RedFlagType.SERVICE_DEGRADED, severity=RedFlagSeverity.MEDIUM,
            description="unavailable", evidence="n/a", source_note_ids=[],
        )
        return [degraded_flag], True
    monkeypatch.setattr(redflag_client, "check_red_flags", fake_check_degraded)

    events = EventBus()
    response = await graph_module.run_handoff("p1", "query", events)

    assert response.red_flag_service_degraded is True
    assert response.red_flags[0].type == RedFlagType.SERVICE_DEGRADED


async def test_events_are_emitted_and_closed(monkeypatch):
    async def fake_summarize(notes, feedback=None):
        return _summary("Patient is stable.")
    monkeypatch.setattr(summarizer, "summarize", fake_summarize)

    events = EventBus()
    await graph_module.run_handoff("p1", "query", events)

    collected = [e async for e in events.stream()]
    steps = {e.step for e in collected}
    assert {"retrieve", "summarize", "red_flag_check", "guardrail", "respond"} <= steps

"""LangGraph orchestration for one handoff: retrieve -> generate (summary +
red-flag check, concurrently) -> guardrail -> respond, with a bounded retry
loop back to the summarizer if the guardrail rejects its output.

retrieve and red-flag checking aren't run concurrently with each other (the
red-flag check needs the retrieved notes as input), but summarizing and
red-flag-checking both depend only on the retrieved notes and not on each
other, so *that* pair runs concurrently via asyncio.gather inside the
"generate" node — this is where the real I/O concurrency win is.
"""
from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import redflag_client, retriever, summarizer
from app.config import settings
from app.events import EventBus
from app.security import validate_summary_against_sources
from schemas import (
    AgentEventType,
    GuardrailResult,
    HandoffResponse,
    RedFlag,
    SBARSummary,
    StoredNote,
)


class HandoffState(TypedDict, total=False):
    patient_id: str
    query: str
    notes: list[StoredNote]
    summary: SBARSummary
    red_flags: list[RedFlag]
    red_flag_degraded: bool
    guardrail: GuardrailResult
    attempts: int
    events: EventBus
    response: HandoffResponse


async def _node_retrieve(state: HandoffState) -> HandoffState:
    events = state["events"]
    events.emit("retrieve", AgentEventType.STARTED, "Retrieving patient history...")
    notes = await retriever.retrieve_notes(state["patient_id"], state["query"])
    events.emit(
        "retrieve", AgentEventType.COMPLETED, f"Retrieved {len(notes)} relevant note(s)."
    )
    return {"notes": notes}


async def _node_generate(state: HandoffState) -> HandoffState:
    events = state["events"]
    notes = state["notes"]
    events.emit("summarize", AgentEventType.STARTED, "Drafting SBAR summary...")
    events.emit("red_flag_check", AgentEventType.STARTED, "Checking meds, allergies, and vitals...")

    summary, (red_flags, degraded) = await asyncio.gather(
        summarizer.summarize(notes),
        redflag_client.check_red_flags(state["patient_id"], notes),
    )

    events.emit("summarize", AgentEventType.COMPLETED, "SBAR draft ready.")
    if degraded:
        events.emit(
            "red_flag_check", AgentEventType.ERROR,
            "Red-flag service unavailable — degraded, manual review advised.",
        )
    else:
        events.emit("red_flag_check", AgentEventType.COMPLETED, f"Found {len(red_flags)} flag(s).")
    for flag in red_flags:
        if flag.type.value != "service_degraded":
            events.emit("red_flag_check", AgentEventType.FLAG, f"{flag.severity.value}: {flag.description}")

    return {"summary": summary, "red_flags": red_flags, "red_flag_degraded": degraded, "attempts": 0}


async def _node_resummarize(state: HandoffState) -> HandoffState:
    events = state["events"]
    events.emit("summarize", AgentEventType.STARTED, "Regenerating summary after guardrail rejection...")
    summary = await summarizer.summarize(state["notes"], feedback=state["guardrail"].reasons)
    events.emit("summarize", AgentEventType.COMPLETED, "Revised SBAR draft ready.")
    return {"summary": summary}


async def _node_guardrail(state: HandoffState) -> HandoffState:
    events = state["events"]
    events.emit("guardrail", AgentEventType.STARTED, "Checking summary against source notes...")
    summary = state["summary"]
    source_texts = [n.text for n in state["notes"]]
    summary_text = " ".join(
        [summary.situation, summary.background, summary.assessment, summary.recommendation]
    )
    result = validate_summary_against_sources(summary_text, source_texts)
    attempts = state.get("attempts", 0) + 1
    if result.passed:
        events.emit("guardrail", AgentEventType.COMPLETED, "Summary is faithful to source notes.")
    else:
        events.emit("guardrail", AgentEventType.ERROR, "; ".join(result.reasons))
    return {"guardrail": result, "attempts": attempts}


def _route_after_guardrail(state: HandoffState) -> str:
    if not state["guardrail"].passed and state["attempts"] < settings.max_summarize_attempts:
        return "resummarize"
    return "respond"


async def _node_respond(state: HandoffState) -> HandoffState:
    events = state["events"]
    response = HandoffResponse(
        patient_id=state["patient_id"],
        summary=state["summary"],
        red_flags=state["red_flags"],
        red_flag_service_degraded=state["red_flag_degraded"],
        guardrail=state["guardrail"],
    )
    events.emit("respond", AgentEventType.COMPLETED, "Handoff brief ready.")
    events.close()
    return {"response": response}


def build_graph():
    graph = StateGraph(HandoffState)
    graph.add_node("retrieve", _node_retrieve)
    graph.add_node("generate", _node_generate)
    graph.add_node("resummarize", _node_resummarize)
    graph.add_node("guardrail", _node_guardrail)
    graph.add_node("respond", _node_respond)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "guardrail")
    graph.add_conditional_edges(
        "guardrail", _route_after_guardrail, {"resummarize": "resummarize", "respond": "respond"}
    )
    graph.add_edge("resummarize", "guardrail")
    graph.add_edge("respond", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def run_handoff(patient_id: str, query: str, events: EventBus) -> HandoffResponse:
    graph = get_graph()
    final_state = await graph.ainvoke(
        {"patient_id": patient_id, "query": query, "events": events}
    )
    return final_state["response"]

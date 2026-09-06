from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.agents.retriever import HANDOFF_QUERY
from app.events import EventBus
from app.graph import run_handoff
from telemetry import get_tracer
from schemas import AgentEventType

router = APIRouter()
_tracer = get_tracer("er-shift-handoff.handoff_route")


@router.get("/handoff/{patient_id}")
async def get_handoff(patient_id: str) -> EventSourceResponse:
    events = EventBus()

    async def run():
        with _tracer.start_as_current_span("handoff.run") as span:
            span.set_attribute("patient_id", patient_id)
            try:
                return await run_handoff(patient_id, HANDOFF_QUERY, events)
            except Exception as exc:
                span.record_exception(exc)
                events.emit("respond", AgentEventType.ERROR, f"Handoff failed: {exc}")
                events.close()
                raise

    task = asyncio.create_task(run())

    async def event_generator():
        async for event in events.stream():
            yield {"event": event.type.value, "data": event.model_dump_json()}
        try:
            response = await task
            yield {"event": "result", "data": response.model_dump_json()}
        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"error": str(exc)})}

    return EventSourceResponse(event_generator())

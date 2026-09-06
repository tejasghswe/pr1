from __future__ import annotations

import json
import logging

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from pydantic import ValidationError

from redflag_service.rules import check_red_flags
from telemetry import extract_trace_context, get_tracer
from schemas import RedFlagCheckRequest

logger = logging.getLogger("redflag_service.executor")
_tracer = get_tracer("er-shift-handoff.redflag_service")


class RedFlagAgentExecutor(AgentExecutor):
    """A2A-facing wrapper around the deterministic rule engine in rules.py.

    Protocol handling (tasks, status updates, artifacts) lives here; clinical
    logic stays in rules.py so it can be unit-tested without any A2A/network
    machinery involved.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Checking vitals, meds, and allergies..."),
        )

        raw_text = get_message_text(context.message)
        try:
            request = RedFlagCheckRequest.model_validate_json(raw_text)
        except ValidationError as exc:
            logger.warning("Rejecting malformed red-flag request: %s", exc)
            await task_updater.failed(message=new_text_message(f"invalid request payload: {exc}"))
            return

        parent_ctx = extract_trace_context(request.trace_context)
        with _tracer.start_as_current_span("redflag.check_red_flags", context=parent_ctx) as span:
            span.set_attribute("patient_id", request.patient_id)
            span.set_attribute("notes_count", len(request.notes))
            try:
                flags = check_red_flags(request.notes)
            except Exception as exc:
                span.record_exception(exc)
                logger.exception("Red-flag rule evaluation failed")
                await task_updater.failed(message=new_text_message(f"rule evaluation error: {exc}"))
                return
            span.set_attribute("flags_found", len(flags))

        payload = json.dumps([f.model_dump(mode="json") for f in flags])
        await task_updater.add_artifact(parts=[new_text_part(text=payload, media_type="application/json")])
        await task_updater.complete(message=new_text_message("Red-flag check complete."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")

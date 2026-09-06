"""A tiny AG-UI-style event bus: agents/graph nodes push step events onto it
as they work, and the FastAPI handoff route streams them out as SSE, so the
frontend sees "retrieving history...", "flag: elevated troponin trend", etc.
instead of a black-box spinner while the graph runs.
"""
from __future__ import annotations

import asyncio

from schemas import AgentEvent, AgentEventType


class EventBus:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    def emit(self, step: str, type_: AgentEventType, detail: str) -> None:
        self._queue.put_nowait(AgentEvent(step=step, type=type_, detail=detail))

    def close(self) -> None:
        self._queue.put_nowait(None)

    async def stream(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

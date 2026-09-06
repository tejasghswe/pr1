"""A2A client for the Red-Flag checker service.

This is the project's one deliberate distributed-systems boundary: a real
network call, over the A2A protocol, to a separately-running process. That
means real failure modes — the service can be slow, down, or return garbage —
so this module owns: a bounded timeout, one bounded retry with backoff, and a
graceful degrade path that lets the handoff continue (with an explicit
"couldn't check, review manually" flag) instead of failing the whole request
because one dependency hiccuped.
"""
from __future__ import annotations

import asyncio
import json

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest

from app.config import settings
from telemetry import get_tracer, inject_trace_context
from schemas import RedFlag, RedFlagCheckRequest, RedFlagSeverity, RedFlagType, StoredNote

_tracer = get_tracer("er-shift-handoff.redflag_client")


async def check_red_flags(patient_id: str, notes: list[StoredNote]) -> tuple[list[RedFlag], bool]:
    """Returns (red_flags, degraded). degraded=True means the red-flag
    service was unreachable or misbehaving and we fell back gracefully."""
    with _tracer.start_as_current_span("a2a.check_red_flags") as span:
        span.set_attribute("patient_id", patient_id)
        span.set_attribute("notes_count", len(notes))

        payload = RedFlagCheckRequest(
            patient_id=patient_id,
            notes=notes,
            trace_context=inject_trace_context(),
        )
        message_text = payload.model_dump_json()

        last_error: Exception | None = None
        for attempt in range(settings.redflag_max_retries + 1):
            try:
                flags = await _send_once(message_text)
                span.set_attribute("degraded", False)
                span.set_attribute("attempts_used", attempt + 1)
                return flags, False
            except Exception as exc:
                last_error = exc
                span.record_exception(exc)
                if attempt < settings.redflag_max_retries:
                    await asyncio.sleep(0.3 * (attempt + 1))

        span.set_attribute("degraded", True)
        span.set_attribute("attempts_used", settings.redflag_max_retries + 1)
        degraded_flag = RedFlag(
            type=RedFlagType.SERVICE_DEGRADED,
            severity=RedFlagSeverity.MEDIUM,
            description=(
                "Red-flag checking service was unavailable "
                f"({type(last_error).__name__ if last_error else 'unknown error'}). "
                "Manual review of medications, allergies, and vitals is advised."
            ),
            evidence="n/a",
            source_note_ids=[],
        )
        return [degraded_flag], True


async def _send_once(message_text: str) -> list[RedFlag]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.redflag_timeout_seconds)) as httpx_client:
        client_config = ClientConfig(streaming=False, httpx_client=httpx_client)
        client = await create_client(settings.redflag_service_url, client_config=client_config)
        try:
            message = new_text_message(message_text, role=Role.ROLE_USER)
            request = SendMessageRequest(message=message)

            payload_json: str | None = None
            async for chunk in client.send_message(request):
                text = get_stream_response_text(chunk)
                if not text:
                    continue
                try:
                    json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                payload_json = text  # last chunk that parses as JSON = the artifact

            if payload_json is None:
                raise RuntimeError("red-flag service returned no parseable JSON payload")

            raw_flags = json.loads(payload_json)
            return [RedFlag.model_validate(item) for item in raw_flags]
        finally:
            await client.close()

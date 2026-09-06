"""Entry point for the Red-Flag checker as a standalone A2A service.

Run separately from the main orchestrator:
    python -m redflag_service.server

This is the project's deliberate "distributed" boundary — see
app/agents/redflag_client.py for how the orchestrator calls it (with a
timeout, a bounded retry, and a graceful degrade path if this process is
down or slow).
"""
from __future__ import annotations

import logging

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI

from redflag_service.agent_executor import RedFlagAgentExecutor
from telemetry import configure_telemetry

logging.basicConfig(level=logging.INFO)

SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 9001

configure_telemetry("er-shift-handoff-redflag-service", console_export=True)

_skill = AgentSkill(
    id="check_red_flags",
    name="Check Red Flags",
    description=(
        "Deterministically checks a patient's notes for abnormal vitals, "
        "dangerous drug combinations, allergy conflicts, and pending "
        "critical results."
    ),
    input_modes=["application/json"],
    output_modes=["application/json"],
    tags=["clinical-safety", "red-flags"],
    examples=['{"patient_id": "p1", "notes": [], "trace_context": {}}'],
)

_agent_card = AgentCard(
    name="ER Red-Flag Checker",
    description="Rule-based clinical safety checker for the ER shift-handoff copilot.",
    version="0.1.0",
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    capabilities=AgentCapabilities(streaming=True),
    supported_interfaces=[
        AgentInterface(
            protocol_binding="JSONRPC",
            url=f"http://{SERVICE_HOST}:{SERVICE_PORT}",
            protocol_version="1.0",
        )
    ],
    skills=[_skill],
)

_request_handler = DefaultRequestHandler(
    agent_executor=RedFlagAgentExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=_agent_card,
)

app = FastAPI(title="ER Red-Flag Checker (A2A)")
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(_agent_card),
    jsonrpc_routes=create_jsonrpc_routes(_request_handler, rpc_url="/"),
)


if __name__ == "__main__":
    uvicorn.run(app, host=SERVICE_HOST, port=SERVICE_PORT)

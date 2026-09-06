from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import vectorstore
from app.config import settings
from app.routers import ask, handoff, notes
from telemetry import configure_telemetry

logging.basicConfig(level=logging.INFO)

configure_telemetry(
    settings.otel_service_name,
    console_export=settings.otel_console_export,
    otlp_endpoint=settings.otel_exporter_otlp_endpoint,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    vectorstore.ensure_index()
    yield


app = FastAPI(title="ER Shift-Handoff Copilot", lifespan=lifespan)

app.include_router(notes.router)
app.include_router(handoff.router)
app.include_router(ask.router)

_frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")

"""Minimal manual OpenTelemetry setup shared by the orchestrator and the
red-flag A2A service. We instrument by hand (spans around FastAPI requests,
LangGraph nodes, and the A2A call) rather than pulling in auto-instrumentation
contrib packages, so we know exactly what's traced and keep the dependency
surface small.

Trace context crosses the A2A process boundary manually: the orchestrator
injects a W3C traceparent into the JSON payload it sends the red-flag agent,
and the red-flag service extracts it before opening its own span, so both
processes' spans land in one trace.
"""
from __future__ import annotations

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

_configured = False


def configure_telemetry(service_name: str, *, console_export: bool = True, otlp_endpoint: str | None = None) -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    if console_export:
        # Simple (synchronous, per-span) rather than batched: this exporter
        # exists for local dev visibility, not throughput, and a background
        # batching thread outliving a short-lived process (e.g. pytest) is
        # what causes noisy "I/O operation on closed file" errors at exit.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def inject_trace_context() -> dict[str, str]:
    """Serialize the current trace context to plain strings for an app-level
    payload (used because the A2A client doesn't expose raw HTTP headers)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def extract_trace_context(carrier: dict[str, str]):
    """Rebuild an OTel context from a carrier produced by inject_trace_context."""
    return propagate.extract(carrier)

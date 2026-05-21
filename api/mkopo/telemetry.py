"""OpenTelemetry instrumentation.

Configures tracing and propagation for the FastAPI app and SQLAlchemy engine.
Exports to the console by default; set `OTEL_EXPORTER_OTLP_ENDPOINT` to ship
to a collector (Phoenix, Tempo, Honeycomb, Datadog, etc.).

Wired from `main.py:lifespan` so it runs exactly once at startup.
"""

from __future__ import annotations

import os

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from mkopo.config import get_settings
from mkopo.db import get_engine

logger = structlog.get_logger()

_initialised = False


def setup_telemetry(app: object) -> None:
    """Initialise tracing. Idempotent — safe to call from multiple lifespans."""
    global _initialised
    if _initialised:
        return

    settings = get_settings()
    resource = Resource.create(
        {
            "service.name": "mkopo-api",
            "service.version": "0.1.0",
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)

    # Console exporter is always wired in dev so traces show up in the uvicorn log.
    if settings.environment == "development":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # OTLP exporter when an endpoint is configured (Phoenix/Tempo/etc.).
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
        )
        logger.info("otel_otlp_exporter_configured", endpoint=otlp_endpoint)

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)

    _initialised = True
    logger.info(
        "otel_initialised",
        service="mkopo-api",
        environment=settings.environment,
        otlp_endpoint=otlp_endpoint or "<console-only>",
    )

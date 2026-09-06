from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), extra="ignore"
    )

    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-5"

    pinecone_api_key: str
    pinecone_index_name: str = "er-shift-handoff"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    redflag_service_url: str = "http://127.0.0.1:9001"
    redflag_timeout_seconds: float = 5.0
    redflag_max_retries: int = 1

    otel_service_name: str = "er-shift-handoff-orchestrator"
    otel_console_export: bool = True
    otel_exporter_otlp_endpoint: str | None = None

    max_summarize_attempts: int = 2
    retrieval_top_k: int = 8


settings = Settings()

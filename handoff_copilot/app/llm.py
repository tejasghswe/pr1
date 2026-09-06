"""Thin, single-place wrapper around the chat model used by the Summarizer
and Ask agents. Centralizing this makes it trivial to swap models (e.g. a
cheaper one for evals) without touching agent logic.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from app.config import settings


@lru_cache(maxsize=4)
def get_chat_model(model: str | None = None) -> ChatAnthropic:
    # Note: `temperature` is intentionally omitted — newer Claude models
    # (e.g. claude-sonnet-5) reject it as a deprecated parameter.
    return ChatAnthropic(
        model=model or settings.anthropic_model,
        anthropic_api_key=settings.anthropic_api_key,
        max_tokens=1024,
    )

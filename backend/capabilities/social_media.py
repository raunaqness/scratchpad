"""LinkedIn content capability."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.config import settings
from backend.prompts import (
    CREATE_CAPABILITY_SYSTEM_PROMPT,
    EDIT_CAPABILITY_SYSTEM_PROMPT,
)


def _model() -> ChatOpenAI:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required to run Signal")
    if not settings.openrouter_model:
        raise RuntimeError("OPENROUTER_MODEL is required to run Signal")
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=settings.openrouter_temperature,
        max_tokens=settings.openrouter_max_tokens,
        timeout=settings.openrouter_timeout_seconds,
        max_retries=settings.openrouter_max_retries,
    )


def create_linkedin_post(request: dict[str, Any]) -> str:
    """Create a LinkedIn post from a validated structured request."""

    response = _model().invoke(
        [
            SystemMessage(content=CREATE_CAPABILITY_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(request, ensure_ascii=False)),
        ]
    )
    return str(response.content).strip()


def edit_linkedin_post(
    request: dict[str, Any],
    draft: str,
    instruction: str,
) -> str:
    """Revise an existing LinkedIn draft using only confirmed request data."""

    response = _model().invoke(
        [
            SystemMessage(content=EDIT_CAPABILITY_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "request": request,
                        "existing_draft": draft,
                        "edit_instruction": instruction,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    return str(response.content).strip()

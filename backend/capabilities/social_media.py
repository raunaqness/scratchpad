"""LinkedIn content capability."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.config import settings
from backend.prompts import CURRENT_SYSTEM_PROMPT


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

    system_prompt = f"""{CURRENT_SYSTEM_PROMPT}

You are the Signal LinkedIn post capability. Create exactly one LinkedIn post
from the validated request below. Use only facts in the request. Do not invent
prices, dates, specifications, availability, testimonials, performance
claims, or other details. Follow every explicit constraint. Return only the
post text, with no analysis or explanation.
"""
    response = _model().invoke(
        [
            SystemMessage(content=system_prompt),
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

    system_prompt = f"""{CURRENT_SYSTEM_PROMPT}

You are the Signal LinkedIn post editing capability. Revise the existing draft
according to the user's explicit editing request. Use only facts in the
validated request. Do not invent prices, dates, specifications, availability,
testimonials, performance claims, benefits, or other details. Preserve
unchanged requirements and return only the revised post text.
"""
    response = _model().invoke(
        [
            SystemMessage(content=system_prompt),
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

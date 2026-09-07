"""Shared DeepEval configuration for Signal's conversation tests."""

import pytest
from deepeval.models import OpenAIModel

from backend.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_eval_model() -> OpenAIModel:
    """Build DeepEval's OpenAI-compatible model adapter for OpenRouter."""

    if not settings.openrouter_api_key or not settings.openrouter_model:
        pytest.skip(
            "Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env "
            "before running DeepEval tests."
        )

    return OpenAIModel(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=settings.openrouter_temperature,
    )

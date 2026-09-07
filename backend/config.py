"""Application configuration boundary for Signal."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseModel):
    """Runtime settings loaded from environment variables."""

    development_mode: bool = Field(
        default=True,
        validation_alias="SIGNAL_DEVELOPMENT_MODE",
    )
    debug: bool = Field(default=False, validation_alias="SIGNAL_DEBUG")
    openrouter_api_key: str = Field(
        default="",
        validation_alias="OPENROUTER_API_KEY",
    )
    openrouter_model: str = Field(
        default="",
        validation_alias="OPENROUTER_MODEL",
    )
    openrouter_temperature: float = Field(
        default=0.7,
        validation_alias="OPENROUTER_TEMPERATURE",
    )
    openrouter_max_tokens: int = Field(
        default=2000,
        validation_alias="OPENROUTER_MAX_TOKENS",
    )
    openrouter_timeout_seconds: int = Field(
        default=60,
        validation_alias="OPENROUTER_TIMEOUT_SECONDS",
    )
    openrouter_max_retries: int = Field(
        default=2,
        validation_alias="OPENROUTER_MAX_RETRIES",
    )
    langsmith_tracing: bool = Field(
        default=False,
        validation_alias="LANGSMITH_TRACING",
    )
    langsmith_api_key: str = Field(
        default="",
        validation_alias="LANGSMITH_API_KEY",
    )
    langsmith_project: str = Field(
        default="signal-mvp",
        validation_alias="LANGSMITH_PROJECT",
    )
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        validation_alias="LANGSMITH_ENDPOINT",
    )
    data_dir: Path = Field(
        default=PROJECT_ROOT / "data",
        validation_alias="SIGNAL_DATA_DIR",
    )


def _environment_values() -> dict[str, str]:
    """Return only environment values understood by Settings."""

    names = {
        "SIGNAL_DEVELOPMENT_MODE",
        "SIGNAL_DEBUG",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_TEMPERATURE",
        "OPENROUTER_MAX_TOKENS",
        "OPENROUTER_TIMEOUT_SECONDS",
        "OPENROUTER_MAX_RETRIES",
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "SIGNAL_DATA_DIR",
    }
    return {name: os.environ[name] for name in names if name in os.environ}


settings = Settings.model_validate(_environment_values())

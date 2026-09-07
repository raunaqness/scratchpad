"""Application configuration boundary for Signal.

One typed settings object, loaded from the environment / ``.env`` via
``pydantic-settings``. Nothing else in the codebase should read ``os.environ``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime settings. Field names map to upper-cased env vars."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    development_mode: bool = Field(default=True, alias="SIGNAL_DEVELOPMENT_MODE")
    debug: bool = Field(default=False, alias="SIGNAL_DEBUG")

    # --- OpenRouter (the only LLM gateway) -------------------------------------
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="", alias="OPENROUTER_MODEL")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_temperature: float = Field(
        default=0.7, alias="OPENROUTER_TEMPERATURE"
    )
    # Turn interpretation is a classification/extraction job: keep it near-zero.
    interpret_temperature: float = Field(
        default=0.0, alias="SIGNAL_INTERPRET_TEMPERATURE"
    )
    openrouter_max_tokens: int = Field(default=2000, alias="OPENROUTER_MAX_TOKENS")
    openrouter_timeout_seconds: int = Field(
        default=60, alias="OPENROUTER_TIMEOUT_SECONDS"
    )
    openrouter_max_retries: int = Field(default=2, alias="OPENROUTER_MAX_RETRIES")

    # --- Conversation shaping ------------------------------------------------
    history_window: int = Field(default=12, alias="SIGNAL_HISTORY_WINDOW")
    summarize_after: int = Field(default=16, alias="SIGNAL_SUMMARIZE_AFTER")

    # --- Persistence ------------------------------------------------------------
    data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="SIGNAL_DATA_DIR")
    db_path: Path | None = Field(default=None, alias="SIGNAL_DB_PATH")

    # --- Observability --------------------------------------------------------
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="signal-mvp", alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT"
    )

    # --- Web adapter --------------------------------------------------------
    agent_port: int = Field(default=8001, alias="SIGNAL_AGENT_PORT")
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        alias="SIGNAL_CORS_ORIGINS",
    )

    @field_validator("data_dir", mode="after")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def _default_db_under_data_dir(self) -> "Settings":
        # db_path tracks data_dir unless SIGNAL_DB_PATH was given explicitly.
        if self.db_path is None:
            self.db_path = self.data_dir / "signal.db"
        else:
            self.db_path = self.db_path.expanduser()
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

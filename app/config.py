"""Application settings, loaded from environment variables or a .env file."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AgentDesk"
    environment: str = "development"
    secret_key: str = "dev-only-secret-change-me-before-deploying-0123456789"
    database_url: str = "sqlite:///./agentdesk.db"
    log_level: str = "INFO"

    # LLM settings. Any OpenAI-compatible endpoint works (OpenAI, Groq, etc.).
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"
    agent_max_history: int = 20
    agent_max_tool_rounds: int = 6
    agent_temperature: float = 0.2

    # Public URL of this server. Used to show the WhatsApp webhook URL in settings.
    public_base_url: str = "http://localhost:8000"
    whatsapp_graph_version: str = "v21.0"
    whatsapp_timeout_seconds: float = 10.0

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()

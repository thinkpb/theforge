from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORGE_", env_file=".env", extra="ignore")

    master_key: str = "change-me"
    database_url: str = "postgresql+asyncpg://forge:forge@localhost:5432/forge"
    redis_url: str = "redis://localhost:6379/0"
    ollama_base_url: str = "http://localhost:11434"

    # model alias -> litellm model string; the gateway only accepts aliases it knows
    model_map: dict[str, str] = {
        "gpt-4o": "openai/gpt-4o",
        "gpt-4o-mini": "openai/gpt-4o-mini",
        "claude-fable-5": "anthropic/claude-fable-5",
        "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
        "llama3.2": "ollama/llama3.2:1b",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORGE_", env_file=".env", extra="ignore")

    master_key: str = "change-me"
    database_url: str = "postgresql+asyncpg://forge:forge@localhost:5432/forge"
    redis_url: str = "redis://localhost:6379/0"
    ollama_base_url: str = "http://localhost:11434"

    # Audit write-behind buffer (ADR-0006). Bounded on purpose: when Postgres is
    # down long enough to fill the queue, requests get 503 rather than silently
    # going unaudited.
    audit_queue_size: int = 10_000
    audit_flush_batch: int = 100

    # PII scrubbing at the outbound boundary (ADR-0007). On by default; turning
    # it off is visible in the audit trail (pii_redactions = NULL).
    pii_scrubbing_enabled: bool = True
    # Domain terms the NER model false-positives on (drug names, org jargon).
    # JSON list in env: FORGE_PII_ALLOW_LIST='["Metformin","Lisinopril"]'
    pii_allow_list: list[str] = []
    # Entity types to scrub. Curated: identifiers, not every date-like string —
    # Presidio's DATE_TIME tags durations ("47 days"), which destroys facts in
    # RAG content (ADR-0012). HIPAA deployments that must suppress personal
    # dates add "DATE_TIME" here.
    pii_entities: list[str] = [
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "US_SSN",
        "CREDIT_CARD",
        "US_BANK_NUMBER",
        "US_ITIN",
        "US_PASSPORT",
        "US_DRIVER_LICENSE",
        "IBAN_CODE",
        "IP_ADDRESS",
        "MEDICAL_LICENSE",
        "UK_NHS",
        "LOCATION",
        "NRP",
    ]

    # Token-aware rate limits per team key per minute (ADR-0009). The master
    # (admin) key is exempt. Redis-down fails open.
    rate_limit_enabled: bool = True
    rate_limit_rpm: int = 60
    rate_limit_tpm: int = 100_000

    # RAG engine (ADR-0012). Embeddings default to a local model so documents
    # never leave the operator's infrastructure; collections are team-scoped.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_prefix: str = "forge"
    embedding_model: str = "ollama/nomic-embed-text"
    embedding_dim: int = 768
    rag_chunk_words: int = 250
    rag_chunk_overlap: int = 50

    # alias -> ordered fallback aliases tried on transient upstream failures
    # (ADR-0010). Env: FORGE_FALLBACK_MAP='{"gpt-4o": ["claude-fable-5"]}'
    fallback_map: dict[str, list[str]] = {}

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

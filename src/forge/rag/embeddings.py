"""Embedding calls via LiteLLM — same provider abstraction as completions
(ADR-0002), defaulting to a local Ollama model so document content never
leaves the operator's infrastructure (ADR-0012).
"""

from typing import Any

import litellm

from forge.config import Settings


async def embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    params: dict[str, Any] = {}
    if settings.embedding_model.startswith("ollama/"):
        params["api_base"] = settings.ollama_base_url
    response = await litellm.aembedding(
        model=settings.embedding_model, input=texts, **params
    )
    data = sorted(response.data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]

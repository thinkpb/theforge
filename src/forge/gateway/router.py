"""Provider routing.

Maps gateway model aliases to upstream providers via LiteLLM. Fallbacks,
retries, cost tracking, and rate limiting attach here in later milestones.
"""

from typing import Any

import litellm
from fastapi import HTTPException, status

from forge.config import Settings

# Forge surfaces errors itself; don't let litellm spam stdout.
litellm.suppress_debug_info = True


def resolve_model(alias: str, settings: Settings) -> str:
    try:
        return settings.model_map[alias]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown model '{alias}'. Available: {sorted(settings.model_map)}",
        ) from None


async def complete(
    *,
    model: str,
    messages: list[dict[str, Any]],
    settings: Settings,
    **params: Any,
) -> dict[str, Any]:
    upstream = resolve_model(model, settings)
    if upstream.startswith("ollama/"):
        params["api_base"] = settings.ollama_base_url
    try:
        response = await litellm.acompletion(model=upstream, messages=messages, **params)
    except litellm.exceptions.APIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream provider error: {exc}",
        ) from exc
    result = response.model_dump()
    # Report the alias, not the upstream model string — callers shouldn't see routing.
    result["model"] = model
    return result

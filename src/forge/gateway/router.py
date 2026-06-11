"""Provider routing.

Maps gateway model aliases to upstream providers via LiteLLM. Fallbacks,
retries, cost tracking, and rate limiting attach here in later milestones.
"""

from typing import Any

import litellm
import openai
from fastapi import HTTPException, status

from forge.config import Settings

# Forge surfaces errors itself; don't let litellm spam stdout.
litellm.suppress_debug_info = True

# Status codes are statements about whose fault it is: a bad upstream key is the
# operator's problem (502, never 401 — that would mean the client's Forge key is
# wrong). Order matters: Timeout subclasses APIConnectionError.
_ERROR_STATUS: dict[type[Exception], int] = {
    litellm.exceptions.Timeout: status.HTTP_504_GATEWAY_TIMEOUT,
    litellm.exceptions.RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    litellm.exceptions.AuthenticationError: status.HTTP_502_BAD_GATEWAY,
    litellm.exceptions.PermissionDeniedError: status.HTTP_502_BAD_GATEWAY,
    litellm.exceptions.BadRequestError: status.HTTP_400_BAD_REQUEST,
    litellm.exceptions.APIConnectionError: status.HTTP_504_GATEWAY_TIMEOUT,
}
_MAPPED_ERRORS = tuple(_ERROR_STATUS)


def _status_for(exc: Exception) -> int:
    for exc_type, code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            return code
    return status.HTTP_502_BAD_GATEWAY


def _upstream_error(exc: Exception, code: int) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail=f"Upstream provider error ({type(exc).__name__}): {exc}",
    )


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
    except _MAPPED_ERRORS as exc:
        raise _upstream_error(exc, _status_for(exc)) from exc
    # litellm's exception classes don't share a single litellm base — some subclass
    # openai's hierarchy directly. openai.OpenAIError is the one common ancestor.
    except openai.OpenAIError as exc:
        raise _upstream_error(exc, status.HTTP_502_BAD_GATEWAY) from exc
    result = response.model_dump()
    # Report the alias, not the upstream model string — callers shouldn't see routing.
    result["model"] = model
    return result

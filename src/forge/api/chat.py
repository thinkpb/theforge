"""OpenAI-compatible chat completions endpoint.

Clients point any OpenAI SDK at the gateway and use Forge model aliases.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from forge.audit import AuditBuffer, get_audit_buffer
from forge.auth import AuthContext, require_api_key
from forge.config import Settings, get_settings
from forge.gateway import router as gateway
from forge.pii import PIIScrubber, get_pii_scrubber

router = APIRouter(dependencies=[Depends(require_api_key)])


class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    # streaming lands in the SSE milestone
    stream: bool = False


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if request.temperature is not None:
        params["temperature"] = request.temperature
    if request.max_tokens is not None:
        params["max_tokens"] = request.max_tokens
    if ctx.pii_opt_out:
        # Per-key opt-out (ADR-0007/0008) — the audit row records NULL redactions.
        scrubber = PIIScrubber(enabled=False)
    return await gateway.complete(
        model=request.model,
        messages=[m.model_dump() for m in request.messages],
        settings=settings,
        audit=audit,
        api_key_hash=ctx.key_hash,
        scrubber=scrubber,
        **params,
    )


@router.get("/v1/models")
async def list_models(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": alias, "object": "model"} for alias in sorted(settings.model_map)],
    }

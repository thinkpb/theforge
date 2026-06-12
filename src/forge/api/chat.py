"""OpenAI-compatible chat completions endpoint.

Clients point any OpenAI SDK at the gateway and use Forge model aliases.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from forge.audit import AuditBuffer, AuditRecord, get_audit_buffer
from forge.auth import AuthContext, require_api_key
from forge.config import Settings, get_settings
from forge.gateway import router as gateway
from forge.pii import PIIScrubber, get_pii_scrubber
from forge.ratelimit import RateLimiter, RateLimitExceeded, get_rate_limiter

router = APIRouter(dependencies=[Depends(require_api_key)])


class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    stream: bool = False


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    if not ctx.is_master:
        try:
            await limiter.check_and_count(ctx.key_hash)
        except RateLimitExceeded as exc:
            # rate-limited requests are still audited — "every request" means every
            audit.put(
                AuditRecord(
                    request_id=uuid.uuid4(),
                    api_key_hash=ctx.key_hash,
                    model_alias=request.model,
                    upstream_model=None,
                    outcome="rate_limited",
                    status_code=429,
                    error_type=exc.reason,
                    latency_ms=0,
                )
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({exc.reason}); retry after {exc.retry_after}s",
                headers={"Retry-After": str(exc.retry_after)},
            ) from None

    params: dict[str, Any] = {}
    if request.temperature is not None:
        params["temperature"] = request.temperature
    if request.max_tokens is not None:
        params["max_tokens"] = request.max_tokens
    if ctx.pii_opt_out:
        # Per-key opt-out (ADR-0007/0008) — the audit row records NULL redactions.
        scrubber = PIIScrubber(enabled=False)
    gateway_args: dict[str, Any] = dict(
        model=request.model,
        messages=[m.model_dump() for m in request.messages],
        settings=settings,
        audit=audit,
        api_key_hash=ctx.key_hash,
        scrubber=scrubber,
        **params,
    )
    if request.stream:
        # setup errors raise before the response starts; the audit record is
        # written when the stream ends (ADR-0011). Streams debit only the
        # request counter (ADR-0009).
        body = await gateway.complete_stream(**gateway_args)
        return StreamingResponse(body, media_type="text/event-stream")
    result = await gateway.complete(**gateway_args)
    if not ctx.is_master:
        usage = result.get("usage") or {}
        await limiter.debit_tokens(ctx.key_hash, usage.get("total_tokens"))
    return result


@router.get("/v1/models")
async def list_models(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": alias, "object": "model"} for alias in sorted(settings.model_map)],
    }

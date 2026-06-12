"""Provider routing.

Maps gateway model aliases to upstream providers via LiteLLM, and emits an
audit record for every request (ADR-0005/0006) — this layer is the choke point
where alias, upstream model, tokens, and cost are all known, so every surface
built on the gateway inherits auditing. Fallbacks, retries, and rate limiting
attach here in later milestones.
"""

import time
import uuid
from typing import Any

import litellm
import openai
from fastapi import HTTPException, status

from forge.audit import AuditBuffer, AuditBufferFull, AuditRecord
from forge.config import Settings
from forge.pii import PIIScrubber

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


def _audit(buffer: AuditBuffer, record: AuditRecord) -> None:
    try:
        buffer.put(record)
    except AuditBufferFull:
        # Bounded-queue backstop (ADR-0006): a request that can't be audited
        # doesn't happen. 503 signals operators, not a client mistake.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit backlog at capacity; request rejected.",
        ) from None


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
    audit: AuditBuffer,
    api_key_hash: str,
    scrubber: PIIScrubber,
    **params: Any,
) -> dict[str, Any]:
    request_id = uuid.uuid4()
    try:
        upstream = resolve_model(model, settings)
    except HTTPException as exc:
        _audit(
            audit,
            AuditRecord(
                request_id=request_id,
                api_key_hash=api_key_hash,
                model_alias=model,
                upstream_model=None,
                outcome="rejected",
                status_code=exc.status_code,
                latency_ms=0,
            ),
        )
        raise

    if upstream.startswith("ollama/"):
        params["api_base"] = settings.ollama_base_url

    # PII boundary (ADR-0007): nothing leaves for an upstream provider unscrubbed.
    messages, pii_redactions = await scrubber.scrub_messages(messages)

    def _error_record(exc: Exception, code: int) -> AuditRecord:
        return AuditRecord(
            request_id=request_id,
            api_key_hash=api_key_hash,
            model_alias=model,
            upstream_model=upstream,
            outcome="upstream_error",
            status_code=code,
            error_type=type(exc).__name__,
            latency_ms=int((time.perf_counter() - started) * 1000),
            pii_redactions=pii_redactions,
        )

    started = time.perf_counter()
    try:
        response = await litellm.acompletion(model=upstream, messages=messages, **params)
    except _MAPPED_ERRORS as exc:
        code = _status_for(exc)
        _audit(audit, _error_record(exc, code))
        raise _upstream_error(exc, code) from exc
    # litellm's exception classes don't share a single litellm base — some subclass
    # openai's hierarchy directly. openai.OpenAIError is the one common ancestor.
    except openai.OpenAIError as exc:
        _audit(audit, _error_record(exc, status.HTTP_502_BAD_GATEWAY))
        raise _upstream_error(exc, status.HTTP_502_BAD_GATEWAY) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)

    result = response.model_dump()
    usage = result.get("usage") or {}
    try:
        cost_usd = litellm.completion_cost(completion_response=response)
    except Exception:
        cost_usd = None  # unknown pricing (e.g. local models) is not an error
    _audit(
        audit,
        AuditRecord(
            request_id=request_id,
            api_key_hash=api_key_hash,
            model_alias=model,
            upstream_model=upstream,
            outcome="success",
            status_code=status.HTTP_200_OK,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            pii_redactions=pii_redactions,
        ),
    )
    # Report the alias, not the upstream model string — callers shouldn't see routing.
    result["model"] = model
    return result

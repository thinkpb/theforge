"""Provider routing.

Maps gateway model aliases to upstream providers via LiteLLM, and emits an
audit record for every request (ADR-0005/0006) — this layer is the choke point
where alias, upstream model, tokens, and cost are all known, so every surface
built on the gateway inherits auditing. Fallbacks, retries, and rate limiting
attach here in later milestones.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import litellm
import openai
from fastapi import HTTPException, status

from forge.audit import AuditBuffer, AuditBufferFull, AuditRecord
from forge.config import Settings
from forge.pii import PIIScrubber

logger = logging.getLogger(__name__)

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

# Worth trying another provider for. BadRequest/Auth failures are excluded on
# purpose: the request or the operator config is wrong, and a different
# provider can't fix either.
TRANSIENT_ERRORS = (
    litellm.exceptions.Timeout,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.ServiceUnavailableError,
)


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

    # PII boundary (ADR-0007): nothing leaves for an upstream provider unscrubbed.
    messages, pii_redactions = await scrubber.scrub_messages(messages)

    def _error_record(exc: Exception, code: int, attempted: str) -> AuditRecord:
        return AuditRecord(
            request_id=request_id,
            api_key_hash=api_key_hash,
            model_alias=model,
            upstream_model=attempted,
            outcome="upstream_error",
            status_code=code,
            error_type=type(exc).__name__,
            latency_ms=int((time.perf_counter() - started) * 1000),
            pii_redactions=pii_redactions,
        )

    # Fallback chain (ADR-0010): primary first, then configured fallbacks, each
    # tried once. Latency in the audit record is total across attempts.
    chain = [model, *settings.fallback_map.get(model, [])]
    started = time.perf_counter()
    response = None
    last_exc: Exception | None = None
    serving = upstream
    for index, alias in enumerate(chain):
        try:
            serving = upstream if alias == model else resolve_model(alias, settings)
        except HTTPException:
            logger.warning("fallback alias %r for %r is not in model_map; skipping", alias, model)
            continue
        attempt_params = dict(params)
        if serving.startswith("ollama/"):
            attempt_params["api_base"] = settings.ollama_base_url
        try:
            response = await litellm.acompletion(
                model=serving, messages=messages, **attempt_params
            )
            break
        # litellm's exception classes don't share a single litellm base — some
        # subclass openai's hierarchy directly. OpenAIError is the one common
        # ancestor.
        except openai.OpenAIError as exc:
            last_exc = exc
            if isinstance(exc, TRANSIENT_ERRORS) and index < len(chain) - 1:
                logger.warning(
                    "upstream %s failed transiently (%s); trying fallback",
                    serving,
                    type(exc).__name__,
                )
                continue
            code = _status_for(exc)
            _audit(audit, _error_record(exc, code, serving))
            raise _upstream_error(exc, code) from exc
    if response is None:
        # chain exhausted on transient errors (or fully misconfigured)
        if last_exc is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"No resolvable upstream for '{model}' — check fallback_map",
            )
        code = _status_for(last_exc)
        _audit(audit, _error_record(last_exc, code, serving))
        raise _upstream_error(last_exc, code) from last_exc
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
            upstream_model=serving,
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


async def complete_stream(
    *,
    model: str,
    messages: list[dict[str, Any]],
    settings: Settings,
    audit: AuditBuffer,
    api_key_hash: str,
    scrubber: PIIScrubber,
    **params: Any,
) -> AsyncIterator[str]:
    """Set up a streaming completion and return the SSE body generator.

    Setup (alias resolution, PII scrub, provider call) happens HERE, before the
    HTTP response starts, so failures still get real status codes. The audit
    record is written when the stream finishes — success or mid-stream error —
    with total latency and whatever usage the provider reported (ADR-0011).
    Fallback chains don't apply to streams (ADR-0010).
    """
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

    messages, pii_redactions = await scrubber.scrub_messages(messages)
    if upstream.startswith("ollama/"):
        params["api_base"] = settings.ollama_base_url

    started = time.perf_counter()

    def _record(outcome: str, code: int, usage: dict | None, error: Exception | None):
        usage = usage or {}
        return AuditRecord(
            request_id=request_id,
            api_key_hash=api_key_hash,
            model_alias=model,
            upstream_model=upstream,
            outcome=outcome,
            status_code=code,
            error_type=type(error).__name__ if error else None,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            pii_redactions=pii_redactions,
        )

    try:
        stream = await litellm.acompletion(
            model=upstream, messages=messages, stream=True, **params
        )
    except openai.OpenAIError as exc:
        code = _status_for(exc)
        _audit(audit, _record("upstream_error", code, None, exc))
        raise _upstream_error(exc, code) from exc

    async def _body() -> AsyncIterator[str]:
        usage: dict | None = None
        try:
            async for chunk in stream:
                data = chunk.model_dump()
                data["model"] = model  # alias, never the upstream string (ADR-0001)
                if data.get("usage"):
                    usage = data["usage"]
                yield f"data: {json.dumps(data)}\n\n"
            yield "data: [DONE]\n\n"
            _audit(audit, _record("success", status.HTTP_200_OK, usage, None))
        except openai.OpenAIError as exc:
            # response already started — emit an SSE error event, audit the truth
            _audit(audit, _record("upstream_error", _status_for(exc), usage, exc))
            payload = {"error": {"message": f"Upstream provider error: {type(exc).__name__}"}}
            yield f"data: {json.dumps(payload)}\n\n"

    return _body()

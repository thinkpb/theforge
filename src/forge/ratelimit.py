"""Token-aware rate limiting (ADR-0009).

Two fixed-window counters per key per minute in Redis: request count (checked
and incremented pre-flight) and consumed tokens (debited after completion,
when actual usage is known). Token enforcement is therefore reactive — a key
can overshoot the token budget by one request, then gets 429 until the window
resets. That is the honest trade-off of not knowing token counts up front.

Redis being down fails OPEN: rate limiting protects cost and capacity, not
compliance — availability wins, loudly logged. (Contrast with the audit
buffer, ADR-0006, where the same failure fails closed.)
"""

import logging
import time

from fastapi import Request
from redis import asyncio as aioredis

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
_KEY_TTL = 2 * _WINDOW_SECONDS  # outlive the window so debits never resurrect it


class RateLimitExceeded(Exception):
    def __init__(self, reason: str, retry_after: int):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, redis: aioredis.Redis, *, rpm: int, tpm: int, enabled: bool):
        self._redis = redis
        self.rpm = rpm
        self.tpm = tpm
        self.enabled = enabled

    @staticmethod
    def _window() -> tuple[int, int]:
        now = time.time()
        return int(now // _WINDOW_SECONDS), _WINDOW_SECONDS - int(now % _WINDOW_SECONDS)

    async def check_and_count(self, key_hash: str) -> None:
        """Count this request; raise RateLimitExceeded if either budget is spent."""
        if not self.enabled:
            return
        window, remaining = self._window()
        request_key = f"rl:{key_hash}:{window}:r"
        token_key = f"rl:{key_hash}:{window}:t"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(request_key)
                pipe.expire(request_key, _KEY_TTL)
                pipe.get(token_key)
                requests, _, tokens = await pipe.execute()
        except Exception:
            logger.exception("rate limiter unavailable; failing open")
            return
        if requests > self.rpm:
            raise RateLimitExceeded("request limit exceeded", retry_after=remaining)
        if tokens is not None and int(tokens) >= self.tpm:
            raise RateLimitExceeded("token limit exceeded", retry_after=remaining)

    async def debit_tokens(self, key_hash: str, tokens: int | None) -> None:
        if not self.enabled or not tokens:
            return
        window, _ = self._window()
        token_key = f"rl:{key_hash}:{window}:t"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incrby(token_key, tokens)
                pipe.expire(token_key, _KEY_TTL)
                await pipe.execute()
        except Exception:
            logger.exception("rate limiter debit failed; failing open")


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter

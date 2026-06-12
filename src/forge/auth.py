"""Bearer-token auth (ADR-0008).

Two credential classes: per-team API keys stored hash-only in Postgres, and the
master key from settings, which is the admin/bootstrap credential — it manages
keys and reads the audit trail. Completions accept either. Lookup is by SHA-256
hash; the raw key is never stored, logged, or audited.
"""

import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from forge.audit import key_fingerprint
from forge.config import Settings, get_settings
from forge.keys import ApiKey

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    key_hash: str
    is_master: bool
    team: str | None = None
    key_name: str | None = None
    pii_opt_out: bool = False


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if credentials is None:
        raise _unauthorized()
    token = credentials.credentials

    if secrets.compare_digest(token, settings.master_key):
        return AuthContext(key_hash=key_fingerprint(token), is_master=True, key_name="master")

    token_hash = key_fingerprint(token)
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        key = await session.scalar(select(ApiKey).where(ApiKey.key_hash == token_hash))
    if key is None or key.revoked_at is not None:
        raise _unauthorized()
    return AuthContext(
        key_hash=token_hash,
        is_master=False,
        team=key.team,
        key_name=key.name,
        pii_opt_out=key.pii_opt_out,
    )


async def require_master_key(ctx: AuthContext = Depends(require_api_key)) -> AuthContext:
    if not ctx.is_master:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires the master key",
        )
    return ctx

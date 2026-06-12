"""API key management endpoints — master key only (ADR-0008)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from forge.auth import require_master_key
from forge.keys import ApiKey, generate_key

router = APIRouter(dependencies=[Depends(require_master_key)])


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    team: str = Field(min_length=1, max_length=128)
    pii_opt_out: bool = False


def _public(key: ApiKey) -> dict[str, Any]:
    return {
        "id": str(key.id),
        "name": key.name,
        "team": key.team,
        "key_prefix": key.key_prefix,
        "pii_opt_out": key.pii_opt_out,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
    }


@router.post("/v1/keys", status_code=status.HTTP_201_CREATED)
async def create_key(request: Request, body: CreateKeyRequest) -> dict[str, Any]:
    token, token_hash, prefix = generate_key()
    record = ApiKey(
        name=body.name,
        team=body.team,
        key_hash=token_hash,
        key_prefix=prefix,
        pii_opt_out=body.pii_opt_out,
    )
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)
    # The only moment the full key ever exists outside the caller's hands.
    return {**_public(record), "key": token}


@router.get("/v1/keys")
async def list_keys(request: Request) -> dict[str, Any]:
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        keys = (await session.scalars(select(ApiKey).order_by(ApiKey.created_at))).all()
    return {"object": "list", "data": [_public(k) for k in keys]}


@router.delete("/v1/keys/{key_id}")
async def revoke_key(request: Request, key_id: uuid.UUID) -> dict[str, Any]:
    """Revoke, never delete — audit records must always resolve to their key."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        key = await session.get(ApiKey, key_id)
        if key is None:
            raise HTTPException(status_code=404, detail="Unknown key id")
        if key.revoked_at is None:
            key.revoked_at = datetime.now(UTC)
            await session.commit()
        await session.refresh(key)
        return _public(key)

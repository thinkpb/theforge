"""Per-team API keys (ADR-0008).

Keys are prefixed random tokens (fsk_...) shown to the caller exactly once at
creation; only the SHA-256 hash and a short display prefix are stored. Revoking
sets revoked_at — key rows are never deleted, so audit records always resolve
to the key that made them.
"""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from forge.audit import key_fingerprint
from forge.db import Base

KEY_PREFIX = "fsk_"
_DISPLAY_PREFIX_LEN = 12


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    team: Mapped[str]
    key_hash: Mapped[str] = mapped_column(unique=True, index=True)
    key_prefix: Mapped[str]
    # Per-key PII opt-out (ADR-0007): the exception that leaves an audit trace
    # (pii_redactions = NULL on every request made with this key).
    pii_opt_out: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def generate_key() -> tuple[str, str, str]:
    """Return (full key, hash for storage, display prefix). The full key exists
    only in the creation response — it is never stored or logged."""
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    return token, key_fingerprint(token), token[:_DISPLAY_PREFIX_LEN]

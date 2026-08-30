from datetime import datetime
import hashlib
import re
import secrets
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


_TOKEN_PREFIX = "sk-presenton-"
_REFERENCE_PREFIX = "sk-presenton-ref-"
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_PATTERN = re.compile(r"^sk-presenton-ref-([0-9a-f]{24})$")


def hash_access_token(token: str) -> str:
    if not isinstance(token, str) or not token.startswith(_TOKEN_PREFIX):
        raise ValueError("access_token_invalid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def access_token_reference(token_hash: str) -> str:
    if not _DIGEST_PATTERN.fullmatch(token_hash):
        raise ValueError("access_token_digest_invalid")
    return _REFERENCE_PREFIX + token_hash[:24]


def access_token_reference_prefix(reference: str) -> str | None:
    match = _REFERENCE_PATTERN.fullmatch(reference)
    return match.group(1) if match else None


def issue_access_token() -> tuple[str, str]:
    token = f"{_TOKEN_PREFIX}{secrets.token_hex(20)}"
    return token, hash_access_token(token)


class AccessToken(SQLModel, table=True):
    __tablename__ = "access_tokens"

    # Keep the historical SQL column name for a narrow data migration, but the
    # Python contract makes clear that only a one-way digest is persisted.
    token_hash: str = Field(
        sa_column=Column("token", String(64), primary_key=True, index=True),
    )
    user_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
        )
    )

from datetime import datetime
from typing import Annotated
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth.users import get_current_admin
from models.sql.access_token import (
    AccessToken,
    access_token_reference,
    access_token_reference_prefix,
    hash_access_token,
    issue_access_token,
)
from models.sql.user import User
from services.database import get_async_session


TOKEN_ROUTER = APIRouter(prefix="/token", tags=["API Keys"])


class AccessTokenView(BaseModel):
    # Existing UI uses `token` as the row/revoke handle.  List responses contain
    # only a non-authenticating reference; create returns the secret once.
    token: str
    user_id: uuid.UUID
    created_at: datetime


def _view(record: AccessToken, *, token: str | None = None) -> AccessTokenView:
    return AccessTokenView(
        token=token or access_token_reference(record.token_hash),
        user_id=record.user_id,
        created_at=record.created_at,
    )


@TOKEN_ROUTER.get("/list", response_model=list[AccessTokenView])
async def list_tokens(
    response: Response,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    records = (
        await session.scalars(
            select(AccessToken)
            .where(AccessToken.user_id == admin.id)
            .order_by(AccessToken.created_at.desc())
        )
    ).all()
    return [_view(record) for record in records]


@TOKEN_ROUTER.post("/create", response_model=AccessTokenView)
async def create_token(
    response: Response,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    raw_token, token_hash = issue_access_token()
    record = AccessToken(token_hash=token_hash, user_id=admin.id)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    response.headers["Cache-Control"] = "no-store"
    return _view(record, token=raw_token)


@TOKEN_ROUTER.post("/revoke")
async def revoke_token(
    token: Annotated[str, Body(embed=True)],
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    reference_prefix = access_token_reference_prefix(token)
    if reference_prefix is not None:
        matches = list(
            await session.scalars(
                select(AccessToken).where(
                    AccessToken.token_hash.like(reference_prefix + "%"),
                    AccessToken.user_id == admin.id,
                )
            )
        )
        access_token = matches[0] if len(matches) == 1 else None
    else:
        try:
            access_token = await session.get(AccessToken, hash_access_token(token))
        except ValueError:
            access_token = None
    if access_token is None or access_token.user_id != admin.id:
        raise HTTPException(status_code=404, detail="Token not found")
    await session.delete(access_token)
    await session.commit()
    return {"message": "Token revoked"}

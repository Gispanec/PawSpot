import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pawspot.auth import (
    AuthError,
    get_internal_actor,
    get_public_actor,
    issue_session,
    limit_auth,
    limit_internal,
    require_allowed,
    upsert_user,
    validate_init_data,
)
from pawspot.config import Settings, get_settings
from pawspot.db import get_session
from pawspot.models import AuthSession, User

public_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
internal_router = APIRouter(prefix="/internal/v1/auth", tags=["internal auth"])


class TelegramLogin(BaseModel):
    init_data: str = Field(min_length=1, max_length=4096)


class SessionResponse(BaseModel):
    token: str
    expires_at: datetime
    user_public_id: UUID
    display_name: str


class ActorResponse(BaseModel):
    public_id: UUID
    display_name: str


@public_router.post(
    "/telegram", response_model=SessionResponse, dependencies=[Depends(limit_auth)]
)
def telegram_login(
    body: TelegramLogin,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionResponse:
    configured = settings.telegram_bot_token
    if configured is None or not configured.get_secret_value():
        raise HTTPException(
            status_code=503, detail="Telegram authentication unavailable"
        )
    try:
        identity = validate_init_data(
            body.init_data,
            configured.get_secret_value(),
            settings.auth_init_data_max_age_seconds,
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram data") from exc
    require_allowed(identity.id, settings)
    user = upsert_user(session, identity)
    token, expires_at = issue_session(session, user, settings)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        token=token,
        expires_at=expires_at,
        user_public_id=user.id,
        display_name=user.display_name,
    )


@public_router.get("/me", response_model=ActorResponse)
def current_actor(actor: Annotated[User, Depends(get_public_actor)]) -> ActorResponse:
    return ActorResponse(public_id=actor.id, display_name=actor.display_name)


@public_router.delete("/session", status_code=204)
def revoke_session(
    request: Request,
    actor: Annotated[User, Depends(get_public_actor)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    authorization = request.headers["authorization"]
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash, AuthSession.user_id == actor.id
        )
    )
    if auth_session is not None:
        auth_session.revoked_at = datetime.now(UTC)
        session.commit()


@internal_router.get(
    "/actor", response_model=ActorResponse, dependencies=[Depends(limit_internal)]
)
def internal_actor(
    actor: Annotated[User, Depends(get_internal_actor)],
) -> ActorResponse:
    return ActorResponse(public_id=actor.id, display_name=actor.display_name)

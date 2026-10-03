import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pawspot.config import Settings, get_settings
from pawspot.db import get_session
from pawspot.models import AuthSession, User


class TelegramIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0, strict=True)
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)
    username: str | None = Field(default=None, max_length=64)

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)[
            :160
        ]


class AuthError(ValueError):
    pass


def validate_init_data(
    raw: str, bot_token: str, max_age_seconds: int
) -> TelegramIdentity:
    if not raw or len(raw) > 4096 or not bot_token:
        raise AuthError("Invalid Telegram data")
    if re.search(r"%(?![0-9A-Fa-f]{2})", raw):
        raise AuthError("Invalid Telegram data")
    try:
        pairs = parse_qsl(
            raw, keep_blank_values=True, strict_parsing=True, errors="strict"
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("Invalid Telegram data") from exc
    data = dict(pairs)
    if len(data) != len(pairs):
        raise AuthError("Duplicate Telegram data field")
    supplied_hash = data.pop("hash", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_hash):
        raise AuthError("Invalid Telegram hash")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash.lower()):
        raise AuthError("Invalid Telegram signature")
    auth_date = data.get("auth_date", "")
    if not auth_date.isascii() or not auth_date.isdigit() or len(auth_date) > 12:
        raise AuthError("Invalid Telegram auth date")
    age = int(time.time()) - int(auth_date)
    if age > max_age_seconds or age < -30:
        raise AuthError("Telegram data expired")
    try:
        user = json.loads(data["user"])
        return TelegramIdentity.model_validate(user)
    except (KeyError, json.JSONDecodeError, ValidationError) as exc:
        raise AuthError("Invalid Telegram user") from exc


def require_allowed(telegram_id: int, settings: Settings) -> None:
    if telegram_id not in settings.allowlist:
        raise HTTPException(status_code=403, detail="Access denied")


def upsert_user(session: Session, identity: TelegramIdentity) -> User:
    statement = (
        insert(User)
        .values(
            telegram_id=identity.id,
            telegram_username=identity.username,
            display_name=identity.display_name,
        )
        .on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_={
                "telegram_username": identity.username,
                "display_name": identity.display_name,
            },
        )
        .returning(User)
    )
    return session.scalars(statement).one()


def issue_session(
    session: Session, user: User, settings: Settings
) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=settings.auth_session_hours)
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expires_at,
        )
    )
    return token, expires_at


def get_public_actor(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization[7:]
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Invalid session")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if auth_session is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    actor = session.get(User, auth_session.user_id)
    if actor is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    require_allowed(actor.telegram_id, settings)
    return actor


def get_internal_actor(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_pawspot_service_token: str | None = Header(default=None),
    x_pawspot_telegram_id: int | None = Header(default=None),
    x_pawspot_display_name_b64: str | None = Header(default=None),
) -> User:
    configured = settings.internal_service_token
    if (
        configured is None
        or not configured.get_secret_value()
        or x_pawspot_service_token is None
        or not hmac.compare_digest(
            x_pawspot_service_token, configured.get_secret_value()
        )
    ):
        raise HTTPException(status_code=401, detail="Invalid service credential")
    if x_pawspot_telegram_id is None or x_pawspot_telegram_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid Telegram actor")
    require_allowed(x_pawspot_telegram_id, settings)
    display_name = "Telegram user"
    if x_pawspot_display_name_b64 is not None:
        if len(x_pawspot_display_name_b64) > 256:
            raise HTTPException(status_code=400, detail="Invalid display name")
        try:
            display_name = base64.b64decode(
                x_pawspot_display_name_b64, altchars=b"-_", validate=True
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="Invalid display name") from exc
    identity = TelegramIdentity(
        id=x_pawspot_telegram_id,
        first_name=display_name[:64],
    )
    actor = upsert_user(session, identity)
    session.commit()
    return actor


@dataclass
class Window:
    started: float
    count: int


class InProcessRateLimiter:
    def __init__(self, limit: int, period_seconds: int) -> None:
        self.limit = limit
        self.period_seconds = period_seconds
        self._windows: dict[str, Window] = {}
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.started >= self.period_seconds:
                self._windows[key] = Window(now, 1)
            elif window.count >= self.limit:
                raise HTTPException(status_code=429, detail="Too many requests")
            else:
                window.count += 1


auth_limiter = InProcessRateLimiter(limit=10, period_seconds=60)
internal_limiter = InProcessRateLimiter(limit=60, period_seconds=60)


def limit_auth(request: Request) -> None:
    auth_limiter.check(request.client.host if request.client else "unknown")


def limit_internal(request: Request) -> None:
    internal_limiter.check(request.client.host if request.client else "unknown")

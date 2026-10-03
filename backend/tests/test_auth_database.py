import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from pawspot.config import Settings, get_settings
from pawspot.db import get_engine
from pawspot.main import app
from pawspot.models import User
from test_auth import BOT_TOKEN, signed_init_data


@pytest.mark.integration
def test_login_allowlist_sessions_and_internal_actor() -> None:
    telegram_id = uuid4().int & ((1 << 52) - 1)
    settings = Settings(
        db_password=SecretStr(""),
        telegram_bot_token=SecretStr(BOT_TOKEN),
        internal_service_token=SecretStr("internal-test-secret"),
        allowed_telegram_ids=str(telegram_id),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            denied = client.post(
                "/api/v1/auth/telegram", json={"init_data": signed_init_data(999)}
            )
            assert denied.status_code == 403

            first = client.post(
                "/api/v1/auth/telegram",
                json={"init_data": signed_init_data(telegram_id)},
            )
            assert first.status_code == 200
            assert first.headers["cache-control"] == "no-store"
            token = first.json()["token"]
            assert token
            second = client.post(
                "/api/v1/auth/telegram",
                json={"init_data": signed_init_data(telegram_id)},
            )
            assert second.status_code == 200
            assert second.json()["user_public_id"] == first.json()["user_public_id"]

            with get_engine().connect() as connection:
                users = connection.execute(
                    select(User.id).where(User.telegram_id == telegram_id)
                ).all()
                assert len(users) == 1

            auth = {"Authorization": f"Bearer {token}"}
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
            assert client.get("/api/v1/auth/me").status_code == 401
            assert (
                client.get(
                    "/api/v1/auth/me", headers={"Authorization": "Bearer wrong"}
                ).status_code
                == 401
            )

            internal_headers = {
                "X-Pawspot-Service-Token": "internal-test-secret",
                "X-Pawspot-Telegram-Id": str(telegram_id),
                "X-Pawspot-Display-Name-B64": base64.urlsafe_b64encode(
                    "Иван".encode()
                ).decode(),
            }
            assert (
                client.get(
                    "/internal/v1/auth/actor", headers=internal_headers
                ).status_code
                == 200
            )
            assert (
                client.get(
                    "/internal/v1/auth/actor",
                    headers={**internal_headers, "X-Pawspot-Service-Token": "wrong"},
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/internal/v1/auth/actor",
                    headers={**internal_headers, "X-Pawspot-Telegram-Id": "999"},
                ).status_code
                == 403
            )

            assert (
                client.delete("/api/v1/auth/session", headers=auth).status_code == 204
            )
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
    finally:
        app.dependency_overrides.clear()

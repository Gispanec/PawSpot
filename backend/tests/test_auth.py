import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from pawspot.auth import AuthError, validate_init_data
from pawspot.config import Settings

BOT_TOKEN = "123456:test-only-token"


def signed_init_data(
    telegram_id: int = 123,
    *,
    auth_date: int | None = None,
    first_name: str = "Иван",
) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "user": json.dumps(
            {"id": telegram_id, "first_name": first_name},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_with_unicode() -> None:
    identity = validate_init_data(signed_init_data(first_name="Гиви"), BOT_TOKEN, 300)
    assert identity.id == 123
    assert identity.display_name == "Гиви"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-query-data",
        "auth_date=12&hash=bad",
        "auth_date=%ZZ&hash=bad",
        signed_init_data() + "&user=other",
    ],
)
def test_malformed_init_data_rejected(raw: str) -> None:
    with pytest.raises(AuthError):
        validate_init_data(raw, BOT_TOKEN, 300)


def test_tampered_signature_rejected() -> None:
    with pytest.raises(AuthError):
        validate_init_data(signed_init_data().replace("123", "124", 1), BOT_TOKEN, 300)


@pytest.mark.parametrize("offset", [-301, 31])
def test_stale_or_future_init_data_rejected(offset: int) -> None:
    with pytest.raises(AuthError):
        validate_init_data(
            signed_init_data(auth_date=int(time.time()) + offset), BOT_TOKEN, 300
        )


def test_allowlist_is_closed_by_default() -> None:
    settings = Settings(db_password=SecretStr("test"))
    assert not settings.allowlist

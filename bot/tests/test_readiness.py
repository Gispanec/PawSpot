import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from aiogram.methods import GetMe, GetUpdates, TelegramMethod
from aiogram.types import WebhookInfo
from pydantic import SecretStr

from pawspot_bot import main
from pawspot_bot.config import BotSettings
from pawspot_bot.readiness import PollingReadiness


def test_ready_requires_successful_get_updates(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "ready.json"
        readiness = PollingReadiness(path)
        readiness.write("starting")
        bot = Bot("123456:TEST_TOKEN")
        request = AsyncMock(return_value=[])
        await readiness(request, bot, GetMe())
        assert json.loads(path.read_text())["status"] == "starting"
        await readiness(request, bot, GetUpdates())
        state = json.loads(path.read_text())
        assert state["status"] == "ready"
        assert state["pid"] > 0
        assert "TEST_TOKEN" not in path.read_text()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("http_status", "backend_status", "webhook", "error_type"),
    [
        (503, "unavailable", "", httpx.HTTPStatusError),
        (200, "unavailable", "", RuntimeError),
        (200, "ready", "https://existing.example/webhook", RuntimeError),
    ],
)
def test_startup_rejects_unready_backend_or_existing_webhook(
    tmp_path: Path,
    http_status: int,
    backend_status: str,
    webhook: str,
    error_type: type[Exception],
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:TEST_TOKEN")
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = httpx.Response(
            http_status,
            json={"status": backend_status},
            request=httpx.Request("GET", "http://127.0.0.1:8000/ready"),
        )
        settings = BotSettings.model_construct(
            telegram_bot_token=SecretStr("123456:TEST_TOKEN"),
            internal_service_token=SecretStr("test-service"),
        )
        path = tmp_path / "ready.json"
        with (
            patch.object(main, "BotSettings", return_value=settings),
            patch.object(main, "Bot", return_value=bot),
            patch.object(main, "BackendClient", return_value=AsyncMock()),
            patch.object(httpx, "AsyncClient", return_value=client),
            patch.object(
                bot,
                "get_webhook_info",
                return_value=WebhookInfo(
                    url=webhook, has_custom_certificate=False, pending_update_count=0
                ),
            ),
            patch.object(main, "configure_menu_button", new_callable=AsyncMock) as menu,
            patch.object(main, "create_dispatcher") as dispatcher,
        ):
            with pytest.raises(error_type):
                await main.run(path)
            menu.assert_not_awaited()
            dispatcher.assert_not_called()
        assert json.loads(path.read_text())["status"] == "failed"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("exception", "status"),
    [(TelegramConflictError, "failed"), (TelegramNetworkError, "waiting")],
)
def test_polling_failure_invalidates_readiness(
    tmp_path: Path,
    exception: type[TelegramConflictError] | type[TelegramNetworkError],
    status: str,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "ready.json"
        readiness = PollingReadiness(path)
        readiness.write("ready")
        method = GetUpdates()
        request = AsyncMock(
            side_effect=exception(
                method=cast(TelegramMethod[Any], method), message="failure"
            )
        )
        with pytest.raises(exception):
            await readiness(request, Bot("123456:TEST_TOKEN"), method)
        state = json.loads(path.read_text())
        assert state["status"] == status
        assert state["error"] == exception.__name__
        request.side_effect = None
        request.return_value = []
        await readiness(request, Bot("123456:TEST_TOKEN"), method)
        assert json.loads(path.read_text())["status"] == "ready"

    asyncio.run(scenario())

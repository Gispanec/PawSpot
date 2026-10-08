import argparse
import asyncio
import logging
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.types import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from pawspot_bot.backend_client import BackendClient
from pawspot_bot.config import BotSettings
from pawspot_bot.flow import BotFlow, create_dispatcher
from pawspot_bot.readiness import PollingReadiness


async def configure_menu_button(bot: Bot, mini_app_url: str) -> None:
    if mini_app_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🐾 Открыть PawSpot", web_app=WebAppInfo(url=mini_app_url)
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def run(ready_file: Path | None = None) -> None:
    settings = BotSettings()  # type: ignore[call-arg]
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    backend = BackendClient(
        settings.bot_backend_url, settings.internal_service_token.get_secret_value()
    )
    readiness = PollingReadiness(ready_file) if ready_file else None
    if readiness:
        readiness.write("starting")
        bot.session.middleware(readiness)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(settings.bot_backend_url.rstrip("/") + "/ready")
            response.raise_for_status()
            if response.json().get("status") != "ready":
                raise RuntimeError("Backend is not ready")
        if (await bot.get_webhook_info()).url:
            raise RuntimeError("Webhook is configured; polling cannot start")
        await configure_menu_button(bot, settings.mini_app_url)
        dispatcher = create_dispatcher(
            BotFlow(bot, backend, settings.max_photo_bytes, settings.mini_app_url)
        )
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    except Exception as exc:
        if readiness:
            readiness.write("failed", type(exc).__name__)
        raise
    else:
        if readiness:
            readiness.write("stopped")
    finally:
        await backend.close()
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", type=Path)
    asyncio.run(run(parser.parse_args().ready_file))

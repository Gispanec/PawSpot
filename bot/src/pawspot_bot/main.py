import asyncio
import logging

from aiogram import Bot

from pawspot_bot.backend_client import BackendClient
from pawspot_bot.config import BotSettings
from pawspot_bot.flow import BotFlow, create_dispatcher


async def run() -> None:
    settings = BotSettings()  # type: ignore[call-arg]
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    backend = BackendClient(
        settings.bot_backend_url, settings.internal_service_token.get_secret_value()
    )
    try:
        dispatcher = create_dispatcher(BotFlow(bot, backend, settings.max_photo_bytes))
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await backend.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import Response, TelegramType


class PollingReadiness(BaseRequestMiddleware):
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, status: str, error: str | None = None) -> None:
        payload = {
            "pid": os.getpid(),
            "updated_at": datetime.now(UTC).isoformat(),
            "status": status,
            "error": error,
        }
        # Замена файла не даёт launcher прочитать незавершённый JSON.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(self.path)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        polling = isinstance(method, GetUpdates)
        if not polling:
            return await make_request(bot, method)
        try:
            result = await make_request(bot, method)
        except TelegramAPIError as exc:
            self.write(
                "failed" if isinstance(exc, TelegramConflictError) else "waiting",
                type(exc).__name__,
            )
            raise
        self.write("ready")
        return result

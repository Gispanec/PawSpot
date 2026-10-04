import asyncio
import io
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.types import File, Update

from pawspot_bot.backend_client import BackendClient, BackendError
from pawspot_bot.flow import ADD, BotFlow, choice, create_dispatcher


class FakeBackend:
    def __init__(self, *, candidates: bool = False) -> None:
        self.draft: dict[str, Any] | None = None
        self.commits = 0
        self.uploads = 0
        self.candidates = candidates
        self.events: list[str] = []
        self.fail_upload: int | None = None

    async def current(self, user_id: int, name: str) -> dict[str, Any] | None:
        return self.draft

    async def create(self, user_id: int, name: str) -> dict[str, Any]:
        if self.draft is None:
            self.draft = {
                "public_id": str(uuid4()),
                "version": 0,
                "state": "need_photo",
                "species": None,
                "location_present": False,
                "selection": None,
                "new_name": None,
                "comment": None,
            }
        return self.draft

    async def upload(
        self, draft: dict[str, Any], user_id: int, name: str, photo: bytes
    ) -> dict[str, Any]:
        if self.fail_upload:
            raise BackendError(self.fail_upload)
        assert photo == b"photo"
        self.uploads += 1
        draft["state"] = "need_species"
        draft["version"] += 1
        self.events.append("photo")
        return draft

    async def patch(
        self, draft: dict[str, Any], user_id: int, name: str, **fields: Any
    ) -> dict[str, Any]:
        if "species" in fields:
            draft["species"] = fields["species"]
            draft["state"] = "need_location_or_skip"
        if "location" in fields:
            draft["location_present"] = fields["location"] is not None
            draft["state"] = "choose_animal"
        if "new_animal" in fields:
            draft["selection"] = "new"
            draft["new_name"] = fields["new_animal"].get("name")
            draft["state"] = "ready"
        if "animal_public_id" in fields:
            draft["selection"] = "existing"
            draft["state"] = "ready"
        if "comment" in fields:
            draft["comment"] = fields["comment"]
        draft["version"] += 1
        self.events.extend(fields)
        return draft

    async def matches(
        self, draft: dict[str, Any], user_id: int, name: str
    ) -> list[dict[str, Any]]:
        self.events.append("matches")
        if self.candidates:
            return [
                {
                    "animal_public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "name": "Гиви",
                    "species": draft["species"],
                }
            ]
        return []

    async def collection(self, user_id: int, name: str) -> list[dict[str, Any]]:
        self.events.append("collection")
        return []

    async def commit(
        self, draft: dict[str, Any], user_id: int, name: str
    ) -> dict[str, Any]:
        if draft["state"] != "committed":
            self.commits += 1
            draft["state"] = "committed"
        return {
            "species": draft["species"],
            "animal_name": draft["new_name"],
            "photo_public_id": str(uuid4()),
            "comment": draft["comment"],
        }

    async def commit_id(
        self, draft_id: str, version: int, user_id: int, name: str
    ) -> dict[str, Any]:
        if self.draft is None or self.draft["public_id"].replace("-", "") != draft_id:
            raise BackendError(404)
        return await self.commit(self.draft, user_id, name)

    async def cancel(self, draft: dict[str, Any], user_id: int, name: str) -> None:
        self.draft = None

    async def photo(self, photo_id: str, user_id: int, name: str) -> bytes:
        return b"thumbnail"


class Harness:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.bot = Bot("123456:TEST_TOKEN")
        self.flow = BotFlow(self.bot, cast(BackendClient, backend), 10 * 1024 * 1024)
        self.dispatcher = create_dispatcher(self.flow)
        self.messages: list[str] = []
        self.markups: list[Any] = []
        self.sent_photos: list[bytes] = []
        self.sequence = 0

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append(text)
        self.markups.append(kwargs.get("reply_markup"))

    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any) -> None:
        self.sent_photos.append(photo.data)
        self.messages.append(kwargs["caption"])

    async def get_file(self, file_id: str) -> File:
        return File(file_id=file_id, file_unique_id="unique", file_path="/x")

    async def download_file(
        self, file_path: str, destination: io.BytesIO
    ) -> io.BytesIO:
        destination.write(b"photo")
        return destination

    async def feed(
        self,
        *,
        text: str | None = None,
        photo: bool = False,
        location: bool = False,
        callback: str | None = None,
    ) -> None:
        self.sequence += 1
        message: dict[str, Any] = {
            "message_id": self.sequence,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Тест"},
        }
        if text is not None:
            message["text"] = text
        if photo:
            message["photo"] = [
                {
                    "file_id": "file",
                    "file_unique_id": "unique",
                    "width": 100,
                    "height": 100,
                }
            ]
        if location:
            message["location"] = {"latitude": 41.71, "longitude": 44.82}
        if callback is None:
            raw = {"update_id": self.sequence, "message": message}
        else:
            raw = {
                "update_id": self.sequence,
                "callback_query": {
                    "id": str(self.sequence),
                    "from": message["from"],
                    "chat_instance": "test",
                    "message": {**message, "text": "Кнопки"},
                    "data": callback,
                },
            }
        update = Update.model_validate(raw, context={"bot": self.bot})
        await self.dispatcher.feed_update(self.bot, update)

    def button(self, label: str) -> str:
        markup = self.markups[-1]
        for row in markup.inline_keyboard:
            if row[0].text == label:
                return cast(str, row[0].callback_data)
        raise AssertionError(f"Missing button {label}")


async def scenario(backend: FakeBackend, actions: str) -> Harness:
    harness = Harness(backend)
    with (
        patch.object(harness.bot, "send_message", side_effect=harness.send_message),
        patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
        patch.object(harness.bot, "get_file", side_effect=harness.get_file),
        patch.object(harness.bot, "download_file", side_effect=harness.download_file),
        patch.object(Bot, "__call__", new_callable=AsyncMock),
    ):
        await harness.feed(text="/start")
        await harness.feed(text=ADD)
        await harness.feed(photo=True)
        await harness.feed(callback=harness.button("🐕 Собака"))
        if "no_location" in actions:
            await harness.feed(text="Пропустить")
        else:
            await harness.feed(location=True)
        if "existing" in actions:
            await harness.feed(callback=harness.button("🐕 Гиви"))
        else:
            await harness.feed(callback=harness.button("🆕 Новое животное"))
            if "name" in actions:
                await harness.feed(text="Бондо")
            else:
                await harness.feed(callback=harness.button("Пропустить"))
        if "comment" in actions:
            await harness.feed(text="У пекарни")
        else:
            await harness.feed(callback=harness.button("Пропустить"))
        save_button = harness.button("💾 Сохранить")
        await harness.feed(callback=save_button)
        await harness.feed(callback=save_button)
    return harness


@pytest.mark.parametrize(
    ("actions", "candidates"),
    [("name comment", False), ("existing", True), ("no_location", False)],
)
def test_telegram_happy_paths(actions: str, candidates: bool) -> None:
    backend = FakeBackend(candidates=candidates)
    harness = asyncio.run(scenario(backend, actions))
    assert backend.commits == 1
    assert backend.uploads == 1
    assert harness.sent_photos == [b"thumbnail"]
    assert any("Встреча сохранена" in message for message in harness.messages)
    assert ("matches" in backend.events) == ("no_location" not in actions)
    assert ("collection" in backend.events) == ("no_location" in actions)
    assert backend.draft is not None
    assert backend.draft["new_name"] == ("Бондо" if "name" in actions else None)
    assert backend.draft["comment"] == ("У пекарни" if "comment" in actions else None)


def test_restart_resume_cancel_and_stale_callback() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=ADD)
            await harness.feed(text="/start")
            assert backend.draft is not None
            await harness.feed(photo=True)
            old = choice("species", backend.draft, "dog")
            await harness.feed(callback=old)
            await harness.feed(callback=old)
            assert any("устарела" in message for message in harness.messages)
            await harness.feed(text="/cancel")
            assert backend.draft is None
            await harness.feed(text=ADD)
            await harness.feed(callback=old)
            assert backend.draft is not None
            assert backend.draft["state"] == "need_photo"

    asyncio.run(run())


def test_blocked_user_gets_human_message() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(backend, "current", side_effect=BackendError(403)),
        ):
            await harness.feed(text="/start")
        assert "закрытому пилоту" in harness.messages[-1]

    asyncio.run(run())


def test_invalid_photo_can_be_retried() -> None:
    async def run() -> None:
        backend = FakeBackend()
        backend.fail_upload = 415
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
        ):
            await harness.feed(photo=True)
            assert "Фото не подошло" in harness.messages[-1]
            assert backend.draft is not None
            assert backend.draft["state"] == "need_photo"
            backend.fail_upload = None
            await harness.feed(photo=True)
            assert backend.draft["state"] == "need_species"

    asyncio.run(run())


def test_backend_unavailable_has_retry_message() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(backend, "current", side_effect=BackendError()),
        ):
            await harness.feed(text="/start")
        assert "временно недоступен" in harness.messages[-1]

    asyncio.run(run())


def test_telegram_photo_download_failure_keeps_draft() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(
                harness.bot, "get_file", side_effect=OSError("download failed")
            ),
        ):
            await harness.feed(photo=True)
        assert "Не удалось скачать фото" in harness.messages[-1]
        assert backend.uploads == 0
        assert backend.draft is not None
        assert backend.draft["state"] == "need_photo"

    asyncio.run(run())

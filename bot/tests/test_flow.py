import asyncio
import io
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.types import File, Update

from pawspot_bot.backend_client import BackendClient, BackendError
from pawspot_bot.flow import (
    ABOUT,
    ADD,
    CANCEL,
    TODAY,
    BotFlow,
    choice,
    create_dispatcher,
)
from pawspot_bot.main import configure_menu_button


class FakeBackend:
    def __init__(self, *, candidates: bool = False) -> None:
        self.draft: dict[str, Any] | None = None
        self.commits = 0
        self.creates = 0
        self.cancels = 0
        self.uploads = 0
        self.candidates = candidates
        self.events: list[str] = []
        self.fail_upload: int | None = None
        self.today_pages: list[int] = []

    async def current(self, user_id: int, name: str) -> dict[str, Any] | None:
        return self.draft if self.draft and self.draft["state"] != "committed" else None

    async def create(self, user_id: int, name: str) -> dict[str, Any]:
        if self.draft is None or self.draft["state"] == "committed":
            self.creates += 1
            self.draft = {
                "public_id": str(uuid4()),
                "version": 0,
                "state": "need_photo",
                "photo_public_id": None,
                "species": None,
                "location_present": False,
                "city_name": "Tbilisi",
                "city_timezone": "Asia/Tbilisi",
                "observed_at": datetime.now(UTC).isoformat(),
                "selection": None,
                "selected_animal_name": None,
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
        draft["photo_public_id"] = str(uuid4())
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
            if draft["selection"] is None:
                draft["state"] = "choose_animal"
        if "new_animal" in fields:
            draft["selection"] = "new"
            draft["new_name"] = fields["new_animal"].get("name")
            draft["state"] = "ready"
        if "animal_public_id" in fields:
            draft["selection"] = "existing"
            draft["selected_animal_name"] = "Гиви"
            draft["state"] = "ready"
        if "comment" in fields:
            draft["comment"] = fields["comment"]
        if "observed_at" in fields:
            draft["observed_at"] = fields["observed_at"]
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
                    "thumbnail_photo_id": str(uuid4()),
                    "last_observed_at": "2026-10-03T12:00:00+00:00",
                    "encounter_count": 3,
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
            draft["encounter_public_id"] = str(uuid4())
        return {
            "encounter_public_id": draft["encounter_public_id"],
            "species": draft["species"],
            "animal_name": (
                draft["new_name"]
                if draft["selection"] == "new"
                else draft["selected_animal_name"]
            ),
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
        self.cancels += 1
        self.draft = None

    async def photo(
        self, photo_id: str, user_id: int, name: str, *, variant: str = "thumbnail"
    ) -> bytes:
        return variant.encode()

    async def today(self, user_id: int, name: str, page: int = 1) -> dict[str, Any]:
        self.today_pages.append(page)
        return {
            "timezone": "Asia/Tbilisi",
            "encounters": 51,
            "dogs": 50,
            "cats": 1,
            "first_animals": 2,
            "page": page,
            "page_size": 1,
            "items": [
                {
                    "encounter_public_id": str(uuid4()),
                    "animal_public_id": str(uuid4()),
                    "animal_name": "Гиви",
                    "species": "dog",
                    "photo_public_id": str(uuid4()),
                    "observed_at": datetime.now(UTC).isoformat(),
                    "comment": "У пекарни",
                    "city_name": "Tbilisi",
                    "location_present": True,
                    "repeat_encounter": page > 1,
                }
            ],
        }


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
        self.edited_media: list[Any] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append(text)
        self.markups.append(kwargs.get("reply_markup"))

    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any) -> None:
        self.sent_photos.append(photo.data)
        self.messages.append(kwargs["caption"])
        self.markups.append(kwargs.get("reply_markup"))

    async def edit_message_media(self, **kwargs: Any) -> None:
        self.edited_media.append(kwargs)
        self.messages.append(kwargs["media"].caption)
        self.markups.append(kwargs["reply_markup"])

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


async def scenario(
    backend: FakeBackend, actions: str, *, mini_app_url: str = ""
) -> Harness:
    harness = Harness(backend)
    harness.flow.mini_app_url = mini_app_url
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
            await harness.feed(callback=harness.button("✅ Да, это он"))
        else:
            new_label = (
                "🆕 Нет, это новое животное"
                if backend.candidates and "no_location" not in actions
                else "🆕 Создать новое животное"
            )
            await harness.feed(callback=harness.button(new_label))
            if "name" in actions:
                await harness.feed(text="Бондо")
            else:
                await harness.feed(callback=harness.button("Пропустить"))
        if "comment" in actions:
            await harness.feed(text="У пекарни")
        else:
            await harness.feed(callback=harness.button("Пропустить"))
        save_button = harness.button("✅ Сохранить")
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
    assert harness.sent_photos[-1] == b"thumbnail"
    assert b"main" in harness.sent_photos
    assert any("Встреча сохранена" in message for message in harness.messages)
    assert harness.markups[-1].keyboard[0][0].text == ADD
    assert harness.markups[-1].keyboard[1][0].text == TODAY
    assert harness.markups[-1].keyboard[1][1].text == ABOUT
    assert ("matches" in backend.events) == ("no_location" not in actions)
    assert ("collection" in backend.events) == ("no_location" in actions)
    assert backend.draft is not None
    assert backend.draft["new_name"] == ("Бондо" if "name" in actions else None)
    assert backend.draft["comment"] == ("У пекарни" if "comment" in actions else None)
    preview_buttons = [
        button.text
        for markup in harness.markups
        if hasattr(markup, "inline_keyboard")
        for row in markup.inline_keyboard
        for button in row
    ]
    assert (
        "Изменить заметку" if "comment" in actions else "Добавить заметку"
    ) in preview_buttons


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


def test_mini_app_button_keeps_existing_add_flow() -> None:
    async def run() -> None:
        harness = Harness(FakeBackend())
        harness.flow.mini_app_url = "https://pawspot.example/app"
        with patch.object(
            harness.bot, "send_message", side_effect=harness.send_message
        ):
            await harness.feed(text="/start")
            assert harness.markups[0].keyboard[0][0].text == ADD
            assert harness.markups[0].keyboard[0][0].web_app is None
            button = harness.markups[1].inline_keyboard[0][0]
            assert button.text == "🐾 Открыть PawSpot"
            assert button.web_app is not None
            assert button.web_app.url == "https://pawspot.example/app"
            assert button.callback_data is None
            await harness.feed(text=ADD)
            assert harness.backend.draft is not None
            assert harness.backend.draft["state"] == "need_photo"

    asyncio.run(run())


def test_menu_button_is_configured_for_mini_app() -> None:
    async def run() -> None:
        bot = Bot("123456:TEST_TOKEN")
        try:
            with patch.object(
                bot, "set_chat_menu_button", new_callable=AsyncMock
            ) as setter:
                await configure_menu_button(bot, "https://pawspot.example/app")
                assert setter.await_args is not None
                button = setter.await_args.kwargs["menu_button"]
                assert button.text == "🐾 Открыть PawSpot"
                assert button.web_app.url == "https://pawspot.example/app"
                assert setter.await_args.kwargs.get("chat_id") is None
                setter.reset_mock()
                await configure_menu_button(bot, "")
                assert setter.await_args is not None
                assert setter.await_args.kwargs["menu_button"].type == "commands"
        finally:
            await bot.session.close()

    asyncio.run(run())


def test_about_and_help_keep_inline_mini_app_link() -> None:
    async def run() -> None:
        harness = Harness(FakeBackend())
        harness.flow.mini_app_url = "https://pawspot.example/app"
        with patch.object(
            harness.bot, "send_message", side_effect=harness.send_message
        ):
            await harness.feed(text="/start")
            assert harness.markups[0].keyboard[1][1].text == ABOUT
            await harness.feed(text=ABOUT)
            assert "Точные координаты встречи не публикуются" in harness.messages[-1]
            assert harness.markups[-1].inline_keyboard[0][0].web_app.url == (
                "https://pawspot.example/app"
            )
            await harness.feed(text="/help")
            assert "ваша коллекция" in harness.messages[-1]

    asyncio.run(run())


def test_repeated_add_reuses_draft_without_repeated_prompts() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
        ):
            await harness.feed(text=ADD)
            draft = backend.draft
            assert draft is not None
            first_id = draft["public_id"]
            await harness.feed(text=ADD)
            await harness.feed(text=ADD)
            await asyncio.gather(harness.feed(text=ADD), harness.feed(text=ADD))
            assert (
                sum("Отправьте фото животного" in text for text in harness.messages)
                == 1
            )
            assert draft["public_id"] == first_id
            await harness.feed(photo=True)
            await harness.feed(text=ADD)
            assert harness.messages.count("Кого встретили?") == 1
            assert draft["public_id"] == first_id

    asyncio.run(run())


def test_candidates_show_photo_and_existing_animal_in_preview() -> None:
    backend = FakeBackend(candidates=True)
    harness = asyncio.run(scenario(backend, "existing"))
    assert any("Возможно, его уже встречали" in text for text in harness.messages)
    assert any("Последняя встреча: 03.10.2026" in text for text in harness.messages)
    assert any("всего встреч: 3" in text for text in harness.messages)
    assert any("Гиви (уже встречали)" in text for text in harness.messages)
    assert harness.sent_photos.count(b"main") == 2  # кандидат и итоговый preview
    assert all("private_location" not in text for text in harness.messages)


def test_new_animal_button_matches_candidate_context() -> None:
    async def run(candidates: bool) -> list[str]:
        backend = FakeBackend(candidates=candidates)
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=ADD)
            await harness.feed(photo=True)
            await harness.feed(callback=harness.button("🐕 Собака"))
            await harness.feed(location=True)
        return [row[0].text for row in harness.markups[-1].inline_keyboard]

    assert asyncio.run(run(False)) == ["🆕 Создать новое животное", CANCEL]
    assert asyncio.run(run(True)) == [
        "✅ Да, это он",
        "🆕 Нет, это новое животное",
        CANCEL,
    ]


def test_candidate_can_be_rejected_as_new_animal() -> None:
    backend = FakeBackend(candidates=True)
    harness = asyncio.run(scenario(backend, "name"))
    assert backend.draft is not None
    assert backend.draft["selection"] == "new"
    assert backend.draft["new_name"] == "Бондо"
    assert any("Возможно, его уже встречали" in text for text in harness.messages)
    assert any("Бондо (новое)" in text for text in harness.messages)


def test_candidate_without_photo_cannot_be_confirmed_as_match() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        draft = await backend.create(123, "Тест")
        draft["species"] = "dog"
        with patch.object(
            harness.bot, "send_message", side_effect=harness.send_message
        ):
            await harness.flow.candidate(
                123,
                "Тест",
                draft,
                {
                    "animal_public_id": str(uuid4()),
                    "name": "Гиви",
                    "species": "dog",
                    "thumbnail_photo_id": None,
                },
            )
        assert "нельзя проверить совпадение" in harness.messages[-1]
        assert [row[0].text for row in harness.markups[-1].inline_keyboard] == [
            "🆕 Нет, это новое животное",
            CANCEL,
        ]

    asyncio.run(run())


def test_new_animal_name_and_note_can_be_kept_changed_or_cleared() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=ADD)
            await harness.feed(photo=True)
            await harness.feed(callback=harness.button("🐕 Собака"))
            await harness.feed(location=True)
            assert harness.markups[-2].keyboard[0][0].text == ADD
            await harness.feed(text=ADD)
            assert (
                sum("Совпадений не нашлось" in text for text in harness.messages) == 1
            )
            await harness.feed(callback=harness.button("▶️ Продолжить"))
            await harness.feed(callback=harness.button("🆕 Создать новое животное"))
            await harness.feed(text=ADD)
            assert harness.flow.pending[123] == "name_initial"
            await harness.feed(callback=harness.button("▶️ Продолжить"))
            await harness.feed(text="Бондо")
            await harness.feed(text="У пекарни")
            draft = backend.draft
            assert draft is not None
            assert "Бондо (новое)" in harness.messages[-1]
            assert "📍 Место указано" in harness.messages[-1]
            assert "💬 У пекарни" in harness.messages[-1]
            assert "41.71" not in harness.messages[-1]

            await harness.feed(callback=harness.button("Имя"))
            assert "Текущее имя: Бондо" in harness.messages[-1]
            await harness.feed(callback=harness.button("Оставить имя"))
            assert draft["new_name"] == "Бондо"
            await harness.feed(callback=harness.button("Имя"))
            await harness.feed(callback=harness.button("Изменить имя"))
            await harness.feed(text="Гиви")
            assert draft["new_name"] == "Гиви"
            assert draft["comment"] == "У пекарни"
            assert "Гиви (новое)" in harness.messages[-1]

            await harness.feed(callback=harness.button("Изменить заметку"))
            assert "Текущая заметка: У пекарни" in harness.messages[-1]
            await harness.feed(callback=harness.button("Оставить заметку"))
            assert draft["comment"] == "У пекарни"
            await harness.feed(callback=harness.button("Изменить заметку"))
            await harness.feed(callback=harness.button("Изменить заметку"))
            await harness.feed(text="На лавочке")
            assert draft["comment"] == "На лавочке"
            await harness.feed(callback=harness.button("Изменить заметку"))
            await harness.feed(callback=harness.button("Удалить заметку"))
            assert draft["comment"] is None
            assert "💬 Без заметки" in harness.messages[-1]
            assert harness.button("Добавить заметку")

            save = harness.button("✅ Сохранить")
            await harness.feed(callback=save)
            assert backend.commits == 1
            assert await backend.current(123, "Тест") is None
            await harness.feed(text=ADD)
            assert backend.draft is not None
            assert backend.draft["state"] == "need_photo"

    asyncio.run(run())


def test_draft_values_survive_new_bot_flow_instance() -> None:
    async def run() -> None:
        backend = FakeBackend()
        first = Harness(backend)
        with (
            patch.object(first.bot, "send_message", side_effect=first.send_message),
            patch.object(first.bot, "send_photo", side_effect=first.send_photo),
            patch.object(first.bot, "get_file", side_effect=first.get_file),
            patch.object(first.bot, "download_file", side_effect=first.download_file),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await first.feed(text=ADD)
            await first.feed(photo=True)
            await first.feed(callback=first.button("🐕 Собака"))
            await first.feed(location=True)
            await first.feed(callback=first.button("🆕 Создать новое животное"))
            await first.feed(text="Бондо")
            await first.feed(text="У пекарни")
        second = Harness(backend)
        with (
            patch.object(second.bot, "send_message", side_effect=second.send_message),
            patch.object(second.bot, "send_photo", side_effect=second.send_photo),
        ):
            await second.feed(text="/start")
            assert "Бондо (новое)" in second.messages[-1]
            assert "💬 У пекарни" in second.messages[-1]

    asyncio.run(run())


def test_delayed_encounter_time_and_place_edits_preserve_draft() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=ADD)
            await harness.feed(photo=True)
            await harness.feed(callback=harness.button("🐕 Собака"))
            assert "Где вы встретили животное" in harness.messages[-1]
            await harness.feed(callback=harness.button("🗺 Указать другое место"))
            assert "Выбрать место на карте" in harness.messages[-1]
            await harness.feed(location=True)
            await harness.feed(callback=harness.button("🆕 Создать новое животное"))
            await harness.feed(text="Бондо")
            await harness.feed(text="У пекарни")
            assert "🕒 Сегодня" in harness.messages[-1]
            await harness.feed(callback=harness.button("Изменить время"))
            await harness.feed(callback=harness.button("Вчера · в это время"))
            assert "🕒 Вчера" in harness.messages[-1]
            await harness.feed(callback=harness.button("Изменить время"))
            await harness.feed(callback=harness.button("Ввести дату и время"))
            await harness.feed(text="31.12.2099 15:20")
            assert "не может быть в будущем" in harness.messages[-1]
            await harness.feed(text="03.10.2026 15:20")
            assert "15:20" in harness.messages[-1]
            await harness.feed(callback=harness.button("Изменить место"))
            assert harness.button("Оставить место")
            await harness.feed(callback=harness.button("⏭ Без места"))
            assert "📍 Без места" in harness.messages[-1]
            assert backend.draft is not None
            assert backend.draft["new_name"] == "Бондо"
            assert backend.draft["comment"] == "У пекарни"
            assert backend.draft["location_present"] is False
            assert backend.draft["observed_at"].startswith("2026-10-03")

    asyncio.run(run())


def test_manual_time_reports_specific_errors_and_keeps_draft() -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(
            state="ready",
            species="dog",
            selection="new",
            new_name="Бондо",
            comment="У пекарни",
            location_present=True,
            photo_public_id=str(uuid4()),
        )
        original_time = draft["observed_at"]
        harness = Harness(backend)
        harness.flow.pending[123] = "time_manual"
        zone = ZoneInfo("Asia/Tbilisi")
        future = (datetime.now(zone) + timedelta(days=1)).strftime("%d.%m.%Y %H:%M")
        past = (datetime.now(zone) - timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
        invalid_cases = [
            ("03.10.2026", "Не удалось распознать"),
            ("03/10/2026 15:20", "Не удалось распознать"),
            ("03.20.2026 14:96", "не существует"),
            ("03.20.2026 14:30", "не существует"),
            ("31.02.2026 15:20", "не существует"),
            ("03.10.2026 25:10", "не существует"),
            ("03.10.2026 14:96", "не существует"),
            (future, "не может быть в будущем"),
        ]
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
        ):
            for value, expected in invalid_cases:
                await harness.feed(text=value)
                assert expected in harness.messages[-1]
                assert draft["observed_at"] == original_time
                assert draft["new_name"] == "Бондо"
                assert draft["comment"] == "У пекарни"
                assert draft["location_present"] is True
                assert harness.flow.pending[123] == "time_manual"
            await harness.feed(text=past)
        assert datetime.fromisoformat(draft["observed_at"]) < datetime.now(zone)
        assert draft["new_name"] == "Бондо"
        assert draft["comment"] == "У пекарни"
        assert draft["location_present"] is True
        assert harness.flow.pending.get(123) is None
        assert "🕒" in harness.messages[-1]

    asyncio.run(run())


def test_today_feed_uses_one_editable_photo_card_for_51_encounters() -> None:
    async def run() -> None:
        backend = FakeBackend()
        harness = Harness(backend)
        harness.flow.mini_app_url = "https://pawspot.example/app"
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(
                harness.bot,
                "edit_message_media",
                side_effect=harness.edit_message_media,
            ),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=TODAY)
            assert "Встреч: 51" in harness.messages[0]
            assert "Собаки: 50" in harness.messages[0]
            assert "Кошки: 1" in harness.messages[0]
            assert "Впервые добавлено животных: 2" in harness.messages[0]
            assert "1 / 51" == harness.markups[-1].inline_keyboard[0][0].text
            assert (
                harness.markups[-1]
                .inline_keyboard[1][0]
                .web_app.url.startswith("https://pawspot.example/encounter/")
            )
            await harness.feed(callback="today:2")
            assert backend.today_pages == [1, 2]
            assert len(harness.sent_photos) == 1
            assert len(harness.edited_media) == 1
            assert "🔁 Повторная встреча" in harness.messages[-1]
            assert "2 / 51" in [
                button.text for button in harness.markups[-1].inline_keyboard[0]
            ]
            assert "private_location" not in "".join(harness.messages)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("state", "pending"),
    [
        ("need_photo", None),
        ("need_species", None),
        ("need_location_or_skip", None),
        ("need_location_or_skip", "current_location"),
        ("need_location_or_skip", "manual_location"),
        ("choose_animal", None),
        ("ready", "name_initial"),
        ("ready", "name_edit"),
        ("ready", "name_choice"),
        ("ready", "comment"),
        ("ready", "comment_choice"),
        ("ready", "time_choice"),
        ("ready", "time_manual"),
        ("ready", "place_choice"),
        ("ready", "current_location"),
        ("ready", "manual_location"),
        ("ready", None),
    ],
)
def test_resume_and_visible_cancel_at_every_draft_stage(
    state: str, pending: str | None
) -> None:
    async def run() -> None:
        backend = FakeBackend(candidates=True)
        draft = await backend.create(123, "Тест")
        draft.update(
            state=state,
            species="dog",
            photo_public_id=str(uuid4()),
            selection="new" if state == "ready" else None,
            new_name=None if pending == "name_initial" else "Бондо",
            comment="У пекарни",
            location_present=True,
        )
        original = dict(draft)
        harness = Harness(backend)
        if pending:
            harness.flow.pending[123] = pending
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.flow.resume(123, "Тест", draft)
            expected_prompt = harness.messages[-1]
            await harness.feed(text=ADD)
            assert "незавершённая встреча" in harness.messages[-1]
            assert backend.creates == 1
            await harness.feed(callback=harness.button("▶️ Продолжить"))
            assert harness.messages[-1] == expected_prompt
            assert draft == original
            markup = harness.markups[-1]
            if hasattr(markup, "inline_keyboard"):
                await harness.feed(callback=harness.button(CANCEL))
            else:
                assert any(b.text == CANCEL for row in markup.keyboard for b in row)
                await harness.feed(text=CANCEL)
            assert harness.messages[-1] == "Добавление встречи отменено."
            assert await backend.current(123, "Тест") is None
            assert backend.commits == 0
            assert backend.cancels == 1
            assert 123 not in harness.flow.pending
            assert 123 not in harness.flow.last_prompt
            await harness.feed(text=ADD)
            assert backend.draft is not None
            assert backend.draft["public_id"] != original["public_id"]
            assert backend.draft["state"] == "need_photo"
            assert backend.draft["photo_public_id"] is None

    asyncio.run(run())


def test_repeated_add_at_matching_and_confirmed_restart() -> None:
    async def run() -> None:
        backend = FakeBackend(candidates=True)
        harness = Harness(backend)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(harness.bot, "get_file", side_effect=harness.get_file),
            patch.object(
                harness.bot, "download_file", side_effect=harness.download_file
            ),
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=ADD)
            await harness.feed(photo=True)
            await harness.feed(callback=harness.button("🐕 Собака"))
            await harness.feed(location=True)
            candidate = harness.messages[-1]
            draft = backend.draft
            assert draft is not None
            await harness.feed(text=ADD)
            await harness.feed(callback=harness.button("🔄 Начать заново"))
            assert "будет удалена" in harness.messages[-1]
            assert backend.cancels == 0
            assert backend.creates == 1
            await harness.feed(callback=harness.button("↩️ Нет, продолжить"))
            assert harness.messages[-1] == candidate
            assert backend.draft is draft
            await harness.feed(text=ADD)
            await harness.feed(callback=harness.button("🔄 Начать заново"))
            confirmation = harness.button("✅ Да, начать заново")
            await harness.feed(callback=confirmation)
            assert backend.cancels == 1
            assert backend.creates == 2
            assert backend.commits == 0
            assert backend.draft is not None and backend.draft is not draft
            assert backend.draft["state"] == "need_photo"
            await harness.feed(callback=confirmation)
            assert backend.cancels == 1
            assert backend.creates == 2

    asyncio.run(run())

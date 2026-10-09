import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from pawspot_bot.backend_client import BackendError
from pawspot_bot.flow import ABOUT, ADD, TODAY, choice
from test_flow import FakeBackend, Harness, scenario

MINI_APP_URL = "https://pawspot.example/app?discard=yes#old-screen"


def assert_menu(markup: Any) -> None:
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert [[button.text for button in row] for row in markup.keyboard] == [
        [ADD],
        [TODAY, ABOUT],
    ]


def assert_encounter_button(markup: Any, encounter_id: str) -> None:
    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 1
    assert len(markup.inline_keyboard[0]) == 1
    button = markup.inline_keyboard[0][0]
    assert button.text == "🐾 Открыть встречу"
    assert button.web_app is not None
    assert button.web_app.url == f"https://pawspot.example/encounter/{encounter_id}"
    assert button.callback_data is None


@pytest.mark.parametrize("existing", [False, True])
def test_saved_photo_opens_new_encounter_and_restores_menu(existing: bool) -> None:
    backend = FakeBackend(candidates=existing)
    harness = asyncio.run(
        scenario(
            backend,
            "existing" if existing else "name comment",
            mini_app_url=MINI_APP_URL,
        )
    )
    assert backend.draft is not None
    assert backend.draft["selection"] == ("existing" if existing else "new")
    assert backend.commits == 1
    assert harness.sent_photos[-1] == b"thumbnail"
    expected_caption = (
        "🐕 Гиви\nВстреча сохранена в PawSpot."
        if existing
        else "🐕 Бондо\nВстреча сохранена в PawSpot.\nУ пекарни"
    )
    assert harness.messages[-1] == expected_caption
    assert "Встреча сохранена в PawSpot." in harness.messages[-1]
    assert sum("Встреча сохранена" in text for text in harness.messages) == 1
    assert_menu(harness.markups[-2])
    assert harness.messages[-2] == "✅ Готово"
    assert harness.messages.count("✅ Готово") == 1
    assert_encounter_button(harness.markups[-1], backend.draft["encounter_public_id"])


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://pawspot.example",
        "not-a-url",
        "https://",
        "https:///app",
        "https://bad host/app",
        "https://pawspot.example:invalid/app",
        "https://[broken/app",
        "https://user:password@pawspot.example/app",
    ],
)
def test_missing_or_invalid_url_keeps_one_photo_confirmation_with_menu(
    url: str,
) -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="ready", species="cat", selection="new")
        result = await backend.commit(draft, 123, "Тест")
        harness = Harness(backend)
        harness.flow.mini_app_url = url
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
        ):
            await harness.flow.result(123, "Тест", result)
        assert len(harness.messages) == 1
        assert harness.sent_photos == [b"thumbnail"]
        assert "Встреча сохранена" in harness.messages[0]
        assert_menu(harness.markups[0])

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["menu", "photo", "download", "fallback", "all"])
def test_notification_errors_never_repeat_commit(failure: str) -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="ready", species="dog", selection="new", version=3)
        save = choice("save", draft)
        harness = Harness(backend)
        harness.flow.mini_app_url = MINI_APP_URL

        async def send_message(chat_id: int, text: str, **kwargs: Any) -> None:
            if (
                failure == "all"
                or (failure == "menu" and text == "✅ Готово")
                or (failure == "fallback" and "Встреча сохранена" in text)
            ):
                raise TelegramBadRequest(
                    method=SendMessage(chat_id=chat_id, text=text),
                    message="Simulated send failure",
                )
            await harness.send_message(chat_id, text, **kwargs)

        async def send_photo(chat_id: int, photo: Any, **kwargs: Any) -> None:
            if failure in {"photo", "fallback", "all"}:
                raise TelegramBadRequest(
                    method=SendPhoto(chat_id=chat_id, photo=photo),
                    message="Simulated photo failure",
                )
            await harness.send_photo(chat_id, photo, **kwargs)

        async def photo(*args: Any, **kwargs: Any) -> bytes:
            if failure == "download":
                raise BackendError(503)
            return b"thumbnail"

        with (
            patch.object(harness.bot, "send_message", side_effect=send_message),
            patch.object(harness.bot, "send_photo", side_effect=send_photo),
            patch.object(backend, "photo", side_effect=photo),
            patch.object(backend, "commit_id", wraps=backend.commit_id) as commit,
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            await harness.feed(callback=save)
            await harness.feed(callback=save)
            commit.assert_awaited_once()
        assert backend.commits == 1
        assert draft["state"] == "committed"
        assert await backend.current(123, "Тест") is None
        assert harness.flow.delivered[123] == draft["public_id"].replace("-", "")
        if failure in {"menu", "photo", "download"}:
            assert "Встреча сохранена" in harness.messages[-1]
            assert_encounter_button(harness.markups[-1], draft["encounter_public_id"])
        if failure == "menu":
            assert harness.sent_photos == [b"thumbnail"]
            assert len(harness.messages) == 1
        elif failure in {"photo", "download"}:
            assert harness.sent_photos == []
            assert len(harness.messages) == 2
            assert harness.messages[0] == "✅ Готово"
            assert_menu(harness.markups[0])

    asyncio.run(run())


def test_photo_failure_without_url_falls_back_to_confirmation_with_menu() -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="ready", species="cat", selection="new")
        result = await backend.commit(draft, 123, "Тест")
        harness = Harness(backend)
        with (
            patch.object(
                harness.bot,
                "send_photo",
                side_effect=TelegramBadRequest(
                    method=SendPhoto(chat_id=123, photo="photo"), message="Unavailable"
                ),
            ),
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
        ):
            await harness.flow.result(123, "Тест", result)
        assert len(harness.messages) == 1
        assert "Встреча сохранена" in harness.messages[0]
        assert_menu(harness.markups[0])

    asyncio.run(run())


def test_repeated_save_while_notification_pending_does_not_commit_again() -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="ready", species="cat", selection="new")
        save = choice("save", draft)
        harness = Harness(backend)
        harness.flow.mini_app_url = MINI_APP_URL
        sending = asyncio.Event()
        release = asyncio.Event()

        async def send_message(chat_id: int, text: str, **kwargs: Any) -> None:
            sending.set()
            await release.wait()
            await harness.send_message(chat_id, text, **kwargs)

        with (
            patch.object(harness.bot, "send_message", side_effect=send_message),
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo),
            patch.object(backend, "commit_id", wraps=backend.commit_id) as commit,
            patch.object(Bot, "__call__", new_callable=AsyncMock),
        ):
            first = asyncio.create_task(harness.feed(callback=save))
            try:
                await asyncio.wait_for(sending.wait(), timeout=2)
                await harness.feed(callback=save)
                commit.assert_awaited_once()
            finally:
                release.set()
                await first
        assert len(harness.messages) == 2
        assert harness.messages[0] == "✅ Готово"
        assert_menu(harness.markups[0])
        assert harness.sent_photos == [b"thumbnail"]
        assert_encounter_button(harness.markups[-1], draft["encounter_public_id"])

    asyncio.run(run())

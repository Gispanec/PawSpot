import asyncio
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageMedia, SendPhoto
from aiogram.types import InputMediaPhoto, Message

from pawspot_bot.backend_client import BackendError
from pawspot_bot.flow import ADD, CANCEL, BotFlow, choice, create_dispatcher
from test_collection_picker import PickerBackend
from test_flow import Harness


class NearbyBackend(PickerBackend):
    def __init__(self) -> None:
        super().__init__()
        self.nearby = [
            {
                "animal_public_id": str(UUID(int=1000 + index)),
                "species": "dog",
                "name": f"Сосед {index + 1}",
                "thumbnail_photo_id": f"nearby-photo-{index}",
                "last_observed_at": "2026-10-09T12:00:00+00:00",
                "encounter_count": 3,
            }
            for index in range(3)
        ]
        self.photos: list[str] = []

    async def matches(
        self, draft: dict[str, Any], user_id: int, name: str
    ) -> list[dict[str, Any]]:
        self.events.append("matches")
        return self.nearby

    async def photo(
        self, photo_id: str, user_id: int, name: str, *, variant: str = "thumbnail"
    ) -> bytes:
        self.photos.append(photo_id)
        return await super().photo(photo_id, user_id, name, variant=variant)


@pytest.fixture
def nearby() -> Iterator[Harness]:
    harness = Harness(NearbyBackend())
    message_id = 100

    def message() -> Message:
        nonlocal message_id
        message_id += 1
        return Message.model_validate(
            {
                "message_id": message_id,
                "date": 0,
                "chat": {"id": 123, "type": "private"},
            }
        )

    async def send_photo(chat_id: int, photo: Any, **kwargs: Any) -> Message:
        await harness.send_photo(chat_id, photo, **kwargs)
        return message()

    async def send_message(chat_id: int, text: str, **kwargs: Any) -> Message:
        await harness.send_message(chat_id, text, **kwargs)
        return message()

    async def edit_text(text: str, **kwargs: Any) -> None:
        harness.messages.append(text)
        harness.markups.append(kwargs["reply_markup"])

    with ExitStack() as stack:
        for method, handler in (
            ("send_message", send_message),
            ("send_photo", send_photo),
            ("edit_message_media", harness.edit_message_media),
            ("edit_message_text", edit_text),
            ("get_file", harness.get_file),
            ("download_file", harness.download_file),
        ):
            stack.enter_context(patch.object(harness.bot, method, side_effect=handler))
        stack.enter_context(patch("aiogram.Bot.__call__", new_callable=AsyncMock))
        yield harness


async def prepare_nearby(harness: Harness) -> None:
    await harness.feed(text=ADD)
    await harness.feed(photo=True)
    await harness.feed(callback=harness.button("🐕 Собака"))
    await harness.feed(location=True)


def test_location_with_three_candidates_does_not_send_photos_automatically(
    nearby: Harness,
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        backend = cast(NearbyBackend, nearby.backend)
        assert len(nearby.sent_photos) == 0
        assert "matches" not in backend.events
        assert not backend.photos
        assert not backend.pages
        assert nearby.messages[-1] == "📍 Место указано. Что делаем дальше?"
        assert [row[0].text for row in nearby.markups[-1].inline_keyboard] == [
            "🔎 Проверить животных рядом",
            "🆕 Это новое животное",
            "🐾 Выбрать из моей коллекции",
            CANCEL,
        ]
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        assert backend.events.count("matches") == 1
        assert len(nearby.sent_photos) == 1
        assert backend.photos == ["nearby-photo-0"]
        assert "1 из 3" in nearby.messages[-1]
        assert backend.draft is not None and backend.draft["selection"] is None
        assert backend.commits == 0

    asyncio.run(run())


def test_navigation_edits_one_card_and_rejects_repeated_and_old_clicks(
    nearby: Harness,
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        check = nearby.button("🔎 Проверить животных рядом")
        await asyncio.gather(nearby.feed(callback=check), nearby.feed(callback=check))
        backend = cast(NearbyBackend, nearby.backend)
        assert backend.events.count("matches") == 1
        state = nearby.flow.nearby[123]
        message_id = state.message_id
        old_yes = nearby.button("✅ Да, это он")
        next_button = nearby.button("➡️ Следующее")
        assert "⬅️ Назад" not in str(nearby.markups[-1])
        entered = asyncio.Event()
        release = asyncio.Event()
        original_photo = backend.photo

        async def slow_photo(
            photo_id: str, user_id: int, name: str, *, variant: str = "thumbnail"
        ) -> bytes:
            entered.set()
            await release.wait()
            return await original_photo(photo_id, user_id, name, variant=variant)

        with patch.object(backend, "photo", side_effect=slow_photo):
            navigation = asyncio.create_task(nearby.feed(callback=next_button))
            await asyncio.wait_for(entered.wait(), timeout=2)
            duplicate = asyncio.create_task(nearby.feed(callback=next_button))
            confirmation = asyncio.create_task(nearby.feed(callback=old_yes))
            release.set()
            await asyncio.gather(navigation, duplicate, confirmation)
        assert state.index == 1
        assert len(nearby.sent_photos) == 1
        assert len(nearby.edited_media) == 1
        assert nearby.edited_media[-1]["message_id"] == message_id
        assert "2 из 3" in nearby.messages[-1]
        assert backend.draft is not None and backend.draft["selection"] is None
        await nearby.feed(callback=nearby.button("➡️ Следующее"))
        assert "3 из 3" in nearby.messages[-1]
        assert "➡️ Следующее" not in str(nearby.markups[-1])
        for target in (3, 99, -1):
            invalid = f"{state.token}.{state.revision}.{target}"
            await nearby.feed(callback=choice("npage", backend.draft, invalid))
        assert state.index == 2
        await nearby.feed(callback=nearby.button("⬅️ Назад"))
        await nearby.feed(callback=nearby.button("⬅️ Назад"))
        assert "1 из 3" in nearby.messages[-1]
        assert len(nearby.sent_photos) == 1
        assert all(edit["message_id"] == message_id for edit in nearby.edited_media)
        for markup in nearby.markups:
            for row in getattr(markup, "inline_keyboard", []):
                for button in row:
                    assert len(button.callback_data.encode()) <= 64
        assert backend.commits == 0

    asyncio.run(run())


@pytest.mark.parametrize("place", ["current", "manual", "skip"])
def test_choose_common_animal_edit_place_and_commit_once(
    nearby: Harness, place: str
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        backend = cast(NearbyBackend, nearby.backend)
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        await nearby.feed(callback=nearby.button("➡️ Следующее"))
        old_yes = nearby.button("✅ Да, это он")
        with patch.object(
            backend, "collection_animal", wraps=backend.collection_animal
        ) as personal:
            await nearby.feed(callback=old_yes)
        personal.assert_not_awaited()
        draft = backend.draft
        assert draft is not None
        assert draft["animal_public_id"] == backend.nearby[1]["animal_public_id"]
        assert draft["animal_public_id"] not in [
            item["animal_public_id"] for item in backend.items
        ]
        assert draft["location_present"]
        assert nearby.flow.pending[123] == "comment"
        await nearby.feed(text="У дерева")
        snapshot = dict(draft)
        await nearby.feed(callback=nearby.button("Изменить место"))
        label = "🗺 Указать другое место" if place == "manual" else "📍 Я ещё здесь"
        await nearby.feed(callback=nearby.button(label))
        if place == "skip":
            await nearby.feed(text="⏭ Без места")
        else:
            await nearby.feed(text="Ррр")
            await nearby.feed(location=True)
        assert draft["state"] == "ready"
        assert draft["location_present"] == (place != "skip")
        for key in snapshot.keys() - {"version", "location_present"}:
            assert draft[key] == snapshot[key]
        assert backend.events.count("matches") == 1
        nearby.flow.mini_app_url = "https://pawspot.example/app"
        save = nearby.button("✅ Сохранить")
        await nearby.feed(callback=save)
        await nearby.feed(callback=save)
        await nearby.feed(callback=old_yes)
        assert backend.commits == 1
        assert "new_animal" not in backend.events
        assert "✅ Готово" in nearby.messages
        assert any("🐾 Открыть встречу" in str(markup) for markup in nearby.markups)
        assert 123 in nearby.flow.reply_menu_users

    asyncio.run(run())


@pytest.mark.parametrize("source", ["initial", "card", "empty"])
def test_new_animal_does_not_require_matching_or_repeat_it(
    nearby: Harness, source: str
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        backend = cast(NearbyBackend, nearby.backend)
        label = "🆕 Это новое животное"
        if source != "initial":
            if source == "empty":
                backend.nearby = []
                label = "🆕 Создать новое животное"
            await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
            if source == "card":
                await nearby.feed(callback=nearby.button("➡️ Следующее"))
                await nearby.feed(callback=nearby.button("➡️ Следующее"))
            else:
                assert nearby.messages[-1] == "Похожих встреч поблизости не найдено."
                assert not nearby.sent_photos
                nearby.button("🐾 Выбрать из моей коллекции")
                nearby.button("↩️ Назад к выбору")
        draft = backend.draft
        assert draft is not None
        snapshot = dict(draft)
        await nearby.feed(callback=nearby.button(label))
        assert nearby.flow.pending[123] == "name_initial"
        assert draft["selection"] == "new"
        assert draft["location_present"]
        assert draft["photo_public_id"] == snapshot["photo_public_id"]
        assert draft["observed_at"] == snapshot["observed_at"]
        assert backend.commits == 0
        await nearby.feed(text="Бондо")
        await nearby.feed(text="У пекарни")
        await nearby.feed(callback=nearby.button("✅ Сохранить"))
        assert backend.commits == 1
        assert backend.events.count("matches") == (0 if source == "initial" else 1)
        assert backend.uploads == 1
        assert backend.creates == 1

    asyncio.run(run())


@pytest.mark.parametrize("empty", [False, True])
def test_back_invalidates_cards_and_restores_choice_without_matching(
    nearby: Harness, empty: bool
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        backend = cast(NearbyBackend, nearby.backend)
        if empty:
            backend.nearby = []
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        old_yes = None if empty else nearby.button("✅ Да, это он")
        draft = backend.draft
        assert draft is not None
        snapshot = dict(draft)
        await nearby.feed(callback=nearby.button("↩️ Назад к выбору"))
        assert nearby.messages[-1] == "📍 Место указано. Что делаем дальше?"
        assert draft == snapshot
        assert backend.events.count("matches") == 1
        if old_yes:
            await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
            await nearby.feed(callback=old_yes)
            assert draft == snapshot
            assert backend.commits == 0

    asyncio.run(run())


@pytest.mark.parametrize("after_nearby", [False, True])
def test_personal_picker_search_and_pagination_keep_location(
    nearby: Harness,
    after_nearby: bool,
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        backend = cast(NearbyBackend, nearby.backend)
        draft = backend.draft
        assert draft is not None
        old_yes = None
        if after_nearby:
            await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
            old_yes = nearby.button("✅ Да, это он")
            await nearby.feed(callback=nearby.button("↩️ Назад к выбору"))
        await nearby.feed(callback=nearby.button("🐾 Выбрать из моей коллекции"))
        assert backend.pages == [("dog", 1, "")]
        if old_yes:
            await nearby.feed(callback=old_yes)
            assert backend.pages == [("dog", 1, "")]
            assert len(nearby.flow.pickers[123].items) == 5
        await nearby.feed(callback=nearby.button("🔎 Найти по имени"))
        await nearby.feed(text="Пёс 14")
        await nearby.feed(callback=nearby.button("➡️ Далее"))
        assert backend.pages[-1] == ("dog", 2, "Пёс 14")
        selected = nearby.markups[-1].inline_keyboard[0][0].callback_data
        await nearby.feed(callback=selected)
        assert len(nearby.sent_photos) == (2 if after_nearby else 1)
        await nearby.feed(callback=nearby.button("✅ Да, это он"))
        assert draft["selection"] == "existing"
        assert draft["location_present"]
        assert backend.events.count("matches") == (1 if after_nearby else 0)
        await nearby.feed(callback=nearby.button("Пропустить"))
        await nearby.feed(callback=nearby.button("✅ Сохранить"))
        assert backend.commits == 1
        assert "new_animal" not in backend.events

    asyncio.run(run())


@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_unavailable_photo_keeps_navigation_and_cannot_confirm(
    nearby: Harness, first: bool, missing: bool
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        backend = cast(NearbyBackend, nearby.backend)
        target = 0 if first else 1
        if missing:
            backend.nearby[target]["thumbnail_photo_id"] = None
        else:
            backend.fail_photo = first
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        if not first:
            backend.fail_photo = not missing
            await nearby.feed(callback=nearby.button("➡️ Следующее"))
        assert "Фото недоступно" in nearby.messages[-1]
        assert "✅ Да, это он" not in str(nearby.markups[-1])
        draft = backend.draft
        assert draft is not None
        state = nearby.flow.nearby[123]
        forged = f"{state.token}.{state.revision}.{state.index}"
        await nearby.feed(callback=choice("nyes", draft, forged))
        assert draft["selection"] is None
        backend.fail_photo = False
        await nearby.feed(callback=nearby.button("➡️ Следующее"))
        assert "✅ Да, это он" in str(nearby.markups[-1])
        assert nearby.edited_media[-1]["message_id"] == state.message_id
        assert backend.events.count("matches") == 1

    asyncio.run(run())


@pytest.mark.parametrize("retire_fails", [False, True])
def test_media_edit_failure_sends_one_fallback_then_edits_it(
    nearby: Harness,
    retire_fails: bool,
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        old_yes = nearby.button("✅ Да, это он")
        error = TelegramBadRequest(
            method=EditMessageMedia(
                chat_id=123, message_id=1, media=InputMediaPhoto(media="x")
            ),
            message="message can't be edited",
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(nearby.bot, "edit_message_media", side_effect=error)
            )
            if retire_fails:
                stack.enter_context(
                    patch.object(
                        nearby.bot, "edit_message_reply_markup", side_effect=error
                    )
                )
            await nearby.feed(callback=nearby.button("➡️ Следующее"))
        assert len(nearby.sent_photos) == 2
        state = nearby.flow.nearby[123]
        fallback_id = state.message_id
        await nearby.feed(callback=old_yes)
        assert nearby.backend.draft is not None
        assert nearby.backend.draft["selection"] is None
        await nearby.feed(callback=nearby.button("➡️ Следующее"))
        assert len(nearby.sent_photos) == 2
        assert nearby.edited_media[-1]["message_id"] == fallback_id

    asyncio.run(run())


def test_photo_send_failure_falls_back_to_text_and_recovers(nearby: Harness) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        error = TelegramBadRequest(
            method=SendPhoto(chat_id=123, photo="x"), message="photo rejected"
        )
        with patch.object(nearby.bot, "send_photo", side_effect=error):
            await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        assert not nearby.sent_photos
        assert "Фото недоступно" in nearby.messages[-1]
        assert "✅ Да, это он" not in str(nearby.markups[-1])
        state = nearby.flow.nearby[123]
        text_id = state.message_id
        await nearby.feed(callback=nearby.button("➡️ Следующее"))
        assert nearby.edited_media[-1]["message_id"] == text_id
        assert state.confirmable

    asyncio.run(run())


@pytest.mark.parametrize("status", [404, 422, 409])
def test_unavailable_or_changed_draft_refreshes_without_matching(
    nearby: Harness, status: int
) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        draft = nearby.backend.draft
        assert draft is not None
        snapshot = dict(draft)
        with patch.object(nearby.backend, "patch", side_effect=BackendError(status)):
            await nearby.feed(callback=nearby.button("✅ Да, это он"))
        assert draft == snapshot
        assert nearby.backend.events.count("matches") == 1
        assert nearby.messages[-1] == "📍 Место указано. Что делаем дальше?"
        assert 123 not in nearby.flow.nearby

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "restart", "resume", "process_restart"])
def test_nearby_draft_lifecycle_and_old_buttons(nearby: Harness, action: str) -> None:
    async def run() -> None:
        await prepare_nearby(nearby)
        await nearby.feed(callback=nearby.button("🔎 Проверить животных рядом"))
        old_yes = nearby.button("✅ Да, это он")
        draft = nearby.backend.draft
        assert draft is not None
        snapshot = dict(draft)
        if action == "cancel":
            await nearby.feed(text=CANCEL)
        elif action == "restart":
            await nearby.feed(text=ADD)
            await nearby.feed(callback=nearby.button("🔄 Начать заново"))
            await nearby.feed(callback=nearby.button("✅ Да, начать заново"))
        else:
            if action == "process_restart":
                nearby.flow = BotFlow(nearby.bot, nearby.flow.backend, 10 * 1024 * 1024)
                nearby.dispatcher = create_dispatcher(nearby.flow)
            await nearby.feed(text="/start")
            assert nearby.messages[-1] == "📍 Место указано. Что делаем дальше?"
        await nearby.feed(callback=old_yes)
        assert nearby.backend.commits == 0
        assert nearby.backend.events.count("matches") == 1
        if action == "cancel":
            assert nearby.backend.draft is None
        elif action == "restart":
            assert nearby.backend.draft is not None
            assert nearby.backend.draft["public_id"] != snapshot["public_id"]
            assert nearby.backend.draft["state"] == "need_photo"
        else:
            assert draft == snapshot

    asyncio.run(run())

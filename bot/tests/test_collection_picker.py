import asyncio
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from pawspot_bot.backend_client import BackendClient, BackendError
from pawspot_bot.flow import ADD, CANCEL, BotFlow, create_dispatcher
from test_flow import FakeBackend, Harness


@pytest.mark.parametrize("skip", ["callback", "text"])
def test_no_location_asks_before_loading_collection(skip: str) -> None:
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
            patch("aiogram.Bot.__call__", new_callable=AsyncMock),
        ):
            await harness.feed(text=ADD)
            await harness.feed(photo=True)
            await harness.feed(callback=harness.button("🐕 Собака"))
            if skip == "callback":
                await harness.feed(callback=harness.button("⏭ Без места"))
            else:
                await harness.feed(text="Пропустить")
        assert "collection" not in backend.events
        assert "matches" not in backend.events
        assert not harness.sent_photos
        assert harness.messages[-1] == "Вы уже встречали это животное раньше?"
        harness.button("🆕 Нет, это новое животное")
        harness.button("🐾 Да, выбрать из коллекции")
        assert backend.commits == 0

    asyncio.run(run())


class PickerBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.pages: list[tuple[str, int, str]] = []
        self.opened: list[str] = []
        self.unavailable = False
        self.fail_photo = False
        self.items: list[dict[str, Any]] = [
            {
                "animal_public_id": str(UUID(int=index + 1)),
                "species": "dog",
                "name": f"Пёс {index + 1}" if index < 149 else None,
                "thumbnail_photo_id": "photo",
                "last_observed_at": "2026-10-08T12:00:00+00:00",
                "encounter_count": 3,
            }
            for index in range(150)
        ]

    async def collection_picker(
        self, user_id: int, name: str, species: str, page: int = 1, q: str = ""
    ) -> dict[str, Any]:
        self.pages.append((species, page, q))
        items = [
            item
            for item in self.items
            if item["species"] == species
            and (not q or q.lower() in str(item["name"] or "").lower())
        ]
        offset = (page - 1) * 5
        return {
            "items": items[offset : offset + 5],
            "page": page,
            "page_size": 5,
            "has_next": offset + 5 < len(items),
        }

    async def collection_animal(
        self, user_id: int, name: str, species: str, animal_id: str
    ) -> dict[str, Any]:
        self.opened.append(animal_id)
        if self.unavailable:
            raise BackendError(404)
        return next(
            i for i in self.items if UUID(i["animal_public_id"]).hex == animal_id
        )

    async def photo(
        self, photo_id: str, user_id: int, name: str, *, variant: str = "thumbnail"
    ) -> bytes:
        if self.fail_photo:
            raise BackendError(404)
        return await super().photo(photo_id, user_id, name, variant=variant)


@pytest.fixture
def picker() -> Iterator[Harness]:
    backend = PickerBackend()
    draft = asyncio.run(backend.create(123, "Тест"))
    draft.update(
        state="choose_animal", species="dog", photo_public_id="draft-photo", version=3
    )
    harness = Harness(backend)

    async def edit_text(text: str, **kwargs: Any) -> None:
        harness.messages.append(text)
        harness.markups.append(kwargs["reply_markup"])

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(harness.bot, "send_message", side_effect=harness.send_message)
        )
        stack.enter_context(
            patch.object(harness.bot, "send_photo", side_effect=harness.send_photo)
        )
        stack.enter_context(
            patch.object(harness.bot, "edit_message_text", side_effect=edit_text)
        )
        stack.enter_context(patch("aiogram.Bot.__call__", new_callable=AsyncMock))
        yield harness


def first_animal(harness: Harness) -> str:
    return cast(str, harness.markups[-1].inline_keyboard[0][0].callback_data)


def test_pages_open_only_one_photo_and_confirm_before_selection(
    picker: Harness,
) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        assert not picker.sent_photos
        assert len(picker.flow.pickers[123].items) == 5
        for _ in range(29):
            await picker.feed(callback=picker.button("➡️ Далее"))
        assert backend.pages[-1] == ("dog", 30, "")
        assert "Страница 30" in picker.messages[-1]
        assert "Далее" not in str(picker.markups[-1])
        assert any(i["name"] is None for i in picker.flow.pickers[123].items)
        data = first_animal(picker)
        await picker.feed(callback=data)
        await picker.feed(callback=data)
        assert len(picker.sent_photos) == 1
        assert backend.draft is not None
        assert backend.draft["selection"] is None
        assert backend.commits == 0
        confirm = picker.button("✅ Да, это он")
        await picker.feed(callback=confirm)
        assert backend.draft["selection"] == "existing"
        assert (
            backend.draft["animal_public_id"]
            == UUID(backend.items[145]["animal_public_id"]).hex
        )
        assert picker.flow.pending[123] == "comment"
        await picker.feed(callback=picker.button("Пропустить"))
        await picker.feed(callback=picker.button("✅ Сохранить"))
        await picker.feed(callback=confirm)
        assert backend.commits == 1
        assert backend.creates == 1

    asyncio.run(run())


def test_search_pagination_clear_and_return(picker: Harness) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        await picker.feed(callback=picker.button("🔎 Найти по имени"))
        await picker.feed(text="  Пёс 14  ")
        assert backend.pages[-1] == ("dog", 1, "Пёс 14")
        await picker.feed(callback=picker.button("➡️ Далее"))
        assert backend.pages[-1] == ("dog", 2, "Пёс 14")
        await picker.feed(callback=first_animal(picker))
        await picker.feed(callback=picker.button("↩️ Вернуться к списку"))
        assert backend.pages[-1] == ("dog", 2, "Пёс 14")
        await picker.feed(callback=picker.button("Сбросить поиск"))
        assert backend.pages[-1] == ("dog", 1, "")
        assert backend.draft is not None
        assert backend.draft["new_name"] is None
        assert backend.draft["comment"] is None
        await picker.feed(callback=picker.button("↩️ К выбору"))
        assert picker.messages[-1] == "Вы уже встречали это животное раньше?"

    asyncio.run(run())


@pytest.mark.parametrize("card", [False, True])
def test_unexpected_text_does_not_reload_collection(
    picker: Harness, card: bool
) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        if card:
            await picker.feed(callback=first_animal(picker))
        assert backend.draft is not None
        snapshot = dict(backend.draft)
        pages = list(backend.pages)
        opened = list(backend.opened)
        photos = len(picker.sent_photos)
        prompts = dict(picker.flow.last_prompt)
        for text in ("Ррр", "Другой текст"):
            count = len(picker.messages)
            await picker.feed(text=text)
            assert picker.messages[count:] == [
                "Выберите вариант с помощью кнопок выше."
            ]
            assert backend.draft == snapshot
            assert backend.pages == pages
            assert backend.opened == opened
            assert len(picker.sent_photos) == photos
            assert picker.flow.last_prompt == prompts

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["question", "list", "search", "card"])
def test_cancel_and_old_buttons_do_not_change_replacement(
    picker: Harness, stage: str
) -> None:
    async def run() -> None:
        await picker.feed(text="/start")
        old_list = picker.button("🐾 Да, выбрать из коллекции")
        if stage != "question":
            await picker.feed(callback=old_list)
        if stage == "search":
            await picker.feed(callback=picker.button("🔎 Найти по имени"))
        if stage == "card":
            await picker.feed(callback=first_animal(picker))
        await picker.feed(callback=picker.button(CANCEL))
        assert not picker.flow.pickers
        assert not picker.flow.pending
        await picker.feed(text=ADD)
        await picker.feed(callback=old_list)
        assert picker.backend.draft is not None
        assert picker.backend.draft["state"] == "need_photo"
        assert picker.backend.commits == 0
        assert picker.backend.cancels == 1

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["list", "search", "card"])
def test_resume_after_process_restart_keeps_draft(picker: Harness, stage: str) -> None:
    async def run() -> None:
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        old = first_animal(picker)
        if stage == "search":
            await picker.feed(callback=picker.button("🔎 Найти по имени"))
        if stage == "card":
            await picker.feed(callback=old)
            old = picker.button("✅ Да, это он")
        picker.flow = BotFlow(
            picker.bot, cast(BackendClient, picker.backend), 10 * 1024 * 1024
        )
        picker.dispatcher = create_dispatcher(picker.flow)
        await picker.feed(text="/start")
        assert picker.messages[-1] == "Вы уже встречали это животное раньше?"
        await picker.feed(callback=old)
        assert picker.backend.draft is not None
        assert picker.backend.draft["selection"] is None
        assert picker.backend.draft["photo_public_id"] == "draft-photo"
        assert picker.backend.creates == 1
        assert picker.backend.commits == 0

    asyncio.run(run())


def test_missing_photo_requires_explicit_confirmation(picker: Harness) -> None:
    async def run() -> None:
        cast(PickerBackend, picker.backend).fail_photo = True
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        await picker.feed(callback=first_animal(picker))
        assert "Фото недоступно" in picker.messages[-1]
        assert picker.backend.draft is not None
        assert picker.backend.draft["selection"] is None
        await picker.feed(callback=picker.button("✅ Да, это он"))
        assert picker.backend.draft["selection"] == "existing"

    asyncio.run(run())


@pytest.mark.parametrize("when", ["open", "confirm"])
def test_unavailable_animal_returns_to_current_list(picker: Harness, when: str) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        if when == "open":
            backend.unavailable = True
            await picker.feed(callback=first_animal(picker))
        else:
            await picker.feed(callback=first_animal(picker))
            with patch.object(backend, "patch", side_effect=BackendError(422)):
                await picker.feed(callback=picker.button("✅ Да, это он"))
        assert any("больше недоступно" in m for m in picker.messages)
        assert backend.draft is not None
        assert backend.draft["selection"] is None
        assert picker.button("🔎 Найти по имени")

    asyncio.run(run())


def test_empty_invalid_search_backend_error_and_callback_size(picker: Harness) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        await picker.feed(callback=picker.button("🔎 Найти по имени"))
        count = len(backend.pages)
        await picker.feed(text=" " * 10)
        await picker.feed(text="a" * 81)
        assert len(backend.pages) == count
        await picker.feed(text="უცნობი%_")
        assert "Ничего не найдено" in picker.messages[-1]
        assert picker.button("Сбросить поиск")
        with patch.object(backend, "collection_picker", side_effect=BackendError(503)):
            await picker.feed(callback=picker.button("Сбросить поиск"))
        assert "временно недоступен" in picker.messages[-1]
        for markup in picker.markups:
            if hasattr(markup, "inline_keyboard"):
                for row in markup.inline_keyboard:
                    for button in row:
                        assert len(button.callback_data.encode()) <= 64
        assert backend.commits == 0

    asyncio.run(run())


def test_stale_version_and_species_discard_picker(picker: Harness) -> None:
    async def run() -> None:
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        old = first_animal(picker)
        assert picker.backend.draft is not None
        picker.backend.draft.update(species="cat", version=4)
        await picker.feed(callback=old)
        assert not picker.flow.pickers
        assert picker.backend.draft["selection"] is None
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        assert "пока нет животных этого вида" in picker.messages[-1]
        await picker.feed(callback=picker.button("🆕 Это новое животное"))
        assert picker.flow.pending[123] == "name_initial"
        assert not picker.flow.pickers

    asyncio.run(run())


def test_preview_no_location_keeps_existing_animal(picker: Harness) -> None:
    async def run() -> None:
        draft = picker.backend.draft
        assert draft is not None
        draft.update(
            state="ready",
            selection="existing",
            animal_public_id="known",
            location_present=True,
        )
        await picker.flow.confirm(123, "Тест", draft)
        await picker.feed(callback=picker.button("Изменить место"))
        await picker.feed(callback=picker.button("⏭ Без места"))
        assert draft["animal_public_id"] == "known"
        assert draft["selection"] == "existing"
        assert not draft["location_present"]
        assert not cast(PickerBackend, picker.backend).pages
        assert "Проверьте встречу" in picker.messages[-1]

    asyncio.run(run())


def test_same_names_are_distinguished_and_dates_match_card(picker: Harness) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        for item in backend.items[:5]:
            item["name"] = "Бондо"
            item["last_observed_at"] = "2026-10-08T22:00:00+00:00"
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        labels = [row[0].text for row in picker.markups[-1].inline_keyboard[:5]]
        assert len(set(labels)) == 5
        assert all("09.10" in label for label in labels)
        await picker.feed(callback=first_animal(picker))
        assert "09.10.2026" in picker.messages[-1]
        assert "#00000001" in picker.messages[-1]

    asyncio.run(run())


def test_shrinking_collection_returns_to_first_page(picker: Harness) -> None:
    async def run() -> None:
        backend = cast(PickerBackend, picker.backend)
        await picker.feed(text="/start")
        await picker.feed(callback=picker.button("🐾 Да, выбрать из коллекции"))
        next_page = picker.button("➡️ Далее")
        backend.items = []
        await picker.feed(callback=next_page)
        assert backend.pages[-2:] == [("dog", 2, ""), ("dog", 1, "")]
        assert "Страница 1" in picker.messages[-1]
        assert "пока нет животных" in picker.messages[-1]
        assert "Далее" not in str(picker.markups[-1])

    asyncio.run(run())

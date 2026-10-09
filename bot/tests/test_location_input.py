import asyncio
from collections.abc import Iterator
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import ReplyKeyboardMarkup

from pawspot_bot.flow import ADD, CANCEL, SKIP, WITHOUT_LOCATION
from test_flow import FakeBackend, Harness


@pytest.fixture
def location_flow() -> Iterator[Harness]:
    harness = Harness(FakeBackend())
    with ExitStack() as stack:
        for method in ("send_message", "send_photo", "get_file", "download_file"):
            stack.enter_context(
                patch.object(harness.bot, method, side_effect=getattr(harness, method))
            )
        stack.enter_context(patch("aiogram.Bot.__call__", new_callable=AsyncMock))
        yield harness


async def prepare_location(harness: Harness, preview: bool = False) -> None:
    await harness.feed(text=ADD)
    await harness.feed(photo=True)
    await harness.feed(callback=harness.button("🐕 Собака"))
    if preview:
        harness.backend.candidates = True
        await harness.feed(location=True)
        await harness.feed(callback=harness.button("🔎 Проверить животных рядом"))
        await harness.feed(callback=harness.button("✅ Да, это он"))
        await harness.feed(text="У пекарни")
        await harness.feed(callback=harness.button("Изменить место"))


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("manual", [False, True])
def test_invalid_location_text_keeps_draft_and_accepts_location(
    location_flow: Harness,
    preview: bool,
    manual: bool,
) -> None:
    async def run() -> None:
        harness = location_flow
        await prepare_location(harness, preview)
        label = "🗺 Указать другое место" if manual else "📍 Я ещё здесь"
        await harness.feed(callback=harness.button(label))
        draft = harness.backend.draft
        assert draft is not None
        snapshot = dict(draft)
        prompts = dict(harness.flow.last_prompt)
        events = list(harness.backend.events)
        photos = len(harness.sent_photos)
        for text in ("Ррр", "Не то сообщение"):
            count = len(harness.messages)
            await harness.feed(text=text)
            hint = (
                "Отправьте точку через скрепку → Геопозиция → Выбрать место "
                "на карте. Или вернитесь к preview."
                if manual
                else "📍 Сейчас ожидаю геопозицию. Нажмите «Отправить текущую точку» "
                "внизу или выберите «⏭ Без места»."
            )
            assert harness.messages[count:] == [hint]
            assert draft == snapshot
            assert harness.flow.pending[123] == (
                "manual_location" if manual else "current_location"
            )
            assert harness.flow.last_prompt == prompts
            assert harness.backend.events == events
            assert len(harness.sent_photos) == photos
        count = len(harness.messages)
        await harness.feed(location=True)
        assert len(harness.messages) - count == 2
        assert draft["location_present"]
        assert draft["state"] == ("ready" if preview else "choose_animal")
        assert 123 not in harness.flow.pending
        assert harness.backend.commits == 0

    asyncio.run(run())


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("skip", [WITHOUT_LOCATION, SKIP])
def test_skip_current_location_restores_menu_and_preserves_selection(
    location_flow: Harness,
    preview: bool,
    skip: str,
) -> None:
    async def run() -> None:
        harness = location_flow
        await prepare_location(harness, preview)
        old_noloc = harness.button(WITHOUT_LOCATION)
        await harness.feed(callback=harness.button("📍 Я ещё здесь"))
        markup = harness.markups[-1]
        assert isinstance(markup, ReplyKeyboardMarkup)
        assert [[button.text for button in row] for row in markup.keyboard] == [
            ["📍 Отправить текущую точку"],
            [WITHOUT_LOCATION],
            [CANCEL],
        ]
        assert markup.keyboard[0][0].request_location is True
        assert markup.keyboard[1][0].request_location is None
        draft = harness.backend.draft
        assert draft is not None
        snapshot = dict(draft)
        events = len(harness.backend.events)
        photos = len(harness.sent_photos)
        count = len(harness.messages)
        with patch.object(
            harness.backend, "patch", wraps=harness.backend.patch
        ) as patch_draft:
            await harness.feed(text=skip)
        assert patch_draft.await_count == 1
        assert patch_draft.call_args.kwargs == {"location": None}
        assert harness.messages[count] == "📍 Место не указано"
        assert len(harness.messages) - count == 2
        assert harness.markups[-2] == harness.flow.active_menu()
        assert 123 in harness.flow.reply_menu_users
        assert 123 not in harness.flow.pending
        assert harness.backend.events[events:] == ["location"]
        assert harness.backend.creates == 1
        assert not draft["location_present"]
        assert draft["state"] == ("ready" if preview else "choose_animal")
        assert len(harness.sent_photos) - photos == (1 if preview else 0)
        for key in snapshot.keys() - {"version", "location_present", "state"}:
            assert draft[key] == snapshot[key]
        if not preview:
            assert harness.messages[-1] == "Вы уже встречали это животное раньше?"
        snapshot = dict(draft)
        await harness.feed(callback=old_noloc)
        await harness.feed(text=WITHOUT_LOCATION)
        assert draft == snapshot
        if preview:
            assert draft["selection"] == "existing"
            await harness.feed(callback=harness.button("✅ Сохранить"))
            assert harness.backend.commits == 1
            assert draft["selection"] == "existing"
            assert "new_animal" not in harness.backend.events

    asyncio.run(run())


@pytest.mark.parametrize(
    "stage",
    [
        "need_photo",
        "need_species",
        "need_location_or_skip",
        "choose_animal",
        "choose_nearby",
    ],
)
def test_unexpected_text_gets_one_hint_without_rendering(
    location_flow: Harness,
    stage: str,
) -> None:
    async def run() -> None:
        harness = location_flow
        await harness.feed(text=ADD)
        if stage != "need_photo":
            await harness.feed(photo=True)
        if stage not in {"need_photo", "need_species"}:
            await harness.feed(callback=harness.button("🐕 Собака"))
        if stage == "choose_animal":
            await harness.feed(callback=harness.button(WITHOUT_LOCATION))
        if stage == "choose_nearby":
            harness.backend.candidates = True
            await harness.feed(location=True)
            await harness.feed(callback=harness.button("🔎 Проверить животных рядом"))
        draft = harness.backend.draft
        assert draft is not None
        snapshot = dict(draft)
        pending = dict(harness.flow.pending)
        prompts = dict(harness.flow.last_prompt)
        events = list(harness.backend.events)
        photos = len(harness.sent_photos)
        hints = {
            "need_photo": "📸 Отправьте фотографию животного, чтобы продолжить.",
            "need_species": "Выберите кошку или собаку с помощью кнопок выше.",
            "need_location_or_skip": (
                "📍 Выберите место с помощью кнопок выше или продолжите без места."
            ),
            "choose_animal": "Выберите вариант с помощью кнопок выше.",
            "choose_nearby": "Выберите вариант с помощью кнопок выше.",
        }
        invalid_texts = ["Ррр", "Ещё текст"]
        if stage != "need_location_or_skip":
            invalid_texts.append(WITHOUT_LOCATION)
        for text in invalid_texts:
            count = len(harness.messages)
            await harness.feed(text=text)
            assert harness.messages[count:] == [hints[stage]]
            assert draft == snapshot
            assert harness.flow.pending == pending
            assert harness.flow.last_prompt == prompts
            assert harness.backend.events == events
            assert len(harness.sent_photos) == photos

    asyncio.run(run())


def test_skip_label_remains_valid_name_and_comment(location_flow: Harness) -> None:
    async def run() -> None:
        harness = location_flow
        await prepare_location(harness)
        await harness.feed(text=WITHOUT_LOCATION)
        await harness.feed(callback=harness.button("🆕 Нет, это новое животное"))
        draft = harness.backend.draft
        assert draft is not None
        events = len(harness.backend.events)
        await harness.feed(text=WITHOUT_LOCATION)
        assert draft["new_name"] == WITHOUT_LOCATION
        await harness.feed(text=WITHOUT_LOCATION)
        assert draft["comment"] == WITHOUT_LOCATION
        assert harness.backend.events[events:] == ["new_animal", "comment"]
        assert draft["state"] == "ready"

    asyncio.run(run())


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("command", [CANCEL, "/cancel", "/start", ADD])
def test_location_commands_and_resume(
    location_flow: Harness, manual: bool, command: str
) -> None:
    async def run() -> None:
        harness = location_flow
        await prepare_location(harness)
        label = "🗺 Указать другое место" if manual else "📍 Я ещё здесь"
        await harness.feed(callback=harness.button(label))
        draft = harness.backend.draft
        assert draft is not None
        await harness.feed(text="Ррр")
        await harness.feed(text=command)
        if command in {CANCEL, "/cancel"}:
            assert harness.backend.draft is None
            assert harness.backend.cancels == 1
            assert 123 not in harness.flow.pending
            return
        if command == ADD:
            await harness.feed(callback=harness.button("▶️ Продолжить"))
        assert harness.flow.pending[123] == (
            "manual_location" if manual else "current_location"
        )
        await harness.feed(location=True)
        assert draft["state"] == "choose_animal"
        assert draft["location_present"]
        assert harness.backend.creates == 1

    asyncio.run(run())

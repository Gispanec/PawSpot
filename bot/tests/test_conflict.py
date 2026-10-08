import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pawspot_bot.backend_client import BackendClient, BackendError
from pawspot_bot.flow import ADD, CANCEL, choice
from test_flow import FakeBackend, Harness


def test_backend_conflict_refreshes_draft_and_current_buttons() -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="need_species", photo_public_id="photo", version=1)
        harness = Harness(backend)
        old = choice("species", draft, "dog")
        harness.flow.pending[123] = "name_edit"

        async def conflict(*args: Any, **kwargs: Any) -> dict[str, Any]:
            backend.draft = {
                **draft,
                "version": 2,
                "species": "cat",
                "state": "need_location_or_skip",
            }
            raise BackendError(409)

        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(backend, "current", wraps=backend.current) as current,
            patch.object(backend, "patch", side_effect=conflict) as mutation,
            patch.object(
                harness.bot, "edit_message_reply_markup", new_callable=AsyncMock
            ) as keyboard,
            patch("aiogram.Bot.__call__", new_callable=AsyncMock),
        ):
            await harness.feed(callback=old)
            keyboard.assert_awaited_once_with(
                chat_id=123, message_id=1, reply_markup=None
            )
            assert current.await_count == 2
            assert current.await_args is not None
            assert current.await_args.args == (123, "Тест")
            assert backend.draft is not None
            assert harness.button("📍 Я ещё здесь") == choice("here", backend.draft)
            assert harness.button(CANCEL) == choice("cancel", backend.draft)
            assert 123 not in harness.flow.pending
            await harness.feed(callback=old)
            assert mutation.await_count == 1
            assert harness.button("📍 Я ещё здесь") == choice("here", backend.draft)
        assert backend.creates == 1
        assert backend.commits == backend.cancels == 0
        assert backend.draft["species"] == "cat"

    asyncio.run(run())


def test_http_409_recovery_through_real_backend_client() -> None:
    async def run() -> None:
        state = FakeBackend()
        draft = await state.create(123, "Тест")
        draft.update(state="need_species", version=1)
        old = choice("species", draft, "dog")
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET" and request.url.path.endswith("/current"):
                return httpx.Response(200, json=state.draft)
            assert request.method == "PATCH"
            state.draft = {
                **draft,
                "species": "cat",
                "version": 2,
                "state": "need_location_or_skip",
            }
            return httpx.Response(409, json={"detail": "Draft version changed"})

        client = BackendClient("http://backend.test", "synthetic-service-secret")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://backend.test", transport=httpx.MockTransport(handle)
        )
        harness = Harness(state)
        harness.flow.backend = client
        try:
            with (
                patch.object(
                    harness.bot, "send_message", side_effect=harness.send_message
                ),
                patch("aiogram.Bot.__call__", new_callable=AsyncMock),
            ):
                await harness.feed(callback=old)
            assert [request.method for request in requests] == ["GET", "PATCH", "GET"]
            assert state.draft is not None
            assert harness.button("📍 Я ещё здесь") == choice("here", state.draft)
            assert state.creates == 1
            assert state.commits == state.cancels == 0
        finally:
            await client.close()

    asyncio.run(run())


def test_save_conflict_forces_current_preview_even_if_already_rendered() -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(
            state="ready",
            species="cat",
            selection="new",
            new_name="Мур",
            version=4,
            photo_public_id=None,
        )
        harness = Harness(backend)
        harness.flow.last_prompt[123] = harness.flow.prompt_key(draft, "ready")
        harness.flow.pending[123] = "name_edit"
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(backend, "current", wraps=backend.current) as current,
            patch.object(backend, "commit_id", side_effect=BackendError(409)) as commit,
            patch("aiogram.Bot.__call__", new_callable=AsyncMock),
        ):
            await harness.feed(callback=choice("save", draft))
            current.assert_awaited_once_with(123, "Тест")
            commit.assert_awaited_once()
        assert "Мур" in harness.messages[-1]
        assert harness.button("✅ Сохранить") == choice("save", draft)
        assert 123 not in harness.flow.pending
        assert backend.creates == 1
        assert backend.commits == backend.cancels == 0

    asyncio.run(run())


@pytest.mark.parametrize("status", [None, 403, 404, 410, 503, 409])
def test_conflict_refresh_handles_missing_or_unavailable_draft(
    status: int | None,
) -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="need_species", version=1)
        harness = Harness(backend)
        harness.flow.pending[123] = "name_edit"
        harness.flow.last_prompt[123] = harness.flow.prompt_key(draft, "need_species")
        missing: Any = None if status is None else BackendError(status)
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(backend, "current", side_effect=[draft, missing]) as current,
            patch.object(backend, "patch", side_effect=BackendError(409)),
            patch("aiogram.Bot.__call__", new_callable=AsyncMock),
        ):
            await harness.feed(callback=choice("species", draft, "cat"))
            assert current.await_count == 2
        assert 123 not in harness.flow.pending
        assert 123 not in harness.flow.last_prompt
        assert backend.creates == 1
        assert backend.commits == backend.cancels == 0
        if status in {None, 404, 410}:
            assert "Добавить встречу" in harness.messages[-1]
            assert harness.markups[-1].keyboard[0][0].text == ADD
        elif status == 403:
            assert "закрытому пилоту" in harness.messages[-1]
        else:
            assert "не удалось" in harness.messages[-1].lower()
            assert "/start" in harness.messages[-1]
        assert not any(
            "Показываю его текущее состояние" in msg for msg in harness.messages
        )

    asyncio.run(run())


def test_local_stale_callback_always_reissues_current_step_without_mutation() -> None:
    async def run() -> None:
        backend = FakeBackend()
        draft = await backend.create(123, "Тест")
        draft.update(state="need_species", version=1)
        old = choice("species", draft, "dog")
        draft["version"] = 2
        harness = Harness(backend)
        harness.flow.last_prompt[123] = harness.flow.prompt_key(draft, "need_species")
        with (
            patch.object(harness.bot, "send_message", side_effect=harness.send_message),
            patch.object(backend, "patch", wraps=backend.patch) as mutation,
            patch("aiogram.Bot.__call__", new_callable=AsyncMock),
        ):
            await harness.feed(callback=old)
            mutation.assert_not_awaited()
        assert harness.button("🐈 Кот") == choice("species", draft, "cat")
        assert backend.creates == 1
        assert backend.commits == backend.cancels == 0

    asyncio.run(run())

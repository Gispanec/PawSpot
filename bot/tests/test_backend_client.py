import asyncio
import base64

import httpx
import pytest

from pawspot_bot.backend_client import BackendClient, BackendError


def test_internal_headers_and_draft_contract() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"public_id": "draft", "version": 0})

    async def run() -> None:
        client = BackendClient("http://backend.test", "service-secret")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://backend.test", transport=httpx.MockTransport(handle)
        )
        try:
            assert (await client.create(123, "Гиви"))["public_id"] == "draft"
        finally:
            await client.close()

    asyncio.run(run())
    request = requests[0]
    assert request.url.path == "/internal/v1/encounter-drafts"
    assert request.headers["x-pawspot-service-token"] == "service-secret"
    assert request.headers["x-pawspot-telegram-id"] == "123"
    assert (
        base64.urlsafe_b64decode(request.headers["x-pawspot-display-name-b64"])
        == "Гиви".encode()
    )


@pytest.mark.parametrize("status", [403, 409, 410, 415, 503])
def test_backend_status_is_preserved(status: int) -> None:
    async def run() -> None:
        client = BackendClient("http://backend.test", "secret")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://backend.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json={"detail": "private"})
            ),
        )
        try:
            with pytest.raises(BackendError) as info:
                await client.create(123, "Тест")
            assert info.value.status_code == status
            assert "private" not in str(info.value)
        finally:
            await client.close()

    asyncio.run(run())


def test_collection_picker_encodes_search_and_uses_internal_actor() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [], "has_next": False})

    async def run() -> None:
        client = BackendClient("http://backend.test", "service-secret")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://backend.test", transport=httpx.MockTransport(handle)
        )
        try:
            await client.collection_picker(123, "Гиви", "dog", 30, " Красн%_&ბონ ")
            await client.collection_animal(123, "Гиви", "dog", "animal-id")
        finally:
            await client.close()

    asyncio.run(run())
    page, detail = requests
    assert page.url.path == "/internal/v1/users/me/collection-picker"
    assert page.url.params["page"] == "30"
    assert page.url.params["page_size"] == "5"
    assert page.url.params["species"] == "dog"
    assert page.url.params["q"] == " Красн%_&ბონ "
    assert page.headers["x-pawspot-telegram-id"] == "123"
    assert page.headers["x-pawspot-service-token"] == "service-secret"
    assert detail.url.path == "/internal/v1/users/me/collection-picker/animal-id"
    assert detail.url.params["species"] == "dog"

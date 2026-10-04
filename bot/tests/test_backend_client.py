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

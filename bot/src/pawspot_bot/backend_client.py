import base64
from typing import Any, cast

import httpx


class BackendError(Exception):
    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(f"PawSpot backend error: {status_code}")


class BackendClient:
    def __init__(self, base_url: str, service_token: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._token = service_token

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        telegram_id: int,
        display_name: str,
        *,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        encoded_name = base64.urlsafe_b64encode(display_name[:64].encode()).decode()
        headers = {
            "X-Pawspot-Service-Token": self._token,
            "X-Pawspot-Telegram-Id": str(telegram_id),
            "X-Pawspot-Display-Name-B64": encoded_name,
        }
        try:
            response = await self._client.request(
                method, path, headers=headers, json=json, files=files, data=data
            )
        except httpx.RequestError as exc:
            raise BackendError() from exc
        if response.status_code >= 400:
            raise BackendError(response.status_code)
        if response.status_code == 204:
            return None
        if response.headers.get("content-type", "").startswith("image/"):
            return response.content
        return response.json()

    async def current(self, user_id: int, name: str) -> dict[str, Any] | None:
        try:
            return cast(
                dict[str, Any],
                await self.request(
                    "GET", "/internal/v1/encounter-drafts/current", user_id, name
                ),
            )
        except BackendError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def create(self, user_id: int, name: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request("POST", "/internal/v1/encounter-drafts", user_id, name),
        )

    async def patch(
        self, draft: dict[str, Any], user_id: int, name: str, **fields: Any
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "PATCH",
                f"/internal/v1/encounter-drafts/{draft['public_id']}",
                user_id,
                name,
                json={"expected_version": draft["version"], **fields},
            ),
        )

    async def upload(
        self, draft: dict[str, Any], user_id: int, name: str, photo: bytes
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "PUT",
                f"/internal/v1/encounter-drafts/{draft['public_id']}/photo",
                user_id,
                name,
                files={"file": ("telegram.jpg", photo, "image/jpeg")},
                data={"expected_version": str(draft["version"])},
            ),
        )

    async def matches(
        self, draft: dict[str, Any], user_id: int, name: str
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request(
                "POST",
                f"/internal/v1/encounter-drafts/{draft['public_id']}/matches",
                user_id,
                name,
            ),
        )

    async def collection(self, user_id: int, name: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request(
                "GET", "/internal/v1/users/me/collection", user_id, name
            ),
        )

    async def commit(
        self, draft: dict[str, Any], user_id: int, name: str
    ) -> dict[str, Any]:
        return await self.commit_id(
            str(draft["public_id"]), int(draft["version"]), user_id, name
        )

    async def commit_id(
        self, draft_id: str, version: int, user_id: int, name: str
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "POST",
                f"/internal/v1/encounter-drafts/{draft_id}/commit",
                user_id,
                name,
                json={"expected_version": version},
            ),
        )

    async def cancel(self, draft: dict[str, Any], user_id: int, name: str) -> None:
        await self.request(
            "DELETE",
            f"/internal/v1/encounter-drafts/{draft['public_id']}",
            user_id,
            name,
        )

    async def photo(self, photo_id: str, user_id: int, name: str) -> bytes:
        return cast(
            bytes,
            await self.request(
                "GET", f"/internal/v1/photos/{photo_id}/thumbnail", user_id, name
            ),
        )

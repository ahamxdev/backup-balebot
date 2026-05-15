from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)


class BaleAPIError(RuntimeError):
    pass


class BaleClient:
    def __init__(self, api_base: str, token: str, timeout_seconds: float) -> None:
        self._api_base = api_base.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def _url(self, method_name: str) -> str:
        return f"{self._api_base}/bot{self._token}/{method_name}"

    def _call(
        self,
        method_name: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = self._url(method_name)
        try:
            response = self._session.post(
                url,
                data=data,
                files=files,
                timeout=timeout or self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise BaleAPIError(f"network error calling {method_name}: {exc}") from exc

        if response.status_code >= 500:
            raise BaleAPIError(
                f"temporary API error {response.status_code} on {method_name}: {response.text[:200]}"
            )

        if response.status_code >= 400:
            raise BaleAPIError(
                f"request failed {response.status_code} on {method_name}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise BaleAPIError(f"invalid JSON from {method_name}: {response.text[:300]}") from exc

        if not payload.get("ok", False):
            raise BaleAPIError(
                f"API rejected {method_name}: "
                f"error_code={payload.get('error_code')} "
                f"description={payload.get('description')}"
            )

        return payload.get("result")

    def delete_webhook(self) -> bool:
        result = self._call("deleteWebhook")
        return bool(result)

    def send_document(
        self,
        chat_id: str,
        file_path: Path,
        caption: str,
        filename: str | None = None,
    ) -> dict[str, Any]:
        upload_name = filename or file_path.name
        with file_path.open("rb") as handle:
            files = {
                "document": (
                    upload_name,
                    handle,
                    "application/octet-stream",
                )
            }
            data = {"chat_id": chat_id, "caption": caption}
            result = self._call("sendDocument", data=data, files=files)

        if not isinstance(result, dict):
            raise BaleAPIError("sendDocument response shape is unexpected")
        return result

    def get_updates(self, offset: int | None, timeout_seconds: int = 30) -> list[dict[str, Any]]:
        data: dict[str, Any] = {"timeout": timeout_seconds, "limit": 100}
        if offset is not None:
            data["offset"] = offset

        result = self._call("getUpdates", data=data, timeout=timeout_seconds + 10)
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        LOGGER.warning("getUpdates response is not a list")
        return []

    def close(self) -> None:
        self._session.close()

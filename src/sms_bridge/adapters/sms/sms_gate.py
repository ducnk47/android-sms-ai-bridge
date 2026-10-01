from typing import Any
from uuid import UUID

import httpx

from sms_bridge.adapters.sms.base import SmsGatewayError, SmsSendResult


class SmsGateLocalClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client: httpx.AsyncClient,
        priority: int = 100,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(username, password)
        self._client = client
        self._priority = priority

    async def health(self) -> bool:
        response = await self._request("GET", "/health", successful_statuses={200})
        payload = self._json_object(response)
        return payload.get("status") in {"pass", "warn"}

    async def send(self, request_id: UUID, recipient: str, text: str) -> SmsSendResult:
        response = await self._request(
            "POST",
            "/message",
            successful_statuses={200, 201},
            json={
                "id": str(request_id),
                "textMessage": {"text": text},
                "phoneNumbers": [recipient],
                "withDeliveryReport": True,
                "priority": self._priority,
            },
        )
        payload = self._json_object(response)
        response_id = payload.get("id")
        message_id = (
            response_id
            if isinstance(response_id, str) and response_id
            else str(request_id)
        )
        return SmsSendResult(message_id=message_id, accepted=True)

    async def register_webhook(
        self,
        url: str,
        event: str = "sms:received",
    ) -> str:
        response = await self._request(
            "POST",
            "/webhooks",
            successful_statuses={200, 201},
            json={"url": url, "event": event},
        )
        payload = self._json_object(response)
        webhook_id = payload.get("id")
        if not isinstance(webhook_id, str) or not webhook_id:
            raise SmsGatewayError("protocol")
        return webhook_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        successful_statuses: set[int],
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                auth=self._auth,
                json=json,
            )
        except httpx.TimeoutException as error:
            raise SmsGatewayError("timeout") from error
        except httpx.TransportError as error:
            raise SmsGatewayError("transport") from error

        if response.status_code not in successful_statuses:
            raise SmsGatewayError("http")
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise SmsGatewayError("protocol") from error
        if not isinstance(payload, dict):
            raise SmsGatewayError("protocol")
        return payload

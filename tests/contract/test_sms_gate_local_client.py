import base64
import json
from uuid import uuid4

import httpx
import pytest
import respx

from sms_bridge.adapters.sms.base import SmsGatewayError
from sms_bridge.adapters.sms.sms_gate import SmsGateLocalClient

BASE_URL = "http://127.0.0.1:8081"


def _expected_auth() -> str:
    encoded = base64.b64encode(b"gateway-user:gateway-password").decode()
    return f"Basic {encoded}"


@pytest.mark.parametrize("status", ["pass", "warn"])
@respx.mock
async def test_health_uses_basic_auth_and_accepts_pass_or_warn(status: str) -> None:
    route = respx.get(f"{BASE_URL}/health").mock(
        return_value=httpx.Response(200, json={"status": status})
    )
    async with httpx.AsyncClient() as http_client:
        gateway = SmsGateLocalClient(
            BASE_URL,
            "gateway-user",
            "gateway-password",
            http_client,
        )

        assert await gateway.health() is True

    assert route.calls[0].request.headers["Authorization"] == _expected_auth()


@respx.mock
async def test_health_rejects_an_unknown_healthy_status() -> None:
    respx.get(f"{BASE_URL}/health").mock(
        return_value=httpx.Response(200, json={"status": "unknown"})
    )
    async with httpx.AsyncClient() as http_client:
        gateway = SmsGateLocalClient(BASE_URL, "user", "password", http_client)

        assert await gateway.health() is False


@respx.mock
async def test_send_uses_local_message_endpoint_and_exact_contract() -> None:
    request_id = uuid4()
    local_route = respx.post(f"{BASE_URL}/message").mock(
        return_value=httpx.Response(201, json={"id": "android-message-01"})
    )
    cloud_route = respx.post(f"{BASE_URL}/3rdparty/v1/messages").mock(
        return_value=httpx.Response(500)
    )
    async with httpx.AsyncClient() as http_client:
        gateway = SmsGateLocalClient(
            BASE_URL,
            "gateway-user",
            "gateway-password",
            http_client,
        )

        result = await gateway.send(request_id, "+84901234567", "Trả lời")

    assert result.accepted is True
    assert result.message_id == "android-message-01"
    assert local_route.called is True
    assert cloud_route.called is False
    request = local_route.calls[0].request
    assert request.headers["Authorization"] == _expected_auth()
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == (
        b'{"id":"'
        + str(request_id).encode()
        + b'","textMessage":{"text":"Tr\xe1\xba\xa3 l\xe1\xbb\x9di"},'
        b'"phoneNumbers":["+84901234567"],"withDeliveryReport":true,"priority":100}'
    )


@respx.mock
async def test_retry_reuses_request_id_and_success_can_fall_back_to_it() -> None:
    request_id = uuid4()
    route = respx.post(f"{BASE_URL}/message").mock(
        side_effect=[
            httpx.ReadTimeout("timed out"),
            httpx.Response(200, json={"accepted": True}),
        ]
    )
    async with httpx.AsyncClient() as http_client:
        gateway = SmsGateLocalClient(BASE_URL, "user", "password", http_client)

        with pytest.raises(SmsGatewayError) as first_error:
            await gateway.send(request_id, "+84901234567", "retry")
        result = await gateway.send(request_id, "+84901234567", "retry")

    assert first_error.value.category == "timeout"
    assert result.message_id == str(request_id)
    assert [json.loads(call.request.content)["id"] for call in route.calls] == [
        str(request_id),
        str(request_id),
    ]


@respx.mock
async def test_register_webhook_uses_android_loopback_url() -> None:
    route = respx.post(f"{BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json={"id": "webhook-01"})
    )
    async with httpx.AsyncClient() as http_client:
        gateway = SmsGateLocalClient(BASE_URL, "user", "password", http_client)

        webhook_id = await gateway.register_webhook(
            "http://127.0.0.1:9876/webhooks/sms-gate"
        )

    assert webhook_id == "webhook-01"
    assert json.loads(route.calls[0].request.content) == {
        "url": "http://127.0.0.1:9876/webhooks/sms-gate",
        "event": "sms:received",
    }


@pytest.mark.parametrize(
    ("response", "category"),
    [
        (httpx.Response(503, json={"password": "gateway-password"}), "http"),
        (httpx.Response(200, content=b"not-json"), "protocol"),
    ],
)
@respx.mock
async def test_send_categorizes_invalid_responses_without_leaking_credentials(
    response: httpx.Response,
    category: str,
) -> None:
    respx.post(f"{BASE_URL}/message").mock(return_value=response)
    async with httpx.AsyncClient() as http_client:
        gateway = SmsGateLocalClient(
            BASE_URL,
            "gateway-user",
            "gateway-password",
            http_client,
        )

        with pytest.raises(SmsGatewayError) as error:
            await gateway.send(uuid4(), "+84901234567", "secret body")

    assert error.value.category == category
    assert "gateway-user" not in str(error.value)
    assert "gateway-password" not in str(error.value)
    assert "secret body" not in str(error.value)

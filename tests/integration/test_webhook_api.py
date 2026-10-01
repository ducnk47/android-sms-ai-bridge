import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from sms_bridge.config import Settings
from sms_bridge.main import create_app

SIGNING_KEY = "test-signing-key"
USER_SECRET = "test-user-secret"


def _payload(
    *,
    event_id: str = "delivery-01",
    event: str = "sms:received",
    sender: str = "0901234567",
    message: str = "Cây bị vàng lá",
) -> bytes:
    return json.dumps(
        {
            "deviceId": "android-01",
            "event": event,
            "id": event_id,
            "payload": {
                "messageId": "gateway-message-01",
                "message": message,
                "sender": sender,
                "recipient": "+84909999999",
                "simNumber": 1,
                "receivedAt": datetime.now(UTC).isoformat(),
            },
            "webhookId": "webhook-01",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _signed_headers(raw_body: bytes, *, signed_at: datetime | None = None) -> dict[str, str]:
    timestamp = str(int((signed_at or datetime.now(UTC)).timestamp()))
    signature = hmac.new(
        SIGNING_KEY.encode(),
        raw_body + timestamp.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"X-Timestamp": timestamp, "X-Signature": signature}


@pytest.fixture
async def runtime(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_path=tmp_path / "webhook.db",
        webhook_signing_key=SecretStr(SIGNING_KEY),
        hmac_user_secret=SecretStr(USER_SECRET),
        gateway_mode="fake",
        ai_mode="echo",
    )
    app = create_app(settings, start_worker=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield app, client


async def test_signed_official_webhook_returns_202_and_enqueues(runtime) -> None:
    app, client = runtime
    raw_body = _payload()

    response = await client.post(
        "/webhooks/sms-gate",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 202
    work = await app.state.store.claim_next(datetime.now(UTC))
    assert work is not None
    assert work.sender == "+84901234567"
    assert work.text == "Cây bị vàng lá"


async def test_duplicate_webhook_is_acknowledged_but_enqueued_once(runtime) -> None:
    app, client = runtime
    raw_body = _payload(event_id="same-delivery")
    headers = _signed_headers(raw_body)

    first = await client.post("/webhooks/sms-gate", content=raw_body, headers=headers)
    duplicate = await client.post("/webhooks/sms-gate", content=raw_body, headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    assert await app.state.store.claim_next(datetime.now(UTC)) is not None
    assert await app.state.store.claim_next(datetime.now(UTC)) is None


@pytest.mark.parametrize("failure", ["bad_signature", "stale_timestamp"])
async def test_invalid_signature_or_stale_timestamp_never_persists(
    runtime,
    failure: str,
) -> None:
    app, client = runtime
    raw_body = _payload(event_id=failure)
    if failure == "bad_signature":
        headers = _signed_headers(raw_body)
        headers["X-Signature"] = "0" * 64
    else:
        headers = _signed_headers(raw_body, signed_at=datetime.now(UTC) - timedelta(minutes=6))

    response = await client.post(
        "/webhooks/sms-gate",
        content=raw_body,
        headers=headers,
    )

    assert response.status_code == 401
    assert await app.state.store.claim_next(datetime.now(UTC)) is None


@pytest.mark.parametrize(
    "raw_body",
    [
        b"{not-json",
        _payload(event_id="bad-sender", sender="not-a-phone"),
    ],
)
async def test_malformed_payload_or_sender_returns_422_without_persistence(
    runtime,
    raw_body: bytes,
) -> None:
    app, client = runtime

    response = await client.post(
        "/webhooks/sms-gate",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 422
    assert await app.state.store.claim_next(datetime.now(UTC)) is None


async def test_unsupported_event_returns_204_without_persistence(runtime) -> None:
    app, client = runtime
    raw_body = _payload(event_id="sent-event", event="sms:sent")

    response = await client.post(
        "/webhooks/sms-gate",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 204
    assert await app.state.store.claim_next(datetime.now(UTC)) is None

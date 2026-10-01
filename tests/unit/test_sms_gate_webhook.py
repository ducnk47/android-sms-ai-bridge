import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from sms_bridge.adapters.sms.webhook import (
    InvalidWebhook,
    WebhookSignatureError,
    normalize_webhook,
    parse_sms_received,
    verify_signature,
)


def _raw_webhook(*, event: str = "sms:received", message: str = "Xin chào") -> bytes:
    return json.dumps(
        {
            "deviceId": "device-01",
            "event": event,
            "id": "webhook-delivery-01",
            "payload": {
                "messageId": "content-message-01",
                "message": message,
                "sender": "0901234567",
                "recipient": "+84909999999",
                "simNumber": 1,
                "receivedAt": "2026-10-01T12:00:00+07:00",
            },
            "webhookId": "registration-01",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def test_verify_signature_accepts_exact_raw_body_and_timestamp() -> None:
    raw_body = _raw_webhook()
    timestamp = "1790827200"
    signature = hmac.new(
        b"signing-secret", raw_body + timestamp.encode(), hashlib.sha256
    ).hexdigest()

    verify_signature(
        raw_body,
        timestamp,
        signature,
        "signing-secret",
        now=datetime.fromtimestamp(int(timestamp), tz=UTC),
    )


@pytest.mark.parametrize("timestamp,signature", [(None, "abc"), ("1790827200", None)])
def test_verify_signature_rejects_missing_headers(
    timestamp: str | None, signature: str | None
) -> None:
    with pytest.raises(WebhookSignatureError):
        verify_signature(
            _raw_webhook(),
            timestamp,
            signature,
            "signing-secret",
            now=datetime(2026, 10, 1, tzinfo=UTC),
        )


def test_verify_signature_rejects_tampered_body() -> None:
    raw_body = _raw_webhook()
    timestamp = "1790827200"
    signature = hmac.new(
        b"signing-secret", raw_body + timestamp.encode(), hashlib.sha256
    ).hexdigest()

    with pytest.raises(WebhookSignatureError):
        verify_signature(
            raw_body + b" ",
            timestamp,
            signature,
            "signing-secret",
            now=datetime.fromtimestamp(int(timestamp), tz=UTC),
        )


def test_verify_signature_rejects_timestamp_older_than_five_minutes() -> None:
    raw_body = _raw_webhook()
    timestamp_time = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
    timestamp = str(int(timestamp_time.timestamp()))
    signature = hmac.new(
        b"signing-secret", raw_body + timestamp.encode(), hashlib.sha256
    ).hexdigest()

    with pytest.raises(WebhookSignatureError):
        verify_signature(
            raw_body,
            timestamp,
            signature,
            "signing-secret",
            now=timestamp_time + timedelta(seconds=301),
        )


def test_normalize_webhook_rejects_non_received_event() -> None:
    webhook = parse_sms_received(_raw_webhook(event="sms:sent"))

    with pytest.raises(InvalidWebhook):
        normalize_webhook(webhook)


def test_normalize_webhook_rejects_blank_message() -> None:
    webhook = parse_sms_received(_raw_webhook(message="   "))

    with pytest.raises(InvalidWebhook):
        normalize_webhook(webhook)


def test_parse_webhook_rejects_invalid_received_timestamp() -> None:
    payload = json.loads(_raw_webhook())
    payload["payload"]["receivedAt"] = "not-a-timestamp"

    with pytest.raises(InvalidWebhook):
        parse_sms_received(json.dumps(payload).encode())


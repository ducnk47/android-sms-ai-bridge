import json
from uuid import UUID

from sms_bridge.adapters.sms.webhook import normalize_webhook, parse_sms_received


def _official_payload(*, delivery_id: str = "Ey6ECgOkVVFjz3CL48B8C") -> bytes:
    return json.dumps(
        {
            "deviceId": "ffffffffceb0b1db0000018e937c815b",
            "event": "sms:received",
            "id": delivery_id,
            "payload": {
                "messageId": "abc123",
                "message": "Cây cacao bị đốm đen",
                "sender": "0901234567",
                "recipient": "+84909999999",
                "simNumber": 1,
                "receivedAt": "2026-10-01T12:46:11.000+07:00",
            },
            "webhookId": "LreFUt-Z3sSq0JufY9uWB",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def test_official_sms_received_payload_maps_to_internal_message() -> None:
    normalized = normalize_webhook(parse_sms_received(_official_payload()))

    assert isinstance(normalized.request_id, UUID)
    assert normalized.event_id == "Ey6ECgOkVVFjz3CL48B8C"
    assert normalized.source_message_id == "abc123"
    assert normalized.device_id == "ffffffffceb0b1db0000018e937c815b"
    assert normalized.sender == "+84901234567"
    assert normalized.text == "Cây cacao bị đốm đen"
    assert normalized.received_at.isoformat() == "2026-10-01T12:46:11+07:00"


def test_blank_delivery_id_uses_stable_content_fallback() -> None:
    first = normalize_webhook(parse_sms_received(_official_payload(delivery_id="")))
    second = normalize_webhook(parse_sms_received(_official_payload(delivery_id="")))

    assert first.event_id == second.event_id
    assert len(first.event_id) == 64
    assert first.request_id != second.request_id

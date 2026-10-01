import hashlib
import hmac
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sms_bridge.domain.identity import InvalidSender, normalize_sender
from sms_bridge.domain.models import InboundMessage


class InvalidWebhook(ValueError):
    """Raised when a gateway webhook cannot be accepted."""


class WebhookSignatureError(ValueError):
    """Raised when webhook authentication or freshness fails."""


class SmsReceivedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message_id: str = Field(alias="messageId")
    message: str
    sender: str
    recipient: str | None = None
    sim_number: int | None = Field(default=None, alias="simNumber")
    received_at: datetime = Field(alias="receivedAt")


class SmsGateWebhook(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    device_id: str = Field(alias="deviceId")
    event: str
    id: str
    webhook_id: str = Field(alias="webhookId")
    payload: SmsReceivedPayload


def verify_signature(
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    signing_key: str,
    *,
    now: datetime,
    max_age_seconds: int = 300,
) -> None:
    if not timestamp or not signature or not signing_key:
        raise WebhookSignatureError("Webhook signature is missing")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise WebhookSignatureError("Webhook timestamp is invalid") from exc

    if now.tzinfo is None or abs(now.timestamp() - signed_at) > max_age_seconds:
        raise WebhookSignatureError("Webhook timestamp is stale")

    expected = hmac.new(
        signing_key.encode(), raw_body + timestamp.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookSignatureError("Webhook signature is invalid")


def parse_sms_received(raw_body: bytes) -> SmsGateWebhook:
    try:
        return SmsGateWebhook.model_validate_json(raw_body)
    except (ValidationError, ValueError) as exc:
        raise InvalidWebhook("Invalid SMS gateway webhook") from exc


def normalize_webhook(webhook: SmsGateWebhook) -> InboundMessage:
    if webhook.event != "sms:received":
        raise InvalidWebhook("Unsupported SMS gateway event")
    if not webhook.device_id.strip() or not webhook.payload.message_id.strip():
        raise InvalidWebhook("Webhook identifiers are missing")

    text = webhook.payload.message.strip()
    if not text:
        raise InvalidWebhook("SMS message is blank")
    if webhook.payload.received_at.tzinfo is None:
        raise InvalidWebhook("SMS received timestamp lacks a timezone")

    try:
        sender = normalize_sender(webhook.payload.sender)
    except InvalidSender as exc:
        raise InvalidWebhook("SMS sender is invalid") from exc

    event_id = webhook.id.strip()
    if not event_id:
        fallback_source = "\0".join(
            (
                webhook.device_id,
                sender,
                webhook.payload.received_at.isoformat(),
                text,
            )
        )
        event_id = hashlib.sha256(fallback_source.encode()).hexdigest()

    return InboundMessage(
        request_id=uuid4(),
        event_id=event_id,
        source_message_id=webhook.payload.message_id,
        device_id=webhook.device_id,
        sender=sender,
        text=text,
        received_at=webhook.payload.received_at,
    )

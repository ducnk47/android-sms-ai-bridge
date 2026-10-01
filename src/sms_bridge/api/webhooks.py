from datetime import UTC, datetime
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sms_bridge.adapters.sms.webhook import (
    InvalidWebhook,
    WebhookSignatureError,
    normalize_webhook,
    parse_sms_received,
    verify_signature,
)
from sms_bridge.domain.identity import InvalidSender, derive_user_id, normalize_sender
from sms_bridge.domain.models import InboundMessage

router = APIRouter()
development_router = APIRouter()
logger = structlog.get_logger()

EMPTY_MESSAGE_ANSWER = "Vui lòng gửi câu hỏi bằng tin nhắn."
LONG_MESSAGE_ANSWER = "Tin nhắn quá dài, vui lòng chia câu hỏi thành nhiều tin ngắn hơn."


class SimulatedInbound(BaseModel):
    sender: str
    text: str
    event_id: str | None = None


@router.post("/webhooks/sms-gate")
async def receive_sms_gate_webhook(request: Request) -> Response:
    raw_body = await request.body()
    settings = request.app.state.settings
    signing_key = settings.webhook_signing_key
    if signing_key is None:
        raise HTTPException(status_code=503, detail="Webhook signing is not configured")

    try:
        verify_signature(
            raw_body,
            request.headers.get("X-Timestamp"),
            request.headers.get("X-Signature"),
            signing_key.get_secret_value(),
            now=datetime.now(UTC),
        )
    except WebhookSignatureError as error:
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from error

    try:
        webhook = parse_sms_received(raw_body)
    except InvalidWebhook as error:
        raise HTTPException(status_code=422, detail="Invalid SMS webhook") from error

    if webhook.event != "sms:received":
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if not webhook.payload.message.strip():
        try:
            sender = normalize_sender(webhook.payload.sender)
        except InvalidSender as error:
            raise HTTPException(status_code=422, detail="Invalid SMS webhook") from error
        inbound = InboundMessage(
            request_id=uuid4(),
            event_id=webhook.id,
            source_message_id=webhook.payload.message_id,
            device_id=webhook.device_id,
            sender=sender,
            text=webhook.payload.message,
            received_at=webhook.payload.received_at,
        )
    else:
        try:
            inbound = normalize_webhook(webhook)
        except InvalidWebhook as error:
            raise HTTPException(status_code=422, detail="Invalid SMS webhook") from error

    return await _enqueue(request, inbound)


@development_router.post("/dev/simulate-inbound")
async def simulate_inbound(payload: SimulatedInbound, request: Request) -> JSONResponse:
    try:
        sender = normalize_sender(payload.sender)
    except InvalidSender as error:
        raise HTTPException(status_code=422, detail="Invalid sender") from error
    now = datetime.now(UTC)
    event_id = payload.event_id or f"dev-{uuid4()}"
    inbound = InboundMessage(
        request_id=uuid4(),
        event_id=event_id,
        source_message_id=event_id,
        device_id="development-simulator",
        sender=sender,
        text=payload.text,
        received_at=now,
    )
    return await _enqueue(request, inbound)


async def _enqueue(request: Request, inbound: InboundMessage) -> JSONResponse:
    settings = request.app.state.settings
    user_secret = settings.hmac_user_secret
    if user_secret is None:
        raise HTTPException(status_code=503, detail="User identity is not configured")
    user_id = derive_user_id(inbound.sender, user_secret.get_secret_value())
    initial_answer = _input_control_answer(inbound.text, settings.input_max_chars)
    result = await request.app.state.store.enqueue(
        inbound,
        user_id,
        datetime.now(UTC),
        initial_answer=initial_answer,
        rate_limit_per_minute=settings.rate_limit_per_minute,
    )
    logger.info(
        "sms_enqueued",
        request_id=str(result.request_id),
        conversation_id=str(result.conversation_id),
        turn_id=result.turn_id,
        sender=inbound.sender,
        disposition=result.disposition.value,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "request_id": str(result.request_id),
            "conversation_id": str(result.conversation_id),
            "turn_id": result.turn_id,
            "duplicate": result.duplicate,
            "disposition": result.disposition.value,
        },
    )


def _input_control_answer(text: str, input_max_chars: int) -> str | None:
    if not text.strip():
        return EMPTY_MESSAGE_ANSWER
    if len(text) > input_max_chars:
        return LONG_MESSAGE_ANSWER
    return None

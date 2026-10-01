import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sms_bridge.adapters.ai.base import AiAdapter
from sms_bridge.adapters.sms.base import SmsGateway, SmsGatewayError
from sms_bridge.domain.models import AiRequest
from sms_bridge.storage.sqlite import PendingWork, SqliteStore

AsyncSleep = Callable[[float], Awaitable[None]]
BUSY_FALLBACK = "Hệ thống đang bận, vui lòng thử lại sau."
GATEWAY_RETRY_DELAYS = (2, 10)


class MessageProcessor:
    def __init__(
        self,
        store: SqliteStore,
        ai: AiAdapter,
        gateway: SmsGateway,
        *,
        ai_timeout_seconds: float = 20,
        reply_max_chars: int = 320,
        sleep: AsyncSleep = asyncio.sleep,
    ) -> None:
        self._store = store
        self._ai = ai
        self._gateway = gateway
        self._ai_timeout_seconds = ai_timeout_seconds
        self._reply_max_chars = reply_max_chars
        self._sleep = sleep

    async def process(self, work: PendingWork) -> None:
        answer = work.answer
        if answer is None:
            request = AiRequest(
                request_id=work.request_id,
                conversation_id=work.conversation_id,
                turn_id=work.turn_id,
                user_id=work.user_id,
                message=work.text,
                language="vi",
                history=work.history,
            )
            answer = await self._answer_with_retry(request)
            await self._store.record_answer(
                work.request_id,
                answer,
                datetime.now(UTC),
                start_sending=True,
            )

        await self._send_with_retry(work, answer)

    async def _answer_with_retry(self, request: AiRequest) -> str:
        for attempt in range(2):
            await self._store.record_ai_attempt(request.request_id, datetime.now(UTC))
            try:
                response = await asyncio.wait_for(
                    self._ai.answer(request),
                    timeout=self._ai_timeout_seconds,
                )
                if response.request_id != request.request_id:
                    raise ValueError("AI response request_id mismatch")
                return response.answer[: self._reply_max_chars]
            except Exception:
                if attempt == 1:
                    return BUSY_FALLBACK[: self._reply_max_chars]
        raise AssertionError("AI retry loop must return")

    async def _send_with_retry(self, work: PendingWork, answer: str) -> None:
        last_category = "unknown"
        for attempt in range(3):
            await self._store.record_send_attempt(work.request_id, datetime.now(UTC))
            try:
                result = await self._gateway.send(work.request_id, work.sender, answer)
                if not result.accepted:
                    raise SmsGatewayError("rejected")
            except Exception as error:
                last_category = _gateway_error_category(error)
                if attempt < len(GATEWAY_RETRY_DELAYS):
                    await self._sleep(GATEWAY_RETRY_DELAYS[attempt])
                    continue
                await self._store.record_send_failed(
                    work.request_id,
                    last_category,
                    datetime.now(UTC),
                )
                return

            await self._store.record_sent(
                work.request_id,
                result.message_id,
                datetime.now(UTC),
            )
            return


def _gateway_error_category(error: Exception) -> str:
    if isinstance(error, SmsGatewayError):
        return error.category
    if isinstance(error, TimeoutError):
        return "timeout"
    return "protocol"

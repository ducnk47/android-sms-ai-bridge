import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sms_bridge.adapters.sms.base import SmsGatewayError, SmsSendResult
from sms_bridge.domain.models import AiRequest, AiResponse, ConversationTurn
from sms_bridge.services.message_processor import MessageProcessor
from sms_bridge.storage.sqlite import PendingWork

NOW = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)


class RecordingStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.answers: dict[UUID, str] = {}
        self.sent: dict[UUID, str] = {}
        self.ai_attempts = 0
        self.send_attempts = 0
        self.ai_failure: str | None = None
        self.send_failure: str | None = None

    async def record_ai_attempt(self, request_id: UUID, now: datetime) -> None:
        self.ai_attempts += 1
        self.events.append("ai_attempt")

    async def record_answer(
        self,
        request_id: UUID,
        answer: str,
        now: datetime,
        *,
        start_sending: bool = False,
    ) -> None:
        assert start_sending is True
        self.answers[request_id] = answer
        self.events.append("answer_persisted")

    async def record_send_attempt(self, request_id: UUID, now: datetime) -> None:
        self.send_attempts += 1
        self.events.append("send_attempt")

    async def record_sent(
        self,
        request_id: UUID,
        gateway_message_id: str,
        now: datetime,
    ) -> None:
        self.sent[request_id] = gateway_message_id
        self.events.append("sent")

    async def record_ai_failed(
        self,
        request_id: UUID,
        error_category: str,
        now: datetime,
    ) -> None:
        self.ai_failure = error_category

    async def record_send_failed(
        self,
        request_id: UUID,
        error_category: str,
        now: datetime,
    ) -> None:
        self.send_failure = error_category


class CapturingAi:
    def __init__(
        self,
        responder: Callable[[AiRequest], Awaitable[AiResponse]],
    ) -> None:
        self._responder = responder
        self.requests: list[AiRequest] = []

    async def answer(self, request: AiRequest) -> AiResponse:
        self.requests.append(request)
        return await self._responder(request)


class RecordingGateway:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[UUID, str, str]] = []

    async def send(self, request_id: UUID, recipient: str, text: str) -> SmsSendResult:
        self.events.append("gateway_send")
        self.calls.append((request_id, recipient, text))
        return SmsSendResult(message_id=f"gateway-{request_id}", accepted=True)

    async def health(self) -> bool:
        return True


def _work(
    *,
    sender: str = "+84901234567",
    user_id: str = "opaque-user",
    message: str = "Cây bị vàng lá",
    history: tuple[ConversationTurn, ...] = (),
    answer: str | None = None,
) -> PendingWork:
    return PendingWork(
        request_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=6,
        sender=sender,
        user_id=user_id,
        text=message,
        history=history,
        answer=answer,
        ai_attempts=0,
        send_attempts=0,
    )


async def _answer(request: AiRequest) -> AiResponse:
    return AiResponse(request_id=request.request_id, answer=f"Giải pháp cho {request.user_id}")


async def test_happy_path_builds_private_ai_request_and_persists_before_send() -> None:
    events: list[str] = []
    store = RecordingStore(events)
    ai = CapturingAi(_answer)
    gateway = RecordingGateway(events)
    history = tuple(
        ConversationTurn(user=f"q{number}", assistant=f"a{number}")
        for number in range(1, 6)
    )
    work = _work(history=history)
    processor = MessageProcessor(store, ai, gateway)

    await processor.process(work)

    assert len(ai.requests) == 1
    request = ai.requests[0]
    assert request.request_id == work.request_id
    assert request.conversation_id == work.conversation_id
    assert request.turn_id == work.turn_id
    assert request.user_id == work.user_id
    assert request.message == work.text
    assert request.language == "vi"
    assert request.history == history
    assert "sender" not in request.model_dump()
    assert work.sender not in request.model_dump_json()
    assert events.index("answer_persisted") < events.index("gateway_send")
    assert gateway.calls == [
        (work.request_id, work.sender, f"Giải pháp cho {work.user_id}")
    ]
    assert store.sent[work.request_id] == f"gateway-{work.request_id}"


async def test_answers_never_cross_between_interleaved_senders() -> None:
    events: list[str] = []
    store = RecordingStore(events)
    ai = CapturingAi(_answer)
    gateway = RecordingGateway(events)
    processor = MessageProcessor(store, ai, gateway)
    alice = _work(sender="+84900000001", user_id="alice")
    bob = _work(sender="+84900000002", user_id="bob")

    await asyncio.gather(processor.process(alice), processor.process(bob))

    assert set(gateway.calls) == {
        (alice.request_id, alice.sender, "Giải pháp cho alice"),
        (bob.request_id, bob.sender, "Giải pháp cho bob"),
    }


async def test_processor_truncates_unicode_answer_before_persisting() -> None:
    async def long_answer(request: AiRequest) -> AiResponse:
        return AiResponse(request_id=request.request_id, answer="🌾" * 400)

    events: list[str] = []
    store = RecordingStore(events)
    processor = MessageProcessor(
        store,
        CapturingAi(long_answer),
        RecordingGateway(events),
        reply_max_chars=320,
    )
    work = _work()

    await processor.process(work)

    assert store.answers[work.request_id] == "🌾" * 320


async def test_ai_times_out_twice_then_persists_and_sends_busy_fallback() -> None:
    async def never_answers(request: AiRequest) -> AiResponse:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")

    events: list[str] = []
    store = RecordingStore(events)
    ai = CapturingAi(never_answers)
    gateway = RecordingGateway(events)
    processor = MessageProcessor(
        store,
        ai,
        gateway,
        ai_timeout_seconds=0.001,
    )
    work = _work()

    await processor.process(work)

    fallback = "Hệ thống đang bận, vui lòng thử lại sau."
    assert len(ai.requests) == 2
    assert store.ai_attempts == 2
    assert store.answers[work.request_id] == fallback
    assert gateway.calls == [(work.request_id, work.sender, fallback)]


class FailingGateway:
    def __init__(self, category: str = "temporary") -> None:
        self.category = category
        self.calls: list[tuple[UUID, str, str]] = []

    async def send(self, request_id: UUID, recipient: str, text: str) -> SmsSendResult:
        self.calls.append((request_id, recipient, text))
        raise SmsGatewayError(self.category)

    async def health(self) -> bool:
        return False


async def test_gateway_retries_three_times_with_fixed_delays_and_request_id() -> None:
    events: list[str] = []
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    store = RecordingStore(events)
    ai = CapturingAi(_answer)
    gateway = FailingGateway()
    processor = MessageProcessor(store, ai, gateway, sleep=record_sleep)
    work = _work(answer="persisted answer")

    await processor.process(work)

    assert ai.requests == []
    assert store.send_attempts == 3
    assert sleeps == [2, 10]
    assert gateway.calls == [
        (work.request_id, work.sender, "persisted answer"),
        (work.request_id, work.sender, "persisted answer"),
        (work.request_id, work.sender, "persisted answer"),
    ]
    assert store.send_failure == "temporary"
    assert work.request_id not in store.sent


async def test_restart_from_answered_never_calls_ai_again() -> None:
    async def must_not_run(request: AiRequest) -> AiResponse:
        raise AssertionError("AI must not be called for persisted answers")

    events: list[str] = []
    store = RecordingStore(events)
    ai = CapturingAi(must_not_run)
    gateway = RecordingGateway(events)
    work = _work(answer="answer survived restart")

    await MessageProcessor(store, ai, gateway).process(work)

    assert ai.requests == []
    assert gateway.calls == [
        (work.request_id, work.sender, "answer survived restart")
    ]
    assert "answer_persisted" not in events


async def test_mismatched_ai_response_id_is_retried_as_protocol_failure() -> None:
    calls = 0

    async def mismatched(request: AiRequest) -> AiResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return AiResponse(request_id=uuid4(), answer="wrong turn")
        return AiResponse(request_id=request.request_id, answer="correct turn")

    events: list[str] = []
    store = RecordingStore(events)
    gateway = RecordingGateway(events)
    work = _work()

    await MessageProcessor(store, CapturingAi(mismatched), gateway).process(work)

    assert calls == 2
    assert store.ai_attempts == 2
    assert gateway.calls == [(work.request_id, work.sender, "correct turn")]

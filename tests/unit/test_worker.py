import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sms_bridge.adapters.sms.fake import FakeSmsGateway
from sms_bridge.domain.models import AiRequest, AiResponse, InboundMessage
from sms_bridge.services.message_processor import MessageProcessor
from sms_bridge.services.worker import MessageWorker
from sms_bridge.storage.sqlite import PendingWork, SqliteStore

NOW = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)


class QueueStore:
    def __init__(self, work: list[PendingWork]) -> None:
        self.work = work
        self.events: list[str] = []
        self.claimed = asyncio.Event()

    async def recover_stale(self, now: datetime, stale_after_seconds: int = 60) -> int:
        self.events.append("recover")
        return 0

    async def claim_next(self, now: datetime) -> PendingWork | None:
        self.events.append("claim")
        self.claimed.set()
        return self.work.pop(0) if self.work else None


class RecordingProcessor:
    def __init__(self) -> None:
        self.processed: list[PendingWork] = []
        self.called = asyncio.Event()

    async def process(self, work: PendingWork) -> None:
        self.processed.append(work)
        self.called.set()


def _pending() -> PendingWork:
    return PendingWork(
        request_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=1,
        sender="+84901234567",
        user_id="opaque-user",
        text="question",
        history=(),
        answer=None,
        ai_attempts=0,
        send_attempts=0,
    )


async def test_run_once_reports_whether_it_processed_work() -> None:
    work = _pending()
    store = QueueStore([work])
    processor = RecordingProcessor()
    worker = MessageWorker(store, processor)

    assert await worker.run_once(NOW) is True
    assert await worker.run_once(NOW) is False
    assert processor.processed == [work]


async def test_run_recovers_before_first_claim_and_stop_is_clean() -> None:
    store = QueueStore([])
    worker = MessageWorker(
        store,
        RecordingProcessor(),
        poll_interval_seconds=0.001,
        concurrency=2,
    )

    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(store.claimed.wait(), timeout=1)
    await worker.stop()
    await asyncio.wait_for(task, timeout=1)

    assert store.events[0] == "recover"
    assert "claim" in store.events[1:]


async def test_run_immediately_recovers_work_claimed_before_restart(tmp_path) -> None:
    moment = datetime.now(UTC)
    store = SqliteStore(tmp_path / "restart.db")
    await store.initialize()
    enqueued = await store.enqueue(
        _inbound("restart", "+84900000001", 0),
        "alice",
        moment,
    )
    claimed_before_restart = await store.claim_next(moment)
    assert claimed_before_restart is not None
    assert claimed_before_restart.request_id == enqueued.request_id

    processor = RecordingProcessor()
    worker = MessageWorker(
        store,
        processor,
        poll_interval_seconds=0.001,
        concurrency=1,
    )
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(processor.called.wait(), timeout=0.2)
    finally:
        await worker.stop()
        await asyncio.wait_for(task, timeout=1)
        await store.close()

    assert [work.request_id for work in processor.processed] == [enqueued.request_id]


async def test_cancelling_run_cleans_up_worker_loops() -> None:
    store = QueueStore([])
    worker = MessageWorker(
        store,
        RecordingProcessor(),
        poll_interval_seconds=0.001,
        concurrency=2,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(store.claimed.wait(), timeout=1)

    task.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert task.done()


class BlockingAi:
    def __init__(self) -> None:
        self.requests: list[AiRequest] = []
        self.active_conversations: set[object] = set()
        self.two_conversations_started = asyncio.Event()
        self.three_requests_seen = asyncio.Event()
        self.release = asyncio.Event()

    async def answer(self, request: AiRequest) -> AiResponse:
        self.requests.append(request)
        self.active_conversations.add(request.conversation_id)
        if len(self.active_conversations) >= 2:
            self.two_conversations_started.set()
        if len(self.requests) >= 3:
            self.three_requests_seen.set()
        await self.release.wait()
        self.active_conversations.remove(request.conversation_id)
        return AiResponse(
            request_id=request.request_id,
            answer=f"answer for {request.user_id}",
        )


def _inbound(event_id: str, sender: str, second: int) -> InboundMessage:
    return InboundMessage(
        request_id=uuid4(),
        event_id=event_id,
        source_message_id=event_id,
        device_id="android-01",
        sender=sender,
        text=event_id,
        received_at=NOW + timedelta(seconds=second),
    )


async def test_worker_overlaps_different_conversations_but_serializes_same_one(
    tmp_path,
) -> None:
    store = SqliteStore(tmp_path / "worker.db")
    await store.initialize()
    await store.enqueue(_inbound("alice-1", "+84900000001", 1), "alice", NOW)
    await store.enqueue(
        _inbound("alice-2", "+84900000001", 2),
        "alice",
        NOW + timedelta(seconds=1),
    )
    await store.enqueue(
        _inbound("bob-1", "+84900000002", 3),
        "bob",
        NOW + timedelta(seconds=2),
    )
    ai = BlockingAi()
    processor = MessageProcessor(store, ai, FakeSmsGateway())
    worker = MessageWorker(
        store,
        processor,
        poll_interval_seconds=0.001,
        concurrency=4,
    )
    task = asyncio.create_task(worker.run())

    await asyncio.wait_for(ai.two_conversations_started.wait(), timeout=1)
    assert [request.user_id for request in ai.requests].count("alice") == 1
    assert {request.user_id for request in ai.requests} == {"alice", "bob"}

    ai.release.set()
    await asyncio.wait_for(ai.three_requests_seen.wait(), timeout=1)
    await worker.stop()
    await asyncio.wait_for(task, timeout=1)
    await store.close()

    assert [request.user_id for request in ai.requests].count("alice") == 2

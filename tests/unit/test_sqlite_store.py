from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from sms_bridge.domain.models import ConversationTurn, InboundMessage
from sms_bridge.domain.states import MessageState, assert_transition
from sms_bridge.storage.sqlite import EnqueueDisposition, SqliteStore

NOW = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)


def _inbound(
    *,
    event_id: str,
    request_id: UUID | None = None,
    source_message_id: str = "gateway-message-01",
    sender: str = "+84901234567",
    text: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        request_id=request_id or uuid4(),
        event_id=event_id,
        source_message_id=source_message_id,
        device_id="android-01",
        sender=sender,
        text=text or f"question for {event_id}",
        received_at=NOW,
    )


@pytest.fixture
async def store(tmp_path):
    database_path = tmp_path / "sms-bridge.db"
    instance = SqliteStore(database_path)
    await instance.initialize()
    yield instance
    await instance.close()


async def test_initialize_is_idempotent_and_healthy(tmp_path) -> None:
    instance = SqliteStore(tmp_path / "sms-bridge.db")

    await instance.initialize()
    await instance.initialize()

    assert await instance.healthcheck() is True
    await instance.close()


async def test_enqueue_deduplicates_the_gateway_event(store: SqliteStore) -> None:
    inbound = _inbound(event_id="delivery-01")

    first = await store.enqueue(inbound, "user-01", NOW)
    duplicate = await store.enqueue(
        _inbound(event_id="delivery-01", request_id=uuid4()),
        "user-01",
        NOW,
    )

    assert first.disposition is EnqueueDisposition.NEW
    assert duplicate.disposition is EnqueueDisposition.DUPLICATE
    assert duplicate.duplicate is True
    assert duplicate.request_id == first.request_id
    assert duplicate.conversation_id == first.conversation_id
    assert duplicate.turn_id == first.turn_id


async def test_distinct_gateway_events_are_distinct_even_with_same_message_id(
    store: SqliteStore,
) -> None:
    first = await store.enqueue(
        _inbound(event_id="delivery-01", source_message_id="same-message"),
        "user-01",
        NOW,
    )
    second = await store.enqueue(
        _inbound(event_id="delivery-02", source_message_id="same-message"),
        "user-01",
        NOW,
    )

    assert first.request_id != second.request_id
    assert second.disposition is EnqueueDisposition.NEW
    assert second.conversation_id == first.conversation_id
    assert second.turn_id == first.turn_id + 1


async def test_conversation_expires_after_thirty_minutes(store: SqliteStore) -> None:
    first = await store.enqueue(_inbound(event_id="delivery-01"), "user-01", NOW)
    second = await store.enqueue(
        _inbound(event_id="delivery-02"),
        "user-01",
        NOW + timedelta(minutes=29),
    )
    expired = await store.enqueue(
        _inbound(event_id="delivery-03"),
        "user-01",
        NOW + timedelta(minutes=59, seconds=1),
    )

    assert second.conversation_id == first.conversation_id
    assert second.turn_id == 2
    assert expired.conversation_id != first.conversation_id
    assert expired.turn_id == 1


async def test_claim_includes_only_latest_five_completed_turns(
    store: SqliteStore,
) -> None:
    for number in range(1, 7):
        moment = NOW + timedelta(seconds=number)
        result = await store.enqueue(
            _inbound(event_id=f"delivery-{number}", text=f"question {number}"),
            "user-01",
            moment,
        )
        work = await store.claim_next(moment)
        assert work is not None
        assert work.request_id == result.request_id
        await store.record_ai_attempt(result.request_id, moment)
        await store.record_answer(result.request_id, f"answer {number}", moment)
        send_work = await store.claim_next(moment)
        assert send_work is not None
        await store.record_send_attempt(result.request_id, moment)
        await store.record_sent(result.request_id, f"outbound-{number}", moment)

    moment = NOW + timedelta(seconds=7)
    current = await store.enqueue(
        _inbound(event_id="delivery-7", text="current question"),
        "user-01",
        moment,
    )

    work = await store.claim_next(moment)

    assert work is not None
    assert work.request_id == current.request_id
    assert work.history == tuple(
        ConversationTurn(user=f"question {number}", assistant=f"answer {number}")
        for number in range(2, 7)
    )


def test_message_state_machine_rejects_illegal_transitions() -> None:
    assert_transition(MessageState.RECEIVED, MessageState.QUEUED)
    assert_transition(MessageState.PROCESSING, MessageState.ANSWERED)
    assert_transition(MessageState.SENDING, MessageState.SENT)

    with pytest.raises(ValueError, match="illegal message state transition"):
        assert_transition(MessageState.QUEUED, MessageState.SENT)


async def test_initial_answer_skips_ai_and_is_claimed_for_sending(
    store: SqliteStore,
) -> None:
    result = await store.enqueue(
        _inbound(event_id="delivery-command"),
        "user-01",
        NOW,
        initial_answer="SMS bridge is healthy.",
    )

    work = await store.claim_next(NOW)

    assert result.disposition is EnqueueDisposition.NEW
    assert work is not None
    assert work.request_id == result.request_id
    assert work.answer == "SMS bridge is healthy."
    assert work.ai_attempts == 0


async def test_rate_limit_replies_once_then_suppresses_and_still_deduplicates(
    store: SqliteStore,
) -> None:
    for number in range(1, 7):
        moment = NOW + timedelta(seconds=number)
        result = await store.enqueue(
            _inbound(event_id=f"accepted-{number}"),
            "user-01",
            moment,
            initial_answer=f"accepted {number}",
        )
        work = await store.claim_next(moment)
        assert work is not None
        await store.record_send_attempt(result.request_id, moment)
        await store.record_sent(result.request_id, f"outbound-{number}", moment)

    limited_message = _inbound(event_id="limited-7")
    limited = await store.enqueue(
        limited_message,
        "user-01",
        NOW + timedelta(seconds=7),
        rate_limit_answer="slow down",
    )
    limited_work = await store.claim_next(NOW + timedelta(seconds=7))
    suppressed_message = _inbound(event_id="limited-8")
    suppressed = await store.enqueue(
        suppressed_message,
        "user-01",
        NOW + timedelta(seconds=8),
        rate_limit_answer="slow down",
    )
    duplicate = await store.enqueue(
        _inbound(event_id="limited-8"),
        "user-01",
        NOW + timedelta(seconds=9),
    )

    assert limited.disposition is EnqueueDisposition.RATE_LIMIT_REPLY
    assert limited_work is not None
    assert limited_work.request_id == limited.request_id
    assert limited_work.answer == "slow down"
    assert suppressed.disposition is EnqueueDisposition.RATE_LIMIT_SUPPRESSED
    assert await store.claim_next(NOW + timedelta(seconds=8)) is None
    assert duplicate.disposition is EnqueueDisposition.DUPLICATE
    assert duplicate.request_id == suppressed.request_id


async def test_claim_allows_only_one_turn_per_conversation(
    store: SqliteStore,
) -> None:
    first = await store.enqueue(
        _inbound(event_id="alice-1", sender="+84900000001"),
        "alice",
        NOW,
    )
    await store.enqueue(
        _inbound(event_id="alice-2", sender="+84900000001"),
        "alice",
        NOW + timedelta(seconds=1),
    )
    bob = await store.enqueue(
        _inbound(event_id="bob-1", sender="+84900000002"),
        "bob",
        NOW + timedelta(seconds=2),
    )

    first_claim = await store.claim_next(NOW)
    second_claim = await store.claim_next(NOW)
    third_claim = await store.claim_next(NOW)

    assert first_claim is not None
    assert first_claim.request_id == first.request_id
    assert second_claim is not None
    assert second_claim.request_id == bob.request_id
    assert third_claim is None


async def test_recover_stale_processing_and_sending_work(store: SqliteStore) -> None:
    processing = await store.enqueue(
        _inbound(event_id="processing", sender="+84900000001"),
        "alice",
        NOW,
    )
    sending = await store.enqueue(
        _inbound(event_id="sending", sender="+84900000002"),
        "bob",
        NOW + timedelta(seconds=1),
        initial_answer="persisted answer",
    )
    assert (await store.claim_next(NOW)).request_id == processing.request_id  # type: ignore[union-attr]
    assert (await store.claim_next(NOW)).request_id == sending.request_id  # type: ignore[union-attr]

    recovered = await store.recover_stale(NOW + timedelta(seconds=61))
    processing_again = await store.claim_next(NOW + timedelta(seconds=61))
    sending_again = await store.claim_next(NOW + timedelta(seconds=61))

    assert recovered == 2
    assert processing_again is not None
    assert processing_again.request_id == processing.request_id
    assert processing_again.answer is None
    assert sending_again is not None
    assert sending_again.request_id == sending.request_id
    assert sending_again.answer == "persisted answer"


async def test_interleaved_turns_keep_their_original_return_number(
    store: SqliteStore,
) -> None:
    first = await store.enqueue(
        _inbound(event_id="first-number", sender="+84900000001"),
        "same-user",
        NOW,
        initial_answer="first",
    )
    second = await store.enqueue(
        _inbound(event_id="second-number", sender="+84900000002"),
        "same-user",
        NOW + timedelta(seconds=1),
        initial_answer="second",
    )

    first_work = await store.claim_next(NOW)
    assert first_work is not None
    assert first_work.request_id == first.request_id
    assert first_work.sender == "+84900000001"
    await store.record_send_attempt(first.request_id, NOW)
    await store.record_sent(first.request_id, "outbound-first", NOW)

    second_work = await store.claim_next(NOW)
    assert second_work is not None
    assert second_work.request_id == second.request_id
    assert second_work.sender == "+84900000002"

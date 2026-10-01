from enum import StrEnum


class MessageState(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    ANSWERED = "answered"
    SENDING = "sending"
    SENT = "sent"
    AI_FAILED = "ai_failed"
    SEND_FAILED = "send_failed"
    RATE_LIMITED = "rate_limited"


_LEGAL_TRANSITIONS: dict[MessageState, frozenset[MessageState]] = {
    MessageState.RECEIVED: frozenset(
        {MessageState.QUEUED, MessageState.ANSWERED, MessageState.RATE_LIMITED}
    ),
    MessageState.QUEUED: frozenset({MessageState.PROCESSING}),
    MessageState.PROCESSING: frozenset(
        {MessageState.QUEUED, MessageState.ANSWERED, MessageState.AI_FAILED}
    ),
    MessageState.ANSWERED: frozenset({MessageState.SENDING}),
    MessageState.SENDING: frozenset(
        {MessageState.ANSWERED, MessageState.SENT, MessageState.SEND_FAILED}
    ),
    MessageState.SENT: frozenset(),
    MessageState.AI_FAILED: frozenset(),
    MessageState.SEND_FAILED: frozenset(),
    MessageState.RATE_LIMITED: frozenset(),
}


def assert_transition(current: MessageState, target: MessageState) -> None:
    if target not in _LEGAL_TRANSITIONS[current]:
        raise ValueError(f"illegal message state transition: {current} -> {target}")

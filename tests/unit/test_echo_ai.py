from uuid import uuid4

import pytest
from pydantic import ValidationError

from sms_bridge.adapters.ai.echo import EchoAiAdapter
from sms_bridge.domain.models import AiRequest, AiResponse, ConversationTurn


def _request(
    message: str = "ping",
    *,
    history: tuple[ConversationTurn, ...] = (),
) -> AiRequest:
    return AiRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=1,
        user_id="opaque-user-id",
        message=message,
        language="vi",
        history=history,
    )


def test_first_turn_accepts_empty_history() -> None:
    request = _request()

    assert request.history == ()


def test_request_accepts_at_most_five_history_turns() -> None:
    five_turns = tuple(
        ConversationTurn(user=f"q{number}", assistant=f"a{number}")
        for number in range(5)
    )

    assert _request(history=five_turns).history == five_turns

    with pytest.raises(ValidationError):
        _request(
            history=five_turns
            + (ConversationTurn(user="too many", assistant="too many"),)
        )


@pytest.mark.parametrize("message", ["", "   ", "\n\t"])
def test_request_rejects_blank_messages(message: str) -> None:
    with pytest.raises(ValidationError):
        _request(message)


@pytest.mark.parametrize("answer", ["", "   ", "\n\t"])
def test_response_rejects_blank_answers(answer: str) -> None:
    with pytest.raises(ValidationError):
        AiResponse(request_id=uuid4(), answer=answer)


async def test_echo_preserves_unicode_and_request_identity() -> None:
    request = _request("Lúa bị vàng lá 🌾")

    response = await EchoAiAdapter().answer(request)

    assert response.request_id == request.request_id
    assert response.answer == "AI: Lúa bị vàng lá 🌾"


async def test_echo_default_prefix_returns_ai_ping() -> None:
    response = await EchoAiAdapter().answer(_request("ping"))

    assert response.answer == "AI: ping"


async def test_echo_does_not_truncate_long_answers() -> None:
    message = "x" * 500

    response = await EchoAiAdapter(prefix="").answer(_request(message))

    assert response.answer == message

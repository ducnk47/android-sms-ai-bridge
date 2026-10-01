from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from sms_bridge.adapters.sms.fake import SentSms
from sms_bridge.api.webhooks import EMPTY_MESSAGE_ANSWER, LONG_MESSAGE_ANSWER
from sms_bridge.config import Settings
from sms_bridge.domain.models import ConversationTurn
from sms_bridge.main import create_app


@pytest.fixture
async def echo_runtime(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_path=tmp_path / "echo.db",
        webhook_signing_key=SecretStr("signing-key"),
        hmac_user_secret=SecretStr("user-secret"),
        gateway_username="gateway-user",
        gateway_password=SecretStr("gateway-password"),
        gateway_mode="fake",
        ai_mode="echo",
    )
    app = create_app(settings, start_worker=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield app, client


async def _post(client: httpx.AsyncClient, sender: str, text: str, event_id: str):
    response = await client.post(
        "/dev/simulate-inbound",
        json={"sender": sender, "text": text, "event_id": event_id},
    )
    assert response.status_code == 202
    return response


async def test_vietnamese_sms_completes_the_fake_round_trip(echo_runtime) -> None:
    app, client = echo_runtime
    response = await _post(client, "0901234567", "Lúa bị vàng lá", "turn-1")

    processed = await app.state.worker.run_once(datetime.now(UTC))

    assert processed is True
    assert response.json()["turn_id"] == 1
    assert app.state.gateway.sent == [
        SentSms(
            request_id=UUID(response.json()["request_id"]),
            recipient="+84901234567",
            text="AI: Lúa bị vàng lá",
        )
    ]


async def test_follow_up_turns_increment_and_supply_latest_five_history(
    echo_runtime,
) -> None:
    app, client = echo_runtime
    conversation_id = None
    for number in range(1, 6):
        response = await _post(
            client,
            "0901234567",
            f"question {number}",
            f"history-{number}",
        )
        payload = response.json()
        conversation_id = conversation_id or payload["conversation_id"]
        assert payload["conversation_id"] == conversation_id
        assert payload["turn_id"] == number
        assert await app.state.worker.run_once(datetime.now(UTC)) is True

    sixth = await _post(client, "0901234567", "question 6", "history-6")
    work = await app.state.store.claim_next(datetime.now(UTC))

    assert sixth.json()["turn_id"] == 6
    assert work is not None
    assert work.history == tuple(
        ConversationTurn(user=f"question {number}", assistant=f"AI: question {number}")
        for number in range(1, 6)
    )
    await app.state.processor.process(work)


async def test_two_sender_streams_keep_recipient_isolation(echo_runtime) -> None:
    app, client = echo_runtime
    alice = await _post(client, "0900000001", "alice question", "alice-1")
    bob = await _post(client, "0900000002", "bob question", "bob-1")

    assert await app.state.worker.run_once(datetime.now(UTC)) is True
    assert await app.state.worker.run_once(datetime.now(UTC)) is True

    assert set(app.state.gateway.sent) == {
        SentSms(
            request_id=UUID(alice.json()["request_id"]),
            recipient="+84900000001",
            text="AI: alice question",
        ),
        SentSms(
            request_id=UUID(bob.json()["request_id"]),
            recipient="+84900000002",
            text="AI: bob question",
        ),
    }


@pytest.mark.parametrize(
    ("text", "expected_answer"),
    [
        ("  \n\t", EMPTY_MESSAGE_ANSWER),
        ("x" * 1_001, LONG_MESSAGE_ANSWER),
    ],
)
async def test_input_controls_bypass_ai_with_exact_answer(
    echo_runtime,
    text: str,
    expected_answer: str,
) -> None:
    app, client = echo_runtime
    response = await _post(client, "0901234567", text, f"control-{len(text)}")

    assert await app.state.worker.run_once(datetime.now(UTC)) is True

    sent = app.state.gateway.sent[-1]
    assert sent.request_id == UUID(response.json()["request_id"])
    assert sent.text == expected_answer
    assert not sent.text.startswith("AI: ")


async def test_readiness_checks_initialized_database_and_gateway(echo_runtime) -> None:
    _, client = echo_runtime

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "pass",
        "components": {"database": "pass", "gateway": "pass"},
    }


async def test_seventh_message_gets_one_rate_limit_reply_then_suppresses(
    echo_runtime,
) -> None:
    app, client = echo_runtime
    for number in range(1, 7):
        await _post(client, "0901234567", f"message {number}", f"rate-{number}")
        assert await app.state.worker.run_once(datetime.now(UTC)) is True

    seventh = await _post(client, "0901234567", "message 7", "rate-7")
    assert seventh.json()["disposition"] == "rate_limit_reply"
    assert await app.state.worker.run_once(datetime.now(UTC)) is True
    eighth = await _post(client, "0901234567", "message 8", "rate-8")

    assert eighth.json()["disposition"] == "rate_limit_suppressed"
    assert await app.state.worker.run_once(datetime.now(UTC)) is False
    assert [sms.text for sms in app.state.gateway.sent].count(
        "Bạn gửi quá nhiều tin nhắn, vui lòng chờ một phút."
    ) == 1


async def test_enqueue_log_masks_sender_and_never_contains_secrets(
    echo_runtime,
    capsys,
) -> None:
    _, client = echo_runtime

    response = await _post(client, "0901234567", "private question", "logged-turn")
    output = capsys.readouterr().out

    assert response.json()["request_id"] in output
    assert "+8490***567" in output
    assert "+84901234567" not in output
    assert "private question" not in output
    assert "gateway-password" not in output
    assert "signing-key" not in output
    assert "user-secret" not in output

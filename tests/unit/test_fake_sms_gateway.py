from uuid import uuid4

import pytest

from sms_bridge.adapters.sms.base import SmsGatewayError
from sms_bridge.adapters.sms.fake import FakeSmsGateway, SentSms


async def test_fake_gateway_records_one_sms_and_returns_deterministic_id() -> None:
    request_id = uuid4()
    gateway = FakeSmsGateway()

    result = await gateway.send(request_id, "+84901234567", "Xin chào")

    assert result.accepted is True
    assert result.message_id == f"fake-{request_id}"
    assert gateway.sent == [
        SentSms(request_id=request_id, recipient="+84901234567", text="Xin chào")
    ]
    assert await gateway.health() is True


async def test_fake_gateway_failure_does_not_record_sms() -> None:
    gateway = FakeSmsGateway(failures_before_success=1)

    with pytest.raises(SmsGatewayError, match="temporary") as error:
        await gateway.send(uuid4(), "+84901234567", "retry me")

    assert error.value.category == "temporary"
    assert gateway.sent == []


async def test_fake_gateway_exposes_a_copy_of_sent_records() -> None:
    gateway = FakeSmsGateway()
    request_id = uuid4()
    await gateway.send(request_id, "+84901234567", "immutable history")

    external_copy = gateway.sent
    external_copy.clear()

    assert gateway.sent == [
        SentSms(
            request_id=request_id,
            recipient="+84901234567",
            text="immutable history",
        )
    ]

from dataclasses import dataclass
from uuid import UUID

from sms_bridge.adapters.sms.base import SmsGatewayError, SmsSendResult


@dataclass(frozen=True, slots=True)
class SentSms:
    request_id: UUID
    recipient: str
    text: str


class FakeSmsGateway:
    def __init__(
        self,
        *,
        failures_before_success: int = 0,
        failure_category: str = "temporary",
        healthy: bool = True,
    ) -> None:
        self._failures_remaining = failures_before_success
        self._failure_category = failure_category
        self._healthy = healthy
        self._sent: list[SentSms] = []

    @property
    def sent(self) -> list[SentSms]:
        return list(self._sent)

    async def send(self, request_id: UUID, recipient: str, text: str) -> SmsSendResult:
        if self._failures_remaining > 0:
            self._failures_remaining -= 1
            raise SmsGatewayError(self._failure_category)

        self._sent.append(SentSms(request_id=request_id, recipient=recipient, text=text))
        return SmsSendResult(message_id=f"fake-{request_id}", accepted=True)

    async def health(self) -> bool:
        return self._healthy

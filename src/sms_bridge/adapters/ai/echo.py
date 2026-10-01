from sms_bridge.domain.models import AiRequest, AiResponse


class EchoAiAdapter:
    def __init__(self, prefix: str = "AI: ") -> None:
        self._prefix = prefix

    async def answer(self, request: AiRequest) -> AiResponse:
        return AiResponse(
            request_id=request.request_id,
            answer=f"{self._prefix}{request.message}",
        )

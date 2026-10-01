import re
from collections.abc import MutableMapping
from typing import Any

import structlog

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "password",
    "secret",
    "signing_key",
    "token",
)
_BODY_KEYS = {"answer", "message", "text"}
_PHONE_PATTERN = re.compile(r"^\+\d{8,15}$")


def _mask_phone(value: str) -> str:
    if not _PHONE_PATTERN.fullmatch(value):
        return value
    return f"{value[:5]}***{value[-3:]}"


def _redact_event(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
    *,
    include_message_bodies: bool,
) -> MutableMapping[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in event_dict.items():
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            redacted[key] = "***REDACTED***"
        elif not include_message_bodies and lowered in _BODY_KEYS:
            continue
        elif lowered in {"sender", "recipient", "phone_number"} and isinstance(value, str):
            redacted[key] = _mask_phone(value)
        else:
            redacted[key] = value
    return redacted


def configure_logging(*, include_message_bodies: bool = False) -> None:
    def redact(
        logger: Any,
        method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]:
        return _redact_event(
            logger,
            method_name,
            event_dict,
            include_message_bodies=include_message_bodies,
        )

    structlog.configure(
        processors=[
            redact,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


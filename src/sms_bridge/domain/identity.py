import hashlib
import hmac

import phonenumbers


class InvalidSender(ValueError):
    """Raised when an SMS sender cannot be normalized safely."""


def normalize_sender(value: str, *, default_region: str = "VN") -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSender("Invalid sender phone number")

    candidate = value.strip().replace(" ", "")
    if candidate.startswith("84") and not candidate.startswith("+"):
        candidate = f"+{candidate}"

    try:
        parsed = phonenumbers.parse(candidate, default_region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidSender("Invalid sender phone number") from exc

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidSender("Invalid sender phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def mask_sender(value: str) -> str:
    if len(value) < 8:
        return "***"
    return f"{value[:5]}***{value[-3:]}"


def derive_user_id(sender: str, secret: str) -> str:
    return hmac.new(secret.encode(), sender.encode(), hashlib.sha256).hexdigest()

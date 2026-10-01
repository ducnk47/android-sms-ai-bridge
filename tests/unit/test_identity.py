import pytest

from sms_bridge.domain.identity import (
    InvalidSender,
    derive_user_id,
    mask_sender,
    normalize_sender,
)


@pytest.mark.parametrize("raw", ["0901234567", "84901234567", "+84901234567"])
def test_normalize_sender_accepts_common_vietnamese_formats(raw: str) -> None:
    assert normalize_sender(raw) == "+84901234567"


@pytest.mark.parametrize("raw", ["", "not-a-phone", None])
def test_normalize_sender_rejects_invalid_values_without_echoing_them(raw: object) -> None:
    with pytest.raises(InvalidSender) as exc_info:
        normalize_sender(raw)  # type: ignore[arg-type]

    if raw:
        assert str(raw) not in str(exc_info.value)


def test_mask_sender_keeps_only_routing_hints() -> None:
    assert mask_sender("+84901234567") == "+8490***567"


def test_derive_user_id_is_stable_and_secret_dependent() -> None:
    first = derive_user_id("+84901234567", "first-secret")
    repeated = derive_user_id("+84901234567", "first-secret")
    different_secret = derive_user_id("+84901234567", "second-secret")

    assert first == repeated
    assert len(first) == 64
    assert first != different_secret

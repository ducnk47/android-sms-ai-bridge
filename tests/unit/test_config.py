from pydantic import SecretStr

from sms_bridge.config import Settings
from sms_bridge.logging import configure_logging


def test_settings_use_spec_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.gateway_base_url == "http://127.0.0.1:8081"
    assert settings.ai_timeout_seconds == 20
    assert settings.history_turn_limit == 5
    assert settings.conversation_ttl_minutes == 30
    assert settings.reply_max_chars == 320
    assert settings.input_max_chars == 1_000
    assert settings.rate_limit_per_minute == 6
    assert settings.log_message_bodies is False


def test_secrets_are_not_in_repr() -> None:
    settings = Settings(
        _env_file=None,
        gateway_password=SecretStr("gateway-secret"),
        webhook_signing_key=SecretStr("webhook-secret"),
        hmac_user_secret=SecretStr("hmac-secret"),
    )

    rendered = repr(settings)
    assert "gateway-secret" not in rendered
    assert "webhook-secret" not in rendered
    assert "hmac-secret" not in rendered
    assert "**********" in rendered


def test_logging_redacts_secret_fields_and_message_body(capsys) -> None:
    import structlog

    configure_logging(include_message_bodies=False)
    structlog.get_logger().info(
        "transport_probe",
        sender="+84901234567",
        password="gateway-secret",
        signing_key="webhook-secret",
        message="private cacao question",
    )

    rendered = capsys.readouterr().out
    assert "transport_probe" in rendered
    assert "+8490***567" in rendered
    assert "+84901234567" not in rendered
    assert "gateway-secret" not in rendered
    assert "webhook-secret" not in rendered
    assert "private cacao question" not in rendered


from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMS_BRIDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_path: Path = Path("var/sms_bridge.db")

    gateway_mode: Literal["fake", "local"] = "fake"
    gateway_base_url: str = "http://127.0.0.1:8081"
    gateway_username: str | None = None
    gateway_password: SecretStr | None = None
    webhook_signing_key: SecretStr | None = None
    hmac_user_secret: SecretStr | None = None
    gateway_priority: int = 100

    ai_mode: Literal["echo", "http"] = "echo"
    ai_base_url: str | None = None
    ai_timeout_seconds: float = 20

    history_turn_limit: int = 5
    conversation_ttl_minutes: int = 30
    reply_max_chars: int = 320
    input_max_chars: int = 1_000
    rate_limit_per_minute: int = 6
    log_message_bodies: bool = False


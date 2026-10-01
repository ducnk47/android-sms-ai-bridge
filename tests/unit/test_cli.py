import subprocess
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from pydantic import SecretStr

from sms_bridge.cli import build_tunnel_commands, main
from sms_bridge.config import Settings


class FakeGatewayClient:
    def __init__(self) -> None:
        self.health_calls = 0
        self.webhook_calls: list[tuple[str, str]] = []
        self.send_calls: list[tuple[UUID, str, str]] = []

    async def health(self) -> bool:
        self.health_calls += 1
        return True

    async def register_webhook(self, url: str, event: str = "sms:received") -> str:
        self.webhook_calls.append((url, event))
        return "webhook-01"

    async def send(self, request_id: UUID, recipient: str, text: str):
        from sms_bridge.adapters.sms.base import SmsSendResult

        self.send_calls.append((request_id, recipient, text))
        return SmsSendResult(message_id="message-01", accepted=True)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        gateway_mode="local",
        gateway_username="user",
        gateway_password=SecretStr("password"),
    )


def _factory_for(fake: FakeGatewayClient):
    @asynccontextmanager
    async def factory(settings: Settings):
        yield fake

    return factory


def test_build_tunnel_commands_match_usb_loopback_contract() -> None:
    assert build_tunnel_commands(8080) == (
        ("adb", "forward", "tcp:8081", "tcp:8080"),
        ("adb", "reverse", "tcp:9876", "tcp:8000"),
    )


@pytest.mark.parametrize("port", [0, -1, 65_536])
def test_build_tunnel_commands_reject_invalid_ports(port: int) -> None:
    with pytest.raises(ValueError, match="port"):
        build_tunnel_commands(port)


def test_invalid_cli_port_fails_before_subprocess() -> None:
    calls: list[tuple[str, ...]] = []

    def run_command(command, **kwargs):
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(SystemExit) as exit_info:
        main(
            ["adb-tunnels", "--device-port", "70000"],
            run_command=run_command,
        )

    assert exit_info.value.code == 2
    assert calls == []


def test_adb_failure_preserves_exit_code_without_printing_output_secrets(capsys) -> None:
    calls: list[tuple[str, ...]] = []

    def run_command(command, **kwargs):
        calls.append(tuple(command))
        return subprocess.CompletedProcess(
            command,
            17,
            stdout="gateway-password",
            stderr="signing-key",
        )

    exit_code = main(
        ["adb-tunnels", "--device-port", "8080"],
        run_command=run_command,
    )
    captured = capsys.readouterr()

    assert exit_code == 17
    assert calls == [("adb", "forward", "tcp:8081", "tcp:8080")]
    assert "gateway-password" not in captured.out + captured.err
    assert "signing-key" not in captured.out + captured.err


def test_successful_adb_setup_runs_forward_then_reverse() -> None:
    calls: list[tuple[str, ...]] = []

    def run_command(command, **kwargs):
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    exit_code = main(
        ["adb-tunnels", "--device-port", "8080"],
        run_command=run_command,
    )

    assert exit_code == 0
    assert calls == [
        ("adb", "forward", "tcp:8081", "tcp:8080"),
        ("adb", "reverse", "tcp:9876", "tcp:8000"),
    ]


def test_gateway_health_uses_local_client(capsys) -> None:
    gateway = FakeGatewayClient()

    exit_code = main(
        ["gateway-health"],
        settings=_settings(),
        gateway_factory=_factory_for(gateway),
    )

    assert exit_code == 0
    assert gateway.health_calls == 1
    assert "pass" in capsys.readouterr().out


def test_register_webhook_uses_android_loopback_contract() -> None:
    gateway = FakeGatewayClient()

    exit_code = main(
        ["register-webhook"],
        settings=_settings(),
        gateway_factory=_factory_for(gateway),
    )

    assert exit_code == 0
    assert gateway.webhook_calls == [
        ("http://127.0.0.1:9876/webhooks/sms-gate", "sms:received")
    ]


def test_send_test_normalizes_phone_and_passes_text() -> None:
    gateway = FakeGatewayClient()

    exit_code = main(
        ["send-test", "0901234567", "Xin chào"],
        settings=_settings(),
        gateway_factory=_factory_for(gateway),
    )

    assert exit_code == 0
    assert len(gateway.send_calls) == 1
    _, recipient, text = gateway.send_calls[0]
    assert recipient == "+84901234567"
    assert text == "Xin chào"

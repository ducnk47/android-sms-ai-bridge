import argparse
import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx

from sms_bridge.adapters.sms.base import SmsGatewayError
from sms_bridge.adapters.sms.sms_gate import SmsGateLocalClient
from sms_bridge.config import Settings
from sms_bridge.domain.identity import InvalidSender, normalize_sender

RunCommand = Callable[..., subprocess.CompletedProcess[str]]
GatewayFactory = Callable[[Settings], AbstractAsyncContextManager[SmsGateLocalClient]]


def build_tunnel_commands(
    device_api_port: int,
    *,
    mac_gateway_port: int = 8081,
    android_webhook_port: int = 9876,
    mac_api_port: int = 8000,
) -> tuple[tuple[str, ...], ...]:
    for port in (
        device_api_port,
        mac_gateway_port,
        android_webhook_port,
        mac_api_port,
    ):
        if not 1 <= port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
    return (
        (
            "adb",
            "forward",
            f"tcp:{mac_gateway_port}",
            f"tcp:{device_api_port}",
        ),
        (
            "adb",
            "reverse",
            f"tcp:{android_webhook_port}",
            f"tcp:{mac_api_port}",
        ),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    run_command: RunCommand | None = None,
    settings: Settings | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "adb-tunnels":
        runner = run_command or subprocess.run
        commands = build_tunnel_commands(args.device_port)
        for command in commands:
            completed = runner(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                print("ADB tunnel setup failed", file=sys.stderr)
                return completed.returncode
        print("ADB forward and reverse tunnels are ready")
        return 0

    resolved_settings = settings or Settings()
    factory = gateway_factory or _local_gateway
    return asyncio.run(_run_gateway_command(args, resolved_settings, factory))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sms-bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tunnels = subparsers.add_parser("adb-tunnels", help="Configure USB ADB tunnels")
    tunnels.add_argument("--device-port", required=True, type=_port)

    subparsers.add_parser("gateway-health", help="Check the Android Local Server")
    subparsers.add_parser("register-webhook", help="Register the inbound SMS webhook")
    send = subparsers.add_parser("send-test", help="Send one carrier SMS through Android")
    send.add_argument("phone")
    send.add_argument("text")
    return parser


def _port(value: str) -> int:
    try:
        port = int(value)
        build_tunnel_commands(port)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535") from error
    return port


@asynccontextmanager
async def _local_gateway(settings: Settings) -> AsyncIterator[SmsGateLocalClient]:
    if settings.gateway_username is None or settings.gateway_password is None:
        raise RuntimeError("Local gateway credentials are required")
    async with httpx.AsyncClient() as client:
        yield SmsGateLocalClient(
            settings.gateway_base_url,
            settings.gateway_username,
            settings.gateway_password.get_secret_value(),
            client,
            priority=settings.gateway_priority,
        )


async def _run_gateway_command(
    args: Any,
    settings: Settings,
    gateway_factory: GatewayFactory,
) -> int:
    try:
        async with gateway_factory(settings) as gateway:
            if args.command == "gateway-health":
                healthy = await gateway.health()
                print("gateway: pass" if healthy else "gateway: fail")
                return 0 if healthy else 1
            if args.command == "register-webhook":
                webhook_id = await gateway.register_webhook(
                    "http://127.0.0.1:9876/webhooks/sms-gate",
                    event="sms:received",
                )
                print(f"webhook registered: {webhook_id}")
                return 0
            if args.command == "send-test":
                recipient = normalize_sender(args.phone)
                result = await gateway.send(uuid4(), recipient, args.text)
                print(f"message accepted: {result.message_id}")
                return 0 if result.accepted else 1
    except (InvalidSender, SmsGatewayError, RuntimeError):
        print("Gateway command failed", file=sys.stderr)
        return 1
    raise AssertionError(f"unsupported command: {args.command}")

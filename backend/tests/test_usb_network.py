import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.usb_network import UsbNetwork, UsbNetworkRequest


def make_network():
    commands = []
    service = SimpleNamespace(
        connection={"ready": True},
        device={"usb_network_supported": True, "call": {}},
        calls=SimpleNamespace(active=lambda: False),
        store=SimpleNamespace(list=lambda: []),
        network_waiter=None,
        location=SimpleNamespace(waiter=None),
        bridge=SimpleNamespace(send=commands.append),
        broadcast=lambda: None,
    )
    return UsbNetwork(service), commands


def test_usb_network_strict_payload():
    for payload in [{"enabled": "false"}, {"enabled": True, "mode": "wifi"}]:
        with pytest.raises(ValidationError):
            UsbNetworkRequest(**payload)


def test_usb_network_request_confirmation_and_reconnection_state():
    async def scenario():
        network, commands = make_network()
        task = asyncio.create_task(network.configure(UsbNetworkRequest(enabled=True)))
        await asyncio.sleep(0)
        network.consume({"type": "usb_network_result", "request_id": "late", "success": True})
        assert not task.done()
        with pytest.raises(HTTPException):
            await network.configure(UsbNetworkRequest(enabled=False))
        network.consume(
            {
                "type": "usb_network_result",
                "request_id": commands[0]["request_id"],
                "success": True,
                "enabled": True,
                "mode": "ecm",
            }
        )
        assert (await task)["enabled"]
        network.consume({"type": "transport", "connected": False})
        assert len(commands) == 1
        network.consume(
            {"type": "status_response", "usb_network": {"enabled": False, "mode": "ecm"}}
        )
        assert not network.state["enabled"] and network.waiter is None

    asyncio.run(scenario())


def test_usb_network_disconnect_is_unknown_without_replay():
    async def scenario():
        network, commands = make_network()
        task = asyncio.create_task(network.configure(UsbNetworkRequest(enabled=True)))
        await asyncio.sleep(0)
        network.consume({"type": "transport", "connected": False})
        assert (await task)["state"] == "unknown"
        assert len(commands) == 1 and network.waiter is None

    asyncio.run(scenario())


def test_usb_network_guards_calls_sms_and_other_requests():
    async def scenario():
        network, commands = make_network()
        for blocked in ["call", "sms", "location", "network"]:
            service = network.service
            service.device["call"]["busy"] = blocked == "call"
            service.store.list = lambda blocked=blocked: (
                [{"state": "sending"}] if blocked == "sms" else []
            )
            service.location.waiter = object() if blocked == "location" else None
            service.network_waiter = object() if blocked == "network" else None
            with pytest.raises(HTTPException) as error:
                await network.configure(UsbNetworkRequest(enabled=True))
            assert error.value.status_code == 409
        assert not commands

    asyncio.run(scenario())

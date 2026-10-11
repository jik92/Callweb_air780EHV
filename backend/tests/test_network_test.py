import asyncio
from contextlib import suppress
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import Service
from app.network_test import NetworkTestRequest, probe
from app.store import Store


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a",
        "https://user:pass@example.com",
        "http://",
        "https://example.com/#x",
        "http://example.com:bad",
        "https://example.com/:CMD_END",
    ],
)
def test_reject_unsafe_network_urls(url):
    with pytest.raises(ValidationError):
        NetworkTestRequest(url=url)


def test_network_probe_uses_device_and_survives_late_results(tmp_path):
    async def scenario():
        service = Service(Store(tmp_path / "test.sqlite3"))
        service.connection["ready"] = True
        service.device = {"network_test_supported": True, "call": {}}
        commands = []

        class Bridge:
            def send(self, command):
                commands.append(command)

        service.bridge = Bridge()
        consumer = asyncio.create_task(service.consume())
        request = NetworkTestRequest(url="https://www.baidu.com")
        try:
            task = asyncio.create_task(probe(service, request))
            await asyncio.sleep(0)
            with pytest.raises(HTTPException) as busy:
                await probe(service, request)
            assert busy.value.status_code == 409
            await service.events.put(
                {"type": "network_test_result", "request_id": str(uuid4()), "reachable": True}
            )
            await asyncio.sleep(0)
            assert not task.done()
            await service.events.put(
                {
                    "type": "network_test_result",
                    "request_id": commands[0]["request_id"],
                    "reachable": True,
                    "status_code": 405,
                    "method": "GET",
                    "elapsed_ms": 100,
                }
            )
            result = await asyncio.wait_for(task, 1)
            assert result["source"] == "device_4g" and result["status_code"] == 405
            assert result["method"] == "GET"
            assert commands[0]["action"] == "network_test"
            assert service.network_waiter is None
            task = asyncio.create_task(probe(service, request))
            await asyncio.sleep(0)
            await service.events.put({"type": "transport", "connected": False})
            with pytest.raises(HTTPException) as disconnected:
                await asyncio.wait_for(task, 1)
            assert disconnected.value.status_code == 503
            assert len(commands) == 2 and service.network_waiter is None
        finally:
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
            service.store.close()

    asyncio.run(scenario())


def test_network_probe_requires_firmware_and_idle_call(tmp_path):
    async def scenario():
        service = Service(Store(tmp_path / "test.sqlite3"))
        request = NetworkTestRequest(url="https://www.baidu.com")
        try:
            for connected, supported, state, expected in [
                (False, False, "idle", 503),
                (True, False, "idle", 409),
                (True, True, "streaming", 409),
            ]:
                service.connection["ready"] = connected
                service.device = {"network_test_supported": supported}
                service.calls.state["state"] = state
                with pytest.raises(HTTPException) as error:
                    await probe(service, request)
                assert error.value.status_code == expected
        finally:
            service.store.close()

    asyncio.run(scenario())

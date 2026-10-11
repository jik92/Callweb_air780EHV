import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.location import Location


def make_service():
    commands = []
    service = SimpleNamespace(
        connection={"ready": True},
        device={"location_supported": True, "call": {}},
        calls=SimpleNamespace(active=lambda: False),
        network_waiter=None,
        bridge=SimpleNamespace(send=commands.append),
        broadcast=lambda: None,
    )
    return Location(service), commands


def test_location_matches_request_and_keeps_coordinates_on_geocoder_failure():
    async def scenario():
        location, commands = make_service()

        async def unavailable(*args):
            raise httpx.ConnectError("offline")

        location.address = unavailable
        task = asyncio.create_task(location.refresh())
        await asyncio.sleep(0)
        location.consume({"type": "location_result", "request_id": "late", "success": True})
        assert not task.done()
        location.consume(
            {
                "type": "location_result",
                "request_id": commands[0]["request_id"],
                "success": True,
                "latitude": 30.25,
                "longitude": 120.15,
            }
        )
        result = await task
        assert result["state"] == "partial" and result["latitude"] == 30.25
        assert location.waiter is None and len(commands) == 1

    asyncio.run(scenario())


def test_location_disconnect_invalidates_pending_address():
    async def scenario():
        location, commands = make_service()
        resolving = asyncio.Event()
        finish = asyncio.Event()

        async def address(*args):
            resolving.set()
            await finish.wait()
            return "模拟地区"

        location.address = address
        task = asyncio.create_task(location.refresh())
        await asyncio.sleep(0)
        location.consume(
            {
                "type": "location_result",
                "request_id": commands[0]["request_id"],
                "success": True,
                "latitude": 30.25,
                "longitude": 120.15,
            }
        )
        await resolving.wait()
        location.consume({"type": "transport", "connected": False})
        finish.set()
        result = await task
        assert result["state"] == "failed" and "address" not in result
        assert "latitude" not in result and location.waiter is None

    asyncio.run(scenario())


def test_location_guards_and_invalid_coordinates():
    async def scenario():
        location, commands = make_service()
        location.service.device["call"]["busy"] = True
        with pytest.raises(HTTPException) as busy:
            await location.refresh()
        assert busy.value.status_code == 409 and not commands
        location.service.device["call"]["busy"] = False
        task = asyncio.create_task(location.refresh())
        await asyncio.sleep(0)
        location.consume(
            {
                "type": "location_result",
                "request_id": commands[0]["request_id"],
                "success": True,
                "latitude": float("nan"),
                "longitude": 120,
            }
        )
        assert (await task)["state"] == "failed"

    asyncio.run(scenario())


def test_reverse_geocoder_caches_and_formats_administrative_address(monkeypatch):
    async def scenario():
        location, _ = make_service()
        calls = []

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                calls.append((url, kwargs))
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={
                        "display_name": "模拟地址",
                        "address": {
                            "state": "示例省",
                            "state_district": "示例地级市",
                            "city": "示例市",
                            "county": "示例区",
                        },
                    },
                )

        monkeypatch.setattr(httpx, "AsyncClient", Client)
        first = await location.address(30.25, 120.15)
        assert first == "示例省 · 示例地级市 · 示例市 · 示例区"
        assert await location.address(30.25, 120.15) == first
        assert len(calls) == 1 and calls[0][1]["params"]["accept-language"] == "zh-CN,zh,en"

    asyncio.run(scenario())

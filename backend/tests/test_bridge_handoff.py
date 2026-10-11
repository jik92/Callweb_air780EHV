import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.bridge_handoff import release_development_bridge


@pytest.mark.parametrize(
    ("title", "port", "busy", "expected"),
    [
        ("Other service", "mock17", False, []),
        ("Air780EHV 本地控制台", "other17", False, []),
        ("Air780EHV 本地控制台", "mock17", False, ["pause"]),
        ("Air780EHV 本地控制台", "mock17", True, ["pause"]),
    ],
)
def test_handoff_only_matching_console_and_preserves_busy(monkeypatch, title, port, busy, expected):
    actions = []

    def respond(request):
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"info": {"title": title}})
        if request.url.path == "/api/state":
            return httpx.Response(200, json={"bridge": {"port": port, "connected": True}})
        assert request.method == "POST"
        assert request.headers["origin"] == "http://127.0.0.1:9011"
        actions.append("pause")
        return (
            httpx.Response(409, json={"detail": "正在通话"})
            if busy
            else httpx.Response(200, json={"paused": True})
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
    )
    if busy:
        with pytest.raises(HTTPException) as error:
            asyncio.run(release_development_bridge("mock17"))
        assert error.value.status_code == 409
    else:
        asyncio.run(release_development_bridge("mock17"))
    assert actions == expected

import asyncio
import base64
import hashlib
import hmac
import json
from contextlib import suppress

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support import install_bridge

from app import dingtalk, main
from app.dingtalk import DingTalkSettings, DingTalkUpdate
from app.main import Service
from app.store import Store

WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abcdefgh1234567890"
SECRET = "SECabcdefgh1234567890"


def configured(tmp_path):
    settings = DingTalkSettings(tmp_path / "dingtalk")
    settings.save(DingTalkUpdate(enabled=True, webhook=WEBHOOK, secret=SECRET))
    return settings


def test_private_settings_validation_and_signature(tmp_path):
    settings = configured(tmp_path)
    assert settings.snapshot()["ready"]
    public = json.dumps(settings.snapshot())
    assert SECRET not in public and "abcdefgh1234567890" not in public
    assert settings.path.stat().st_mode & 0o777 == 0o600
    assert settings.directory.stat().st_mode & 0o777 == 0o700
    settings.save(DingTalkUpdate(enabled=True, webhook="", secret=""))
    assert settings.read()["secret"] == SECRET
    params = dingtalk.signed_params(settings.config(), 123456789)
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), ("123456789\n" + SECRET).encode(), hashlib.sha256).digest()
    ).decode()
    assert params["sign"] == expected and params["timestamp"] == "123456789"
    for url in [
        "http://oapi.dingtalk.com/robot/send?access_token=abcdefgh1234567890",
        "https://example.org/robot/send?access_token=abcdefgh1234567890",
        WEBHOOK + "&sign=x",
        WEBHOOK + "#fragment",
    ]:
        with pytest.raises(ValidationError):
            DingTalkUpdate(webhook=url)
    with pytest.raises(ValueError, match="配置"):
        settings.save(DingTalkUpdate(enabled=True, clear_credentials=True))
    settings.save(DingTalkUpdate(clear_credentials=True))
    assert not settings.snapshot()["has_webhook"]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"errcode": 0, "errmsg": "ok"}, "sent"),
        ({"errcode": 310000, "errmsg": SECRET}, "failed"),
        ({"errcode": "0"}, "unknown"),
        ([], "unknown"),
        (None, "unknown"),
    ],
)
def test_signed_http_request_and_sanitized_results(tmp_path, monkeypatch, result, expected):
    settings = configured(tmp_path)
    client_type = httpx.AsyncClient
    attempts = []

    def handler(request):
        attempts.append(request)
        assert request.url.host == "oapi.dingtalk.com"
        assert request.url.params["access_token"] == "abcdefgh1234567890"
        assert request.url.params["sign"]
        assert json.loads(request.content)["text"]["content"] == "模拟短信"
        return httpx.Response(200, json=result)

    monkeypatch.setattr(
        dingtalk.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw),
    )
    state, error = asyncio.run(dingtalk.send_notification(settings.config(), "模拟短信"))
    assert state == expected and len(attempts) == 1
    assert SECRET not in (error or "")


def test_network_timeout_unknown_without_retry(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    client_type = httpx.AsyncClient
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    monkeypatch.setattr(
        dingtalk.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw),
    )
    state, error = asyncio.run(dingtalk.send_notification(settings.config(), "模拟短信"))
    assert state == "unknown" and len(attempts) == 1 and SECRET not in error


def test_live_sms_dedupe_history_and_restart(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path / "console.sqlite3")
        service = Service(store)
        service.dingtalk_settings.save(DingTalkUpdate(enabled=True, webhook=WEBHOOK, secret=SECRET))
        attempts = []
        completed = asyncio.Event()

        async def send(config, text):
            attempts.append(text)
            completed.set()
            return "sent", None

        monkeypatch.setattr(dingtalk, "send_notification", send)
        consume = asyncio.create_task(service.consume())
        worker = asyncio.create_task(service.dingtalk.run())
        historical = {"from": "10000", "content": "历史模拟", "received_at": 1700000000}
        live = {
            "type": "incoming_sms",
            "from": "10000",
            "content": "新短信模拟",
            "received_at": 1700000001,
        }
        service.events.put_nowait({"type": "inbox_response", "messages": [historical]})
        service.events.put_nowait(live)
        service.events.put_nowait(live)
        service.events.put_nowait({"type": "inbox_response", "messages": [live]})
        await asyncio.wait_for(completed.wait(), 1)
        await asyncio.wait_for(service.dingtalk.queue.join(), 1)
        assert len(attempts) == 1 and "新短信模拟" in attempts[0]
        assert "10000" in attempts[0] and "历史模拟" not in attempts[0]
        assert store.push_list()[0]["state"] == "sent"
        assert len(store.list()) == 2
        for task in (consume, worker):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        service.dingtalk_settings.save(DingTalkUpdate(enabled=False))
        row = store.incoming(
            {"from": "10000", "content": "停用模拟", "received_at": 1700000002}, ""
        )
        service.dingtalk.enqueue(row)
        assert service.dingtalk.queue.empty()
        assert store.push_claim(row["id"])
        store.push_update(row["id"], "sending")
        store.close()
        restored = Store(tmp_path / "console.sqlite3")
        assert restored.push_list()[0]["state"] == "unknown"
        assert not restored.push_claim(row["id"])
        assert len(restored.list()) == 3
        restored.close()

    asyncio.run(run())


def test_api_origin_privacy_test_and_rate_limit(tmp_path, monkeypatch):
    class Bridge:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def close(self):
            pass

    attempts = []

    async def send(config, text):
        attempts.append(text)
        return "sent", None

    monkeypatch.setattr(main, "DATA", tmp_path)
    install_bridge(monkeypatch, Bridge)
    monkeypatch.setattr(main, "send_notification", send)
    headers = {"origin": "http://127.0.0.1:9011"}
    with TestClient(main.app) as client:
        assert client.put("/api/dingtalk", json={}).status_code == 403
        assert client.post("/api/dingtalk/test").status_code == 403
        body = {"enabled": True, "webhook": WEBHOOK, "secret": SECRET}
        result = client.put("/api/dingtalk", json=body, headers=headers)
        assert result.status_code == 200 and SECRET not in result.text
        assert "abcdefgh1234567890" not in client.get("/api/dingtalk").text
        invalid = client.put(
            "/api/dingtalk", json={**body, "secret": SECRET + "\n"}, headers=headers
        )
        assert invalid.status_code == 422 and SECRET not in invalid.text
        result = client.post("/api/dingtalk/test", headers=headers)
        assert result.status_code == 200 and len(attempts) == 1
        assert "测试" in attempts[0] and "不包含真实短信" in attempts[0]
        assert client.post("/api/dingtalk/test", headers=headers).status_code == 429


def test_full_queue_and_disabled_pending_messages(tmp_path):
    async def run():
        store = Store(tmp_path / "console.sqlite3")
        settings = configured(tmp_path)
        notifier = dingtalk.DingTalkNotifier(store, settings)
        notifier.queue = asyncio.Queue(maxsize=1)
        first = store.incoming({"from": "10000", "content": "模拟一", "received_at": 1}, "")
        second = store.incoming({"from": "10000", "content": "模拟二", "received_at": 2}, "")
        notifier.enqueue(first)
        notifier.enqueue(second)
        assert any(row["state"] == "failed" for row in store.push_list())
        settings.save(DingTalkUpdate(enabled=False))
        worker = asyncio.create_task(notifier.run())
        await asyncio.wait_for(notifier.queue.join(), 1)
        assert any(row["state"] == "skipped" for row in store.push_list())
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        store.close()

    asyncio.run(run())

import asyncio
import json
from contextlib import suppress

import httpx
import pytest
from test_dingtalk_reply import setup

from app import dingtalk_stream_push
from app.dingtalk import DingTalkUpdate
from app.dingtalk_reply import ReplySettings, ReplyUpdate
from app.dingtalk_stream_push import StreamNotifier, push_config
from app.main import Service
from app.store import Store


def configured(tmp_path):
    store = Store(tmp_path / "console.sqlite3")
    settings = ReplySettings(tmp_path / "dingtalk")
    settings.save(
        ReplyUpdate(
            enabled=True,
            client_id="app-test",
            client_secret="secret-test",
            push_enabled=True,
            push_conversation_id="group-test",
            robot_code="robot-test",
        )
    )
    return StreamNotifier(store, settings)


def mock_http(monkeypatch, handler):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        dingtalk_stream_push.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw),
    )


def test_application_api_payload_and_token_cache(tmp_path, monkeypatch):
    notifier = configured(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.host == "api.dingtalk.com"
        data = json.loads(request.content)
        if request.url.path == "/v1.0/oauth2/accessToken":
            assert data == {"appKey": "app-test", "appSecret": "secret-test"}
            return httpx.Response(200, json={"accessToken": "token-private", "expireIn": 7200})
        assert request.headers["x-acs-dingtalk-access-token"] == "token-private"
        assert data == {
            "robotCode": "robot-test",
            "openConversationId": "group-test",
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": "模拟短信"}, ensure_ascii=False),
        }
        return httpx.Response(200, json={"processQueryKey": "accepted"})

    mock_http(monkeypatch, handler)

    async def run():
        config = push_config(notifier.settings)
        assert await notifier.send(config, "模拟短信") == ("sent", None)
        assert await notifier.send(config, "模拟短信") == ("sent", None)

    asyncio.run(run())
    assert calls == [
        "/v1.0/oauth2/accessToken",
        "/v1.0/robot/groupMessages/send",
        "/v1.0/robot/groupMessages/send",
    ]
    assert "token-private" not in json.dumps(notifier.settings.snapshot())
    notifier.store.close()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("token_failure", "failed"),
        ("token_network", "failed"),
        ("rejected", "failed"),
        ("unauthorized", "failed"),
        ("timeout", "unknown"),
        ("server_error", "unknown"),
        ("malformed", "unknown"),
    ],
)
def test_failures_are_sanitized_and_never_retry(tmp_path, monkeypatch, mode, expected):
    notifier = configured(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("accessToken"):
            if mode == "token_failure":
                return httpx.Response(403, json={"message": "secret-test"})
            if mode == "token_network":
                raise httpx.ConnectError("secret-test", request=request)
            return httpx.Response(200, json={"accessToken": "private-token", "expireIn": 7200})
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-test", request=request)
        if mode == "server_error":
            return httpx.Response(500, json={"message": "secret-test"})
        if mode == "malformed":
            return httpx.Response(200, text="secret-test")
        return httpx.Response(
            401 if mode == "unauthorized" else 403, json={"message": "secret-test"}
        )

    mock_http(monkeypatch, handler)
    state, error = asyncio.run(notifier.send(push_config(notifier.settings), "模拟"))
    assert state == expected and "secret-test" not in error
    assert len(calls) == (1 if mode.startswith("token_") else 2)
    if mode == "unauthorized":
        assert notifier.token_until == 0
    notifier.store.close()


@pytest.mark.parametrize(
    ("webhook", "stream"), [(False, False), (False, True), (True, False), (True, True)]
)
def test_incoming_routes_modes_independently_and_skips_history(tmp_path, webhook, stream):
    store = Store(tmp_path / "console.sqlite3")
    service = Service(store)
    service.dingtalk_settings.save(
        DingTalkUpdate(
            enabled=webhook,
            webhook="https://oapi.dingtalk.com/robot/send?access_token=abcdefgh1234567890",
            secret="SECabcdefgh1234567890",
        )
    )
    service.dingtalk_replies.settings.save(
        ReplyUpdate(
            enabled=True,
            client_id="test",
            client_secret="secret",
            push_enabled=stream,
            push_conversation_id="group-test",
            allow_sending=False,
        )
    )

    async def run():
        task = asyncio.create_task(service.consume())
        await service.events.put(
            {"type": "incoming_sms", "from": "10000", "content": "新短信", "received_at": 1}
        )
        await service.events.put(
            {
                "type": "inbox_response",
                "messages": [{"from": "10000", "content": "历史短信", "received_at": 2}],
            }
        )
        for _ in range(10):
            await asyncio.sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert service.dingtalk.queue.qsize() == int(webhook)
    assert service.dingtalk_replies.notifier.queue.qsize() == int(stream)
    assert len(store.list()) == 2
    store.close()


@pytest.mark.parametrize("disable", [False, True])
def test_queue_dedupes_and_skips_disabled_push(tmp_path, monkeypatch, disable):
    notifier = configured(tmp_path)
    original = notifier.store.incoming(
        {"from": "10000", "content": "模拟", "received_at": 1}, "sim-test"
    )
    notifier.enqueue(original)
    notifier.enqueue(original)
    assert notifier.queue.qsize() == 1
    sent = []

    async def send(config, text):
        sent.append(text)
        return "sent", None

    monkeypatch.setattr(notifier, "send", send)
    if disable:
        config = notifier.settings.read()
        notifier.settings.save(ReplyUpdate(**{**config, "push_enabled": False}))

    async def run():
        task = asyncio.create_task(notifier.run())
        await notifier.queue.join()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert bool(sent) is not disable
    assert notifier.store.stream_push_list()[0]["state"] == ("skipped" if disable else "sent")
    if sent:
        assert f"短信编号：{original['id'][3:19]}" in sent[0]
    notifier.store.close()


def test_stream_push_alone_can_be_quoted_for_sms_reply(tmp_path, monkeypatch):
    service, message, sent, _, ref = setup(tmp_path, monkeypatch)
    original = service.store.list()[0]
    service.store.conn.execute("DELETE FROM sms_pushes")
    service.store.conn.commit()
    service.store.stream_push_claim(original["id"], "group-test")
    service.store.stream_push_update(original["id"], "sent")
    asyncio.run(service.dingtalk_replies.handle(message))
    assert len(sent) == 1 and sent[0]["to"] == "10000"
    assert service.store.reply_list()[0]["reference"] == ref
    service.store.close()


def test_restart_preserves_claims_without_replaying_push(tmp_path):
    notifier = configured(tmp_path)
    notifier.store.stream_push_claim("rx-test", "group-test")
    notifier.store.close()
    restored = Store(tmp_path / "console.sqlite3")
    assert restored.stream_push_list()[0]["state"] == "unknown"
    assert not restored.stream_push_claim("rx-test", "another-group")
    restored.close()


def test_push_requires_explicit_target_and_credentials(tmp_path):
    settings = ReplySettings(tmp_path / "dingtalk")
    with pytest.raises(ValueError):
        settings.save(ReplyUpdate(push_enabled=True, push_conversation_id="group-test"))
    with pytest.raises(ValueError):
        settings.save(
            ReplyUpdate(enabled=True, client_id="test", client_secret="secret", push_enabled=True)
        )
    settings.save(ReplyUpdate(enabled=True, client_id="test", client_secret="secret"))
    assert settings.snapshot()["push_enabled"] is False


@pytest.mark.parametrize(
    ("push_group", "reply_group", "expected"),
    [("", "reply-group", "reply-group"), ("push-group", "reply-group", "push-group")],
)
def test_push_defaults_to_reply_group_and_explicit_override(
    tmp_path, push_group, reply_group, expected
):
    notifier = configured(tmp_path)
    config = notifier.settings.read()
    notifier.settings.save(
        ReplyUpdate(
            **{
                **config,
                "push_conversation_id": push_group,
                "conversation_id": reply_group,
            }
        )
    )
    assert push_config(notifier.settings)["push_conversation_id"] == expected
    original = notifier.store.incoming(
        {"from": "10000", "content": "模拟", "received_at": 1}, "sim-test"
    )
    notifier.enqueue(original)
    assert notifier.store.stream_push_list()[0]["conversation_id"] == expected
    notifier.store.close()

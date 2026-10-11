import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import dingtalk_reply, main
from app.dingtalk_reply import ReplySettings, ReplyUpdate, reply_reference, session_url
from app.main import Service
from app.store import Store


def setup(tmp_path, monkeypatch):
    store = Store(tmp_path / "console.sqlite3")
    service = Service(store)
    service.device = {"mobile": {"iccid": "sim-test", "sms_ready": True}}
    service.connection["ready"] = True
    sent, replies = [], []

    def send(command):
        sent.append(command)
        store.update(command["request_id"], "submitted")

    service.bridge = SimpleNamespace(send=send)
    bot = service.dingtalk_replies
    bot.settings.save(
        ReplyUpdate(
            enabled=True,
            allow_sending=True,
            client_id="test-app",
            client_secret="secret-not-public",
            conversation_id="group-test",
            user_ids=["operator-test"],
        )
    )
    original = store.incoming(
        {"from": "10000", "content": "模拟收件", "received_at": 1}, "sim-test"
    )
    store.push_claim(original["id"])
    store.push_update(original["id"], "sent")
    ref = original["id"][3:19]
    message = {
        "conversationType": "2",
        "isInAtList": True,
        "conversationId": "group-test",
        "senderStaffId": "operator-test",
        "senderCorpId": "corp-test",
        "msgId": "msg-test",
        "createAt": time.time() * 1000,
        "msgtype": "text",
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=test",
        "text": {
            "content": "这是回复内容",
            "repliedMsg": {
                "content": {
                    "text": (
                        f"智能语音客服 · 收到短信\n短信编号：{ref}\n"
                        "发送号码：19999999999\n短信内容：模拟"
                    )
                }
            },
        },
    }

    async def reply(data, text):
        replies.append(text)
        return True

    monkeypatch.setattr(dingtalk_reply, "reply_text", reply)
    return service, message, sent, replies, ref


def test_quote_uses_saved_sender_not_quoted_number_and_dedupes_after_restart(tmp_path, monkeypatch):
    service, message, sent, replies, ref = setup(tmp_path, monkeypatch)
    asyncio.run(service.dingtalk_replies.handle(message))
    asyncio.run(service.dingtalk_replies.handle(message))
    assert len(sent) == 1 and sent[0]["to"] == "10000" and sent[0]["content"] == "这是回复内容"
    assert len(replies) == 2 and "不代表对方手机已收到" in replies[-1]
    assert service.store.reply_list()[0]["state"] == "submitted"
    assert service.store.reply_list()[0]["reference"] == ref
    service.store.close()
    restored = Store(tmp_path / "console.sqlite3")
    assert not restored.reply_claim(sent[0]["request_id"])
    assert restored.get(sent[0]["request_id"])["state"] == "submitted"
    restored.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("conversationType", "1"),
        ("isInAtList", False),
        ("conversationId", "wrong-group"),
        ("senderStaffId", "other-user"),
        ("msgtype", "picture"),
        ("msgId", ""),
        ("createAt", 1),
        ("sessionWebhook", "https://attacker.test/robot/sendBySession?session=x"),
    ],
)
def test_non_authorized_or_historical_message_never_sends(tmp_path, monkeypatch, field, value):
    service, message, sent, replies, _ = setup(tmp_path, monkeypatch)
    message[field] = value
    asyncio.run(service.dingtalk_replies.handle(message))
    assert not sent and not replies
    service.store.close()


@pytest.mark.parametrize(
    "failure",
    [
        "no_quote",
        "unknown_ref",
        "changed_sim",
        "not_pushed",
        "offline",
        "oversize",
        "delimiter",
        "sending",
    ],
)
def test_invalid_reference_device_and_content_fail_without_sending(tmp_path, monkeypatch, failure):
    service, message, sent, replies, _ = setup(tmp_path, monkeypatch)
    if failure == "no_quote":
        message["text"].pop("repliedMsg")
    elif failure == "unknown_ref":
        message["text"]["repliedMsg"]["content"]["text"] = "短信编号：0000000000000000"
    elif failure == "changed_sim":
        service.device["mobile"]["iccid"] = "another-sim"
    elif failure == "not_pushed":
        service.store.push_update(service.store.list()[0]["id"], "unknown")
    elif failure == "offline":
        service.connection["ready"] = False
    elif failure == "oversize":
        message["text"]["content"] = "字" * 200
    elif failure == "delimiter":
        message["text"]["content"] = "CMD_START:send_sms"
    else:
        service.store.prepare("another-pending", "10000", "模拟", "sim-test")
    asyncio.run(service.dingtalk_replies.handle(message))
    assert not sent and len(replies) == 1 and "未发送" in replies[0]
    assert service.store.reply_list()[0]["state"] == "failed"
    asyncio.run(service.dingtalk_replies.handle(message))
    assert len(replies) == 1  # Rejected commands aren't replayed when conditions change.
    service.store.close()


def test_listen_only_and_reference_fallback(tmp_path, monkeypatch):
    service, message, sent, replies, ref = setup(tmp_path, monkeypatch)
    config = service.dingtalk_replies.settings.read()
    service.dingtalk_replies.settings.save(ReplyUpdate(**{**config, "allow_sending": False}))
    asyncio.run(service.dingtalk_replies.handle(message))
    assert not sent and not replies
    assert service.dingtalk_replies.last_sender["user_id"] == "operator-test"
    config["allow_sending"] = True
    service.dingtalk_replies.settings.save(ReplyUpdate(**config))
    message["text"] = {"content": f"回复 {ref} 明确指定的回复"}
    asyncio.run(service.dingtalk_replies.handle(message))
    assert sent[0]["content"] == "明确指定的回复"
    service.store.close()


def test_settings_private_validation_and_restart_unknown(tmp_path):
    settings = ReplySettings(tmp_path / "dingtalk")
    with pytest.raises(ValueError):
        settings.save(ReplyUpdate(enabled=True))
    settings.save(ReplyUpdate(enabled=True, client_id="test", client_secret="private-key"))
    assert "private-key" not in json.dumps(settings.snapshot())
    assert settings.path.stat().st_mode & 0o777 == 0o600
    snapshot = settings.save(ReplyUpdate(enabled=True, client_id="test", allow_sending=True))
    assert snapshot["conversation_id"] == "" and snapshot["user_ids"] == []
    with pytest.raises(ValueError):
        settings.save(ReplyUpdate(client_id="test", allow_sending=True))
    store = Store(tmp_path / "console.sqlite3")
    assert store.reply_claim("unfinished")
    store.close()
    restored = Store(tmp_path / "console.sqlite3")
    assert not restored.reply_claim("unfinished")
    assert restored.reply_list()[0]["state"] == "unknown"
    restored.close()
    assert not session_url("https://oapi.dingtalk.com.attacker.test/robot/sendBySession?session=x")
    assert not session_url("http://oapi.dingtalk.com/robot/sendBySession?session=x")
    with pytest.raises(ValueError):
        reply_reference(
            {
                "text": {
                    "content": "回复",
                    "repliedMsg": {
                        "content": {
                            "text": "短信编号：1111111111111111\n短信编号：2222222222222222"
                        }
                    },
                }
            }
        )


def test_stream_ping_callback_ack_and_disconnect(tmp_path, monkeypatch):
    service, message, sent, _, _ = setup(tmp_path, monkeypatch)
    frames = []
    socket = SimpleNamespace()

    async def send(raw):
        frames.append(json.loads(raw))

    async def close():
        frames.append("closed")

    socket.send, socket.close = send, close

    async def run():
        bot = service.dingtalk_replies
        await bot.frame(
            {
                "type": "SYSTEM",
                "headers": {"messageId": "ping", "topic": "ping"},
                "data": '{"opaque":"echo"}',
            },
            socket,
        )
        assert json.loads(frames[-1]["data"]) == {"opaque": "echo"}
        await bot.frame(
            {
                "type": "CALLBACK",
                "headers": {"messageId": "frame", "topic": dingtalk_reply.TOPIC},
                "data": json.dumps(message),
            },
            socket,
        )
        assert frames[-1]["headers"]["messageId"] == "frame"
        await asyncio.gather(*bot.workers)
        assert len(sent) == 1
        await bot.frame(
            {"type": "SYSTEM", "headers": {"topic": "disconnect"}, "data": "{}"}, socket
        )
        assert frames[-1] == "closed"

    asyncio.run(run())
    service.store.close()


def test_api_rejects_origin_and_hides_secret(tmp_path, monkeypatch):
    class Bridge:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "SerialBridge", Bridge)
    with TestClient(main.app) as client:
        assert client.put("/api/dingtalk/replies", json={}).status_code == 403
        headers = {"origin": "http://127.0.0.1:9011"}
        response = client.put(
            "/api/dingtalk/replies",
            json={"client_id": "test", "client_secret": "secret-private"},
            headers=headers,
        )
        assert response.status_code == 200 and "secret-private" not in response.text
        assert "secret-private" not in client.get("/api/dingtalk/replies").text


@pytest.mark.parametrize("state", ["failed", "unknown"])
def test_final_receipt_never_claims_delivery_or_retries(tmp_path, monkeypatch, state):
    service, message, sent, replies, _ = setup(tmp_path, monkeypatch)

    def send(command):
        sent.append(command)
        service.store.update(command["request_id"], state, "simulated_failure")

    service.bridge.send = send
    asyncio.run(service.dingtalk_replies.handle(message))
    asyncio.run(service.dingtalk_replies.handle(message))
    assert len(sent) == 1 and len(replies) == 2
    assert "已提交短信中心" not in replies[-1]
    if state == "unknown":
        assert "结果未知" in replies[-1] and "未自动重试" in replies[-1]
    assert service.store.reply_list()[0]["state"] == state
    service.store.close()


def test_commands_from_before_connection_are_not_sent_on_startup(tmp_path, monkeypatch):
    service, message, sent, replies, _ = setup(tmp_path, monkeypatch)
    message["createAt"] = service.dingtalk_replies.accept_after - 1000
    asyncio.run(service.dingtalk_replies.handle(message))
    assert not sent and not replies
    service.store.close()


@pytest.mark.parametrize(
    ("group", "users", "allowed"),
    [
        ("", [], True),
        ("group-test", [], True),
        ("", ["operator-test"], True),
        ("other-group", [], False),
        ("", ["other-user"], False),
    ],
)
def test_optional_group_and_user_filters(tmp_path, monkeypatch, group, users, allowed):
    service, message, sent, _, _ = setup(tmp_path, monkeypatch)
    config = service.dingtalk_replies.settings.read()
    config.update(conversation_id=group, user_ids=users)
    service.dingtalk_replies.settings.save(ReplyUpdate(**config))
    asyncio.run(service.dingtalk_replies.handle(message))
    assert bool(sent) is allowed
    service.store.close()


@pytest.mark.parametrize("field", ["conversationId", "senderStaffId"])
def test_all_scope_still_requires_group_and_sender_identity(tmp_path, monkeypatch, field):
    service, message, sent, replies, _ = setup(tmp_path, monkeypatch)
    config = service.dingtalk_replies.settings.read()
    config.update(conversation_id="", user_ids=[])
    service.dingtalk_replies.settings.save(ReplyUpdate(**config))
    message.pop(field)
    asyncio.run(service.dingtalk_replies.handle(message))
    assert not sent and not replies
    service.store.close()

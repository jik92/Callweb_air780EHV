import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.main import Command, Service
from app.protocol import FrameParser, encode_command
from app.store import Store


def frame(message):
    return b"SMS_START:" + json.dumps(message, ensure_ascii=False).encode() + b":SMS_END\r\n"


def test_parser_handles_split_utf8_noise_and_multiple_frames():
    parser = FrameParser()
    incoming = {"type": "incoming_sms", "content": "收到中文回复"}
    wire = b"noise\xff" + frame(incoming) + frame({"type": "status_response"})
    result = []
    for byte in wire:
        result.extend(parser.feed(bytes([byte])))
    assert result == [incoming, {"type": "status_response"}]


def test_parser_recovers_from_invalid_json_and_limits_unfinished_frames():
    parser = FrameParser()
    assert parser.feed(b"SMS_START:broken:SMS_END" + frame({"ok": True})) == [{"ok": True}]
    parser.feed(b"SMS_START:" + b"x" * 300_000)
    assert len(parser.buffer) < 256_000
    assert parser.feed(frame({"recovered": True})) == [{"recovered": True}]


def test_inbox_dedupe_and_restart_do_not_resend(tmp_path):
    path = tmp_path / "sms.sqlite3"
    store = Store(path)
    message = {"from": "+15555550100", "content": "你好", "received_at": 1000}
    store.incoming(message, "sim-a")
    store.incoming(message, "sim-a")
    store.incoming(message, "sim-b")
    request_id = str(uuid4())
    store.prepare(request_id, "+15555550100", "测试", "sim-a")
    store.close()
    store = Store(path)
    assert len(store.list()) == 3
    assert store.get(request_id)["state"] == "unknown"
    assert store.get(request_id)["error"] == "service_restarted"
    store.close()


@pytest.mark.parametrize("content", [" ", "中" * 171, "包含:CMD_END分隔符", "包含:SMS_END分隔符"])
def test_reject_invalid_sms(content):
    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "action": "send_sms",
                "request_id": str(uuid4()),
                "to": "+15555550100",
                "content": content,
            }
        )


def test_protocol_command_is_utf8():
    assert "你好" in encode_command({"action": "send_sms", "content": "你好"}).decode()


def test_service_deduplicates_send_requests(tmp_path):
    store = Store(tmp_path / "sms.db")
    service = Service(store)
    sent = []

    class Bridge:
        def send(self, payload):
            sent.append(payload)

    service.bridge = Bridge()
    service.connection["ready"] = True
    service.device = {"mobile": {"sms_ready": True, "iccid": "sim-a"}}
    request = {
        "action": "send_sms",
        "request_id": str(uuid4()),
        "to": "+15555550100",
        "content": "只发一次",
    }
    service.command(request)
    assert service.command(request)["duplicate"] is True
    assert len(sent) == 1
    with pytest.raises(ValueError, match="不同短信"):
        service.command({**request, "content": "不同内容"})
    with pytest.raises(ValueError, match="正在发送"):
        service.command({**request, "request_id": str(uuid4())})
    store.close()


def test_websocket_sim_sms_and_multiclient(tmp_path, monkeypatch):
    sent = []

    class FakeBridge:
        def __init__(self, port, emit):
            self.emit = emit

        def start(self):
            self.emit({"type": "transport", "connected": True, "port": "test-vuart"})
            self.emit(
                {
                    "type": "status_response",
                    "version": "test",
                    "mobile": {"sim_ready": True, "sms_ready": True, "iccid": "sim-a"},
                }
            )
            self.emit({"type": "inbox_response", "messages": {}})

        def close(self):
            pass

        def send(self, payload):
            sent.append(payload)
            if payload["action"] == "send_sms":
                self.emit({"type": "sms_send_accepted", "request_id": payload["request_id"]})
                self.emit(
                    {
                        "type": "sms_send_result",
                        "request_id": payload["request_id"],
                        "success": True,
                    }
                )
                self.emit(
                    {
                        "type": "incoming_sms",
                        "from": payload["to"],
                        "content": "真实接收事件的格式",
                        "received_at": 1000,
                    }
                )

    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "SerialBridge", FakeBridge)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://localhost:9011"}) as first:
            initial = first.receive_json()
            while not initial["bridge"]["ready"]:
                initial = first.receive_json()
            assert initial["device"]["mobile"]["iccid"] == "sim-a"
            with client.websocket_connect(
                "/ws", headers={"origin": "http://localhost:9011"}
            ) as second:
                second.receive_json()
                request = {
                    "action": "send_sms",
                    "request_id": str(uuid4()),
                    "to": "+15555550100",
                    "content": "模拟短信",
                }
                first.send_json(request)
                found = []
                while True:
                    message = second.receive_json()
                    if message["type"] == "snapshot":
                        found = message["messages"]
                        if any(row["direction"] == "incoming" for row in found):
                            break
                assert (
                    next(row for row in found if row["direction"] == "outgoing")["state"]
                    == "submitted"
                )
                second.send_json(request)
                while True:
                    ack = second.receive_json()
                    if ack["type"] == "command_ack":
                        break
                assert ack["duplicate"] is True
                assert len(sent) == 1
        assert client.get("/api/health").json()["device_ready"] is True
        with pytest.raises(Exception) as exc:
            with client.websocket_connect("/ws", headers={"origin": "https://untrusted.example"}):
                pass
        assert exc.value.code == 1008

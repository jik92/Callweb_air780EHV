import json
from contextlib import ExitStack
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.devices import DeviceBinding, DeviceRegistry

HEADERS = {"origin": "http://127.0.0.1:9011"}


def frame(message):
    return b"SMS_START:" + json.dumps(message).encode() + b":SMS_END"


class Bridge:
    def __init__(self, port, emit):
        self.emit = emit

    def start(self):
        pass

    def close(self):
        pass


def test_registry_credentials_and_restart(tmp_path):
    path = tmp_path / "devices.sqlite3"
    registry = DeviceRegistry(path)
    device_id, token = registry.bind("客服一")
    assert registry.authenticate(device_id, token)
    assert not registry.authenticate(device_id, "wrong-token")
    assert not registry.authenticate("unbound", token)
    assert token not in json.dumps(registry.list())
    assert token.encode() not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    registry.close()
    registry = DeviceRegistry(path)
    assert registry.authenticate(device_id, token)
    registry.close()
    for url in (
        "http://public.example/ws/device",
        "wss://example.org/ws",
        "wss://example.org/ws/device?token=bad",
    ):
        with pytest.raises(ValidationError):
            DeviceBinding(name="测试", server_url=url)
    assert DeviceBinding(name="测试", server_url="wss://example.org/ws/device")
    assert DeviceBinding(name="测试", server_url="ws://example.org/ws/device")


def wait_ready(client, device_id):
    for _ in range(100):
        data = client.get("/api/state", params={"device_id": device_id}).json()
        if data["bridge"]["ready"]:
            return data
    raise AssertionError("device did not become ready")


def test_remote_login_two_device_isolation_routing_and_reconnect(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client, ExitStack() as stack:
        assert client.post("/api/devices", json={"name": "一"}).status_code == 403
        bound = [
            client.post("/api/devices", json={"name": name}, headers=HEADERS).json()
            for name in ("一", "二")
        ]
        ids = [item["device_id"] for item in bound]
        assert all(not row["online"] for row in client.get("/api/devices").json())
        device_sockets = []
        for index, credentials in enumerate(bound):
            ws = stack.enter_context(client.websocket_connect("/ws/device"))
            ws.send_json({"type": "device_login", **credentials})
            assert ws.receive_json()["type"] == "device_login_ok"
            assert b"get_status" in ws.receive_bytes()
            assert b"get_inbox" in ws.receive_bytes()
            ws.send_bytes(
                frame(
                    {
                        "type": "status_response",
                        "version": "test",
                        "mobile": {"iccid": f"sim-{index}", "sim_ready": True, "sms_ready": True},
                        "call": {"ready": True, "audio_ready": True, "live_supported": True},
                    }
                )
            )
            ws.send_bytes(
                frame(
                    {
                        "type": "incoming_sms",
                        "from": "10000",
                        "content": f"设备{index}模拟短信",
                        "received_at": 1700000000,
                    }
                )
            )
            device_sockets.append(ws)
        for index, device_id in enumerate(ids):
            data = wait_ready(client, device_id)
            assert data["device_id"] == device_id
            for _ in range(100):
                data = client.get("/api/state", params={"device_id": device_id}).json()
                if data["messages"]:
                    break
            assert len(data["messages"]) == 1
            assert data["messages"][0]["content"] == f"设备{index}模拟短信"
        assert client.get("/api/state").json()["messages"] == []
        assert len([row for row in client.get("/api/devices").json() if row["online"]]) == 2
        for index, device_id in enumerate(ids):
            agent = client.put(
                "/api/agent",
                params={"device_id": device_id},
                json={"instructions": f"设备{index}场景"},
                headers=HEADERS,
            )
            assert agent.status_code == 200
            assert (
                client.get("/api/agent", params={"device_id": device_id}).json()["instructions"]
                == f"设备{index}场景"
            )
        with client.websocket_connect("/ws?device_id=" + ids[0], headers=HEADERS) as browser:
            browser.receive_json()
            browser.send_json(
                {
                    "action": "send_sms",
                    "request_id": str(uuid4()),
                    "to": "10000",
                    "content": "仅设备一模拟发送",
                }
            )
            wire = device_sockets[0].receive_bytes()
            assert b"send_sms" in wire
            device_sockets[0].send_bytes(
                frame(
                    {
                        "type": "sms_send_result",
                        "request_id": json.loads(wire[10:-10])["request_id"],
                        "success": True,
                    }
                )
            )
        # Duplicate device login is rejected without disconnecting the original device.
        with client.websocket_connect("/ws/device") as duplicate:
            duplicate.send_json({"type": "device_login", **bound[0]})
            packet = duplicate.receive()
            assert packet["type"] == "websocket.close" and packet["code"] == 1008
        assert client.get("/api/state", params={"device_id": ids[0]}).json()["bridge"]["ready"]
        assert client.get("/api/state", params={"device_id": "not-bound"}).status_code == 404
    # The binding survives backend restart; unsent work is never replayed.
    with TestClient(main.app) as client:
        assert len(client.get("/api/devices").json()) == 2
        with client.websocket_connect("/ws/device") as ws:
            ws.send_json({"type": "device_login", **bound[0]})
            assert ws.receive_json()["type"] == "device_login_ok"
            assert b"get_status" in ws.receive_bytes()
            assert b"get_inbox" in ws.receive_bytes()
            data = client.get("/api/state", params={"device_id": ids[0]}).json()
            assert len(data["messages"]) == 2


def test_unknown_wrong_token_and_malformed_event_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        bound = client.post("/api/devices", json={"name": "测试"}, headers=HEADERS).json()
        for login in (
            {"type": "device_login", "device_id": bound["device_id"], "token": "bad"},
            {"type": "device_login", "device_id": str(uuid4()), "token": bound["token"]},
        ):
            with client.websocket_connect("/ws/device") as ws:
                ws.send_json(login)
                assert ws.receive()["code"] == 1008
        with client.websocket_connect("/ws/device") as ws:
            ws.send_json({"type": "device_login", **bound})
            ws.receive_json()
            ws.receive_bytes()
            ws.receive_bytes()
            ws.send_bytes(frame({"type": "status_response", "mobile": {"iccid": []}}))
            assert ws.receive()["code"] == 1008
        assert all(not row["online"] for row in client.get("/api/devices").json())
        assert bound["token"] not in client.get("/api/devices").text


def test_remote_only_server_does_not_open_usb(tmp_path, monkeypatch):
    class NoUsb(Bridge):
        def start(self):
            raise AssertionError("remote-only server opened serial port")

        def close(self):
            raise AssertionError("remote-only server closed unstarted bridge")

    monkeypatch.setattr(main, "DATA", tmp_path)
    from app import serial_bridge

    monkeypatch.setattr(serial_bridge, "SerialBridge", NoUsb)
    with TestClient(main.app) as client:
        assert client.get("/api/devices").json() == []


def test_remote_call_answer_and_bidirectional_audio(tmp_path, monkeypatch):
    import struct

    from app.protocol import AUDIO_END, AUDIO_START, encode_audio_input

    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        bound = client.post("/api/devices", json={"name": "模拟通话"}, headers=HEADERS).json()
        device_id = bound["device_id"]
        with client.websocket_connect("/ws/device") as device:
            device.send_json({"type": "device_login", **bound})
            device.receive_json()
            device.receive_bytes()
            device.receive_bytes()
            device.send_bytes(
                frame(
                    {
                        "type": "status_response",
                        "mobile": {},
                        "call": {"ready": True, "audio_ready": True, "live_supported": True},
                    }
                )
            )
            wait_ready(client, device_id)
            call_id = str(uuid4())
            with client.websocket_connect(
                "/ws/call?device_id=" + device_id, headers=HEADERS
            ) as browser:
                browser.receive_json()
                device.send_bytes(
                    frame(
                        {
                            "type": "call_event",
                            "request_id": call_id,
                            "state": "INCOMINGCALL",
                            "to": "10000",
                        }
                    )
                )
                assert browser.receive_json()["state"] == "incoming"
                browser.send_json(
                    {"action": "call_answer", "request_id": str(uuid4()), "call_id": call_id}
                )
                assert b"call_answer" in device.receive_bytes()
                device.send_bytes(
                    frame(
                        {
                            "type": "call_event",
                            "request_id": call_id,
                            "state": "stream_started",
                            "sample_rate": 16000,
                        }
                    )
                )
                while browser.receive_json().get("state") != "streaming":
                    pass
                pcm = b"\x01\x02" * 320
                device.send_bytes(
                    AUDIO_START
                    + call_id.encode()
                    + struct.pack("<HHI", len(pcm), 16000, 1)
                    + pcm
                    + AUDIO_END
                )
                assert browser.receive_bytes() == struct.pack("<HI", 16000, 1) + pcm
                browser.send_bytes(struct.pack("<I", 2) + pcm)
                assert device.receive_bytes() == encode_audio_input(call_id, 2, pcm)
            # Closing the owning browser sends one hangup through the selected device.
            assert b"call_hangup" in device.receive_bytes()
            device.send_bytes(
                frame(
                    {
                        "type": "call_result",
                        "request_id": call_id,
                        "connected": True,
                        "reason": "browser_disconnected",
                    }
                )
            )


def test_outgoing_call_acceptance_keeps_device_connected(tmp_path, monkeypatch):
    from app.devices import valid_event

    assert not valid_event({"type": "call_accepted", "request_id": "invalid"})
    assert not valid_event({"type": "call_accepted", "request_id": str(uuid4()), "to": []})
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        bound = client.post("/api/devices", json={"name": "模拟拨号"}, headers=HEADERS).json()
        device_id = bound["device_id"]
        with client.websocket_connect("/ws/device") as device:
            device.send_json({"type": "device_login", **bound})
            device.receive_json()
            device.receive_bytes()
            device.receive_bytes()
            device.send_bytes(
                frame(
                    {
                        "type": "status_response",
                        "mobile": {},
                        "call": {
                            "ready": True,
                            "audio_ready": True,
                            "live_supported": True,
                        },
                    }
                )
            )
            wait_ready(client, device_id)
            with client.websocket_connect(
                "/ws/call?device_id=" + device_id, headers=HEADERS
            ) as browser:
                browser.receive_json()
                call_id = str(uuid4())
                browser.send_json({"action": "call_dial", "request_id": call_id, "to": "10000"})
                command = json.loads(device.receive_bytes()[10:-10])
                assert command == {"action": "call_live", "request_id": call_id, "to": "10000"}
                device.send_bytes(
                    frame({"type": "call_accepted", "request_id": call_id, "to": "10000"})
                )
                device.send_bytes(
                    frame({"type": "call_event", "request_id": call_id, "state": "CONNECTED"})
                )
                for _ in range(20):
                    if browser.receive_json().get("state") == "connected":
                        break
                else:
                    raise AssertionError("Dial acceptance disconnected the device")
                assert client.get("/api/state", params={"device_id": device_id}).json()["bridge"][
                    "ready"
                ]
                device.send_bytes(
                    frame(
                        {
                            "type": "call_result",
                            "request_id": call_id,
                            "connected": True,
                            "reason": "DISCONNECTED",
                        }
                    )
                )

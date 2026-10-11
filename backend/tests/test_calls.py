import struct
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from support import install_bridge

from app import main
from app.main import Service
from app.protocol import AUDIO_END, AUDIO_START, FrameParser
from app.store import Store


def test_binary_audio_mixed_with_control_and_delimiters():
    call_id = str(uuid4())
    pcm = b"SMS_START::SMS_END\x00\xff" * 40
    pcm += b"\x00" * (len(pcm) % 2)
    audio = (
        AUDIO_START + call_id.encode() + struct.pack("<HHI", len(pcm), 16000, 3) + pcm + AUDIO_END
    )
    wire = b"noise" + audio + b'SMS_START:{"type":"status_response"}:SMS_END'
    parser = FrameParser()
    events = []
    for i in range(0, len(wire), 7):
        events.extend(parser.feed(wire[i : i + 7]))
    assert events[0]["pcm"] == pcm
    assert events[0]["request_id"] == call_id
    assert events[0]["sequence"] == 3
    assert events[1] == {"type": "status_response"}


def test_owner_sequence_dedupe_and_disconnect_hangup(tmp_path):
    store = Store(tmp_path / "sms.db")
    service = Service(store)
    written, audio = [], []

    class Bridge:
        def send(self, payload):
            written.append(payload)

        def send_audio(self, frame):
            audio.append(frame)
            return False

    service.bridge = Bridge()
    service.connection["ready"] = True
    service.device = {"call": {"ready": True, "audio_ready": True, "live_supported": True}}
    owner, stranger = object(), object()
    call_id = str(uuid4())
    request = {"action": "call_dial", "request_id": call_id, "to": "+15555550100"}
    calls = service.calls
    calls.command(request, owner)
    assert calls.command(request, owner)["duplicate"]
    assert len(written) == 1
    with pytest.raises(ValueError, match="已有通话"):
        calls.command({**request, "request_id": str(uuid4())}, stranger)
    calls.event(
        {
            "type": "call_event",
            "request_id": call_id,
            "state": "stream_started",
            "sample_rate": 16000,
        }
    )
    packet = struct.pack("<I", 1) + b"\x00\x00" * 640
    calls.audio(packet, stranger)
    calls.audio(packet, owner)
    calls.audio(packet, owner)
    assert len(audio) == 1
    assert audio[0].startswith(b"PCM_START:" + call_id.encode())
    with pytest.raises(ValueError, match="当前页面"):
        calls.command(
            {"action": "call_hangup", "request_id": str(uuid4()), "call_id": call_id}, stranger
        )
    calls.hangup("browser_disconnected")
    assert written[-1] == {"action": "call_hangup", "request_id": call_id}
    calls.event(
        {"type": "call_result", "request_id": call_id, "connected": True, "reason": "user_hangup"}
    )
    assert calls.state["state"] == "ended"
    assert calls.command(request, owner)["duplicate"]
    assert len(written) == 2
    store.close()


def test_audio_queue_is_bounded(tmp_path):
    from app.serial_bridge import SerialBridge

    bridge = SerialBridge(None, lambda event: None)
    drops = [bridge.send_audio(bytes([i])) for i in range(20)]
    assert sum(drops) == 14
    assert bridge.audio.qsize() == 6
    assert bridge.audio.get_nowait() == bytes([14])


def test_audio_websocket_roundtrip_and_disconnect(tmp_path, monkeypatch):
    sent = []
    instances = []

    class FakeBridge:
        def __init__(self, port, emit):
            self.emit = emit
            instances.append(self)

        def start(self):
            self.emit({"type": "transport", "connected": True, "port": "test"})
            self.emit(
                {
                    "type": "status_response",
                    "call": {"ready": True, "audio_ready": True, "live_supported": True},
                    "mobile": {},
                }
            )

        def close(self):
            pass

        def send(self, command):
            sent.append(command)
            if command["action"] == "call_live":
                self.emit(
                    {
                        "type": "call_event",
                        "request_id": command["request_id"],
                        "state": "stream_started",
                        "sample_rate": 16000,
                    }
                )

        def send_audio(self, frame):
            call_id = frame[10:46].decode()
            length, sequence = struct.unpack_from("<HI", frame, 46)
            self.emit(
                {
                    "type": "call_pcm",
                    "request_id": call_id,
                    "sample_rate": 16000,
                    "sequence": sequence,
                    "pcm": frame[52 : 52 + length],
                }
            )
            return False

    monkeypatch.setattr(main, "DATA", tmp_path)
    install_bridge(monkeypatch, FakeBridge)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://localhost:9011"}) as state:
            while not state.receive_json()["bridge"]["ready"]:
                pass
        with client.websocket_connect(
            "/ws/call", headers={"origin": "http://localhost:9011"}
        ) as ws:
            ws.receive_json()
            ws.send_json({"action": "call_dial", "request_id": str(uuid4()), "to": "+15555550100"})
            while ws.receive_json().get("state") != "streaming":
                pass
            pcm = b"\x01\x02" * 320
            ws.send_bytes(struct.pack("<I", 1) + pcm)
            while True:
                event = ws.receive()
                if event.get("bytes"):
                    assert event["bytes"][6:] == pcm
                    break
        # A websocket disconnect must release the microphone side and request hangup.
        for _ in range(10):
            if sent[-1]["action"] == "call_hangup":
                break
            client.get("/api/health")
        assert sent[-1]["action"] == "call_hangup"


def incoming_service(tmp_path):
    service = Service(Store(tmp_path / "sms.db"))
    sent = []

    class Bridge:
        def send(self, command):
            sent.append(command)

        def send_audio(self, frame):
            sent.append(frame)
            return False

    service.bridge = Bridge()
    service.connection["ready"] = True
    service.device = {"call": {"ready": True, "audio_ready": True, "live_supported": True}}
    return service, sent


def test_incoming_answer_ownership_and_long_call(tmp_path):
    import asyncio
    import time

    service, sent = incoming_service(tmp_path)
    calls = service.calls
    owner, stranger = object(), object()
    calls.clients[owner] = asyncio.Queue(maxsize=16)
    calls.clients[stranger] = asyncio.Queue(maxsize=16)
    call_id = str(uuid4())
    ring = {
        "type": "call_event",
        "request_id": call_id,
        "state": "INCOMINGCALL",
        "to": "+15555550100",
    }
    calls.event(ring)
    calls.event(ring)
    assert calls.state["state"] == "incoming"
    assert calls.clients[stranger].get_nowait()["state"] == "incoming"
    assert len(service.store.call_list()) == 1
    assert sent == []  # Ringing never answers automatically.
    command = {"action": "call_answer", "request_id": str(uuid4()), "call_id": call_id}
    calls.command(command, owner)
    assert sent == [{"action": "call_answer", "request_id": call_id}]
    with pytest.raises(ValueError, match="其他页面"):
        calls.command({**command, "request_id": str(uuid4())}, stranger)
    calls.event(ring)  # Repeated network ring indications cannot undo answer ownership.
    assert calls.state["state"] == "answering"
    calls.event({"type": "call_event", "request_id": call_id, "state": "SPEECH_START"})
    calls.event(
        {
            "type": "call_event",
            "request_id": call_id,
            "state": "stream_started",
            "sample_rate": 8000,
        }
    )
    calls.state["started_at"] = time.time() - 300
    calls.tick()
    assert calls.state["state"] == "streaming"
    packet = struct.pack("<I", 0) + b"\x00\x00" * 320
    calls.audio(packet, stranger)
    assert len(sent) == 1
    calls.audio(packet, owner)
    assert len(sent) == 2
    calls.event(
        {"type": "call_result", "request_id": call_id, "connected": True, "reason": "DISCONNECTED"}
    )
    assert calls.state["state"] == "ended"
    assert service.store.call_get(call_id)["sample_rate"] == 8000
    service.store.close()


def test_incoming_reject_and_caller_cancels(tmp_path):
    service, sent = incoming_service(tmp_path)
    calls = service.calls
    call_id = str(uuid4())
    calls.event(
        {"type": "status_response", "call": {"state": "incoming", "request_id": call_id, "to": ""}}
    )
    calls.command(
        {"action": "call_reject", "request_id": str(uuid4()), "call_id": call_id}, object()
    )
    assert sent == [{"action": "call_hangup", "request_id": call_id}]
    calls.event(
        {"type": "call_result", "request_id": call_id, "connected": False, "reason": "user_hangup"}
    )
    assert calls.state["reason"] == "user_rejected"
    other_id = str(uuid4())
    calls.event(
        {
            "type": "call_event",
            "request_id": other_id,
            "state": "INCOMINGCALL",
            "to": "+15555550100",
        }
    )
    calls.event(
        {
            "type": "call_result",
            "request_id": other_id,
            "connected": False,
            "reason": "DISCONNECTED",
        }
    )
    with pytest.raises(ValueError, match="来电已结束"):
        calls.command(
            {"action": "call_answer", "request_id": str(uuid4()), "call_id": other_id}, object()
        )
    assert len(sent) == 1
    service.store.close()


def test_incoming_answer_queue_full_stays_available(tmp_path):
    import queue

    service, sent = incoming_service(tmp_path)
    call_id = str(uuid4())
    service.calls.event({"type": "call_event", "request_id": call_id, "state": "INCOMINGCALL"})

    class FullBridge:
        def send(self, command):
            raise queue.Full()

    service.bridge = FullBridge()
    with pytest.raises(queue.Full):
        service.calls.command(
            {"action": "call_answer", "request_id": str(uuid4()), "call_id": call_id}, object()
        )
    assert service.calls.state["state"] == "incoming"
    assert service.calls.owner is None
    service.store.close()

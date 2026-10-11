import struct
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setattr(main, "SerialBridge", FakeBridge)
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

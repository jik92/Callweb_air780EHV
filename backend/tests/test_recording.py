import io
import struct
import wave
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, recording
from app.main import Service
from app.recording import Recording, mix
from app.store import Store


@pytest.mark.parametrize("rate", [8000, 16000])
def test_stereo_recording_alignment_gaps_and_bound(tmp_path, monkeypatch, rate):
    clock = [0.0]
    monkeypatch.setattr(recording, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    recorder = Recording(tmp_path, rate)
    count = rate // 25
    clock[0] = 0.04
    recorder.write("up", struct.pack("<h", 1000) * count)
    recorder.write("down", struct.pack("<h", -2000) * count)
    clock[0] = 0.12
    recorder.write("up", struct.pack("<h", 3000) * count)
    clock[0] = 101
    recorder.write("up", b"\1\0" * count)
    recorder.close()
    with wave.open(str(mix(tmp_path)), "rb") as audio:
        assert audio.getparams()[:3] == (2, 2, rate)
        frames = list(struct.iter_unpack("<hh", audio.readframes(audio.getnframes())))
    assert frames[:count] == [(1000, -2000)] * count
    assert frames[count : count * 2] == [(0, 0)] * count
    assert frames[count * 2 :] == [(3000, 0)] * count


def test_history_failure_restart_deduplication_and_partial_recording(tmp_path):
    path = tmp_path / "console.sqlite3"
    store = Store(path)
    service = Service(store)
    commands = []
    service.bridge = SimpleNamespace(send=commands.append, send_audio=lambda _: False)
    owner = object()
    rejected = str(uuid4())
    with pytest.raises(ValueError, match="尚未就绪"):
        service.calls.command(
            {"action": "call_dial", "request_id": rejected, "to": "+15555550100"}, owner
        )
    assert store.call_get(rejected)["state"] == "failed"
    assert not store.call_list()[0]["recording_available"]
    service.connection["ready"] = True
    service.device = {"call": {"live_supported": True, "ready": True, "audio_ready": True}}
    call_id = str(uuid4())
    request = {"action": "call_dial", "request_id": call_id, "to": "+15555550100"}
    service.calls.command(request, owner)
    service.calls.event(
        {
            "type": "call_event",
            "request_id": call_id,
            "state": "stream_started",
            "sample_rate": 16000,
        }
    )
    service.calls.audio(struct.pack("<I", 1) + b"\1\0" * 640, owner)
    service.calls.finish_recording()
    store.close()
    restored = Store(path)
    assert restored.call_get(call_id)["state"] == "unknown"
    assert restored.call_list()[0]["recording_available"]
    recovered_service = Service(restored)
    assert recovered_service.calls.command(request, owner)["duplicate"]
    assert len(commands) == 1
    assert mix(restored.recordings / call_id)
    restored.close()


def test_history_and_recording_http_end_to_end(tmp_path, monkeypatch):
    class Bridge:
        def __init__(self, _port, emit):
            self.emit = emit

        def start(self):
            self.emit({"type": "transport", "connected": True, "port": "test"})
            self.emit(
                {
                    "type": "status_response",
                    "call": {"live_supported": True, "ready": True, "audio_ready": True},
                    "mobile": {},
                }
            )

        def close(self):
            pass

        def send(self, command):
            if command["action"] == "call_live":
                self.call_id = command["request_id"]
                self.emit(
                    {
                        "type": "call_event",
                        "request_id": self.call_id,
                        "state": "stream_started",
                        "sample_rate": 16000,
                    }
                )
            elif command["action"] == "call_hangup":
                self.emit(
                    {
                        "type": "call_result",
                        "request_id": self.call_id,
                        "connected": True,
                        "reason": "user_hangup",
                    }
                )

        def send_audio(self, frame):
            self.emit(
                {
                    "type": "call_pcm",
                    "request_id": self.call_id,
                    "sample_rate": 16000,
                    "sequence": 1,
                    "pcm": b"\2\0" * 640,
                }
            )
            return False

    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "SerialBridge", Bridge)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://localhost:9011"}) as ws:
            while not ws.receive_json()["bridge"]["ready"]:
                pass
        call_id = str(uuid4())
        with client.websocket_connect(
            "/ws/call", headers={"origin": "http://localhost:9011"}
        ) as ws:
            ws.receive_json()
            ws.send_json({"action": "call_dial", "request_id": call_id, "to": "+15555550100"})
            while ws.receive_json().get("state") != "streaming":
                pass
            ws.send_bytes(struct.pack("<I", 1) + b"\1\0" * 640)
            while not ws.receive().get("bytes"):
                pass
            assert client.get(f"/api/calls/{call_id}/recording").status_code == 404
            ws.send_json({"action": "call_hangup", "call_id": call_id, "request_id": str(uuid4())})
            while ws.receive_json().get("state") != "ended":
                pass
        rows = client.get("/api/calls").json()
        assert len(rows) == 1 and rows[0]["recording_available"]
        assert rows[0]["phone"] == "+15555550100"
        response = client.get(f"/api/calls/{call_id}/recording")
        assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(response.content), "rb") as audio:
            assert audio.getnchannels() == 2 and audio.getframerate() == 16000
            assert audio.getnframes() >= 640
        ranged = client.get(f"/api/calls/{call_id}/recording", headers={"Range": "bytes=0-43"})
        assert ranged.status_code == 206 and len(ranged.content) == 44
        assert client.get(f"/api/calls/{uuid4()}/recording").status_code == 404
        assert client.get("/api/calls/../../console.sqlite3/recording").status_code != 200
    with TestClient(main.app) as client:
        assert client.get("/api/calls").json()[0]["recording_available"]


def test_recording_write_failure_does_not_drop_the_call(tmp_path, monkeypatch):
    store = Store(tmp_path / "console.sqlite3")
    service = Service(store)
    sent = []
    service.bridge = SimpleNamespace(send=sent.append, send_audio=lambda _: False)
    service.connection["ready"] = True
    service.device = {"call": {"live_supported": True, "ready": True, "audio_ready": True}}
    owner, call_id = object(), str(uuid4())
    service.calls.command(
        {"action": "call_dial", "request_id": call_id, "to": "+15555550100"}, owner
    )
    service.calls.event(
        {
            "type": "call_event",
            "request_id": call_id,
            "state": "stream_started",
            "sample_rate": 16000,
        }
    )
    monkeypatch.setattr(
        service.calls.recording, "write", lambda *_: (_ for _ in ()).throw(OSError("disk full"))
    )
    service.calls.audio(struct.pack("<I", 1) + b"\0\0" * 640, owner)
    assert service.calls.state["state"] == "streaming"
    assert service.calls.state["recording_error"] == "disk full"
    assert service.calls.state["up_bytes"] == 1280
    service.calls.event(
        {"type": "call_result", "request_id": call_id, "connected": True, "reason": "user_hangup"}
    )
    assert store.call_get(call_id)["recording_error"] == "disk full"
    assert not store.call_list()[0]["recording_available"]
    # Late start events must not truncate or reopen an ended recording.
    service.calls.event(
        {
            "type": "call_event",
            "request_id": call_id,
            "state": "stream_started",
            "sample_rate": 16000,
        }
    )
    assert service.calls.state["state"] == "ended"
    store.close()

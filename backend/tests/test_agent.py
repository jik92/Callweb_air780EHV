import asyncio
import base64
import json
import struct
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main, realtime_agent
from app.agent_settings import AgentSettings, AgentUpdate
from app.main import Service
from app.store import Store


def test_settings_key_is_private_and_markdown_is_live(tmp_path, monkeypatch):
    monkeypatch.delenv("AIHUBMIX_API_KEY", raising=False)
    settings = AgentSettings(tmp_path / "agent")
    assert not settings.snapshot()["ready"]
    with pytest.raises(ValueError, match="启用"):
        settings.session_config()
    update = AgentUpdate(
        enabled=True, api_key="test-private-key", instructions="# 场景\n只回答营业时间。"
    )
    public = settings.save(update)
    assert public["ready"] and public["has_api_key"]
    assert "test-private-key" not in json.dumps(public)
    assert settings.path.stat().st_mode & 0o777 == 0o600
    assert settings.directory.stat().st_mode & 0o777 == 0o700
    settings.markdown.write_text("# 新场景\n只回复预约事项。")
    assert settings.session_config()["instructions"] == settings.markdown.read_text()
    settings.save(update.model_copy(update={"api_key": ""}))
    assert settings.session_config()["api_key"] == "test-private-key"
    with pytest.raises(ValueError, match="切换提供方"):
        settings.save(update.model_copy(update={"api_key": "", "provider": "openai"}))
    settings.save(update.model_copy(update={"clear_api_key": True, "api_key": ""}))
    assert not settings.snapshot()["has_api_key"]
    monkeypatch.setenv("AIHUBMIX_API_KEY", "env-test-key")
    assert settings.snapshot()["key_source"] == "environment"
    assert settings.session_config()["api_key"] == "env-test-key"


class FakeSocket:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, raw):
        self.sent.append(json.loads(raw))
        if self.sent[-1]["type"] == "session.update":
            self.events.put_nowait(json.dumps({"type": "session.updated"}))

    async def recv(self):
        return await self.events.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()

    async def close(self):
        self.closed = True


def build_service(tmp_path):
    service = Service(Store(tmp_path / "console.sqlite3"))
    service.connection["ready"] = True
    service.device = {"call": {"ready": True, "audio_ready": True, "live_supported": True}}
    service.agent_settings.save(AgentUpdate(enabled=True, api_key="test-key", greeting=""))
    sent, audio = [], []

    class Bridge:
        cleared = 0

        def send(self, command):
            sent.append(command)

        def send_audio(self, frame):
            audio.append(frame)
            return False

        def clear_audio(self):
            self.cleared += 1

    service.bridge = Bridge()
    call_id = str(uuid4())
    service.calls.event({"type": "call_event", "state": "INCOMINGCALL", "request_id": call_id})
    return service, call_id, sent, audio


@pytest.mark.parametrize("rate", [8000, 16000])
def test_agent_audio_bridge_pacing_resampling_interrupt_and_cleanup(tmp_path, monkeypatch, rate):
    async def run():
        service, call_id, sent, audio = build_service(tmp_path)
        socket = FakeSocket()
        connections = []

        async def connect(url, **options):
            connections.append((url, options))
            return socket

        monkeypatch.setattr(realtime_agent, "connect", connect)
        owner = object()
        await service.calls.answer_agent({"request_id": str(uuid4()), "call_id": call_id}, owner)
        assert connections[0][0] == "wss://aihubmix.com/v1/realtime?model=gpt-realtime-2.1"
        assert connections[0][1]["additional_headers"]["Authorization"] == "Bearer test-key"
        assert socket.sent[0]["session"]["instructions"].startswith("# 电话助手")
        assert sent == [{"action": "call_answer", "request_id": call_id}]
        with pytest.raises(ValueError, match="其他页面"):
            await service.calls.answer_agent(
                {"request_id": str(uuid4()), "call_id": call_id}, object()
            )
        service.calls.event(
            {
                "type": "call_event",
                "request_id": call_id,
                "state": "stream_started",
                "sample_rate": rate,
            }
        )
        agent = service.calls.agent
        # 200 ms of phone audio must be converted to 24 kHz and sent to the model.
        pcm = (np.sin(np.arange(rate // 5) * 2 * np.pi * 440 / rate) * 8000).astype("<i2").tobytes()
        for index, offset in enumerate(range(0, len(pcm), rate // 50 * 2)):
            service.calls.event(
                {
                    "type": "call_pcm",
                    "request_id": call_id,
                    "sample_rate": rate,
                    "sequence": index,
                    "pcm": pcm[offset : offset + rate // 50 * 2],
                }
            )
        await asyncio.sleep(0.08)
        model_input = [e for e in socket.sent if e["type"] == "input_audio_buffer.append"]
        assert model_input
        converted = b"".join(base64.b64decode(e["audio"]) for e in model_input)
        assert 8000 < len(converted) <= 9600
        # 100 ms of 24 kHz output is converted back and paced as 20 ms frames.
        output = (np.sin(np.arange(2400) * 2 * np.pi * 660 / 24000) * 6000).astype("<i2").tobytes()
        socket.events.put_nowait(
            json.dumps(
                {
                    "type": "response.output_audio.delta",
                    "item_id": "reply-1",
                    "delta": base64.b64encode(output).decode(),
                }
            )
        )
        socket.events.put_nowait(json.dumps({"type": "response.output_audio.done"}))
        await asyncio.sleep(0.16)
        assert 6 <= len(audio) <= 18  # Silence keeps the device stream alive without bursts.
        assert all(len(frame) == 60 + rate // 50 * 2 for frame in audio)
        sequences = [struct.unpack_from("<I", frame, 48)[0] for frame in audio]
        assert sequences == list(range(len(sequences)))
        assert any(any(frame[52:-8]) for frame in audio)
        assert agent.played["reply-1"] == rate // 10 * 2
        agent.queue_audio({"item_id": "reply-1", "delta": base64.b64encode(output).decode()})
        await agent.interrupt()
        assert not agent.output and not agent.pending
        assert socket.sent[-1]["type"] == "conversation.item.truncate"
        assert socket.sent[-1]["audio_end_ms"] == 100
        agent.queue_audio({"item_id": "reply-1", "delta": base64.b64encode(output).decode()})
        assert not agent.output  # Late deltas from a cancelled response cannot resume playback.
        service.calls.hangup("user_hangup")
        await agent.close()
        after = len(audio)
        await asyncio.sleep(0.04)
        assert len(audio) == after and socket.closed
        assert service.calls.agent is None
        service.calls.close()
        service.store.close()

    asyncio.run(run())


def test_agent_connection_failure_and_cancel_never_answer(tmp_path, monkeypatch):
    async def run():
        service, call_id, sent, _ = build_service(tmp_path)

        async def failed(url, **kwargs):
            raise OSError("private upstream detail")

        monkeypatch.setattr(realtime_agent, "connect", failed)
        with pytest.raises(ValueError, match="连接失败"):
            await service.calls.answer_agent(
                {"request_id": str(uuid4()), "call_id": call_id}, object()
            )
        assert not sent and service.calls.state["state"] == "incoming"
        assert service.calls.owner is None and service.calls.agent is None
        started = asyncio.Event()

        async def blocked(url, **kwargs):
            started.set()
            await asyncio.Future()

        monkeypatch.setattr(realtime_agent, "connect", blocked)
        task = asyncio.create_task(
            service.calls.answer_agent({"request_id": str(uuid4()), "call_id": call_id}, object())
        )
        await started.wait()
        service.calls.event(
            {
                "type": "call_result",
                "request_id": call_id,
                "connected": False,
                "reason": "DISCONNECTED",
            }
        )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not sent and service.calls.state["state"] == "failed"
        assert service.calls.agent is None
        service.store.close()

    asyncio.run(run())


def test_agent_api_masks_keys_and_checks_origin(tmp_path, monkeypatch):
    class Bridge:
        def __init__(self, port, emit):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "SerialBridge", Bridge)
    with TestClient(main.app) as client:
        assert client.put("/api/agent", json={}).status_code == 403
        headers = {"origin": "http://127.0.0.1:9011"}
        body = AgentUpdate(enabled=True, api_key="private-test-key").model_dump()
        result = client.put("/api/agent", json=body, headers=headers)
        assert result.status_code == 200
        assert "private-test-key" not in result.text
        assert "private-test-key" not in client.get("/api/agent").text
        invalid = client.put(
            "/api/agent", json={**body, "api_key": "private-test-key\n"}, headers=headers
        )
        assert invalid.status_code == 422 and "private-test-key" not in invalid.text
        assert (tmp_path / "agent" / "AGENT.md").exists()


def test_websocket_disconnect_during_agent_setup_does_not_accept(tmp_path, monkeypatch):
    call_id = str(uuid4())
    sent = []

    class Bridge:
        def __init__(self, port, emit):
            self.emit = emit

        def start(self):
            self.emit({"type": "transport", "connected": True, "port": "test"})
            self.emit({"type": "status_response", "call": {"audio_ready": True}, "mobile": {}})
            self.emit({"type": "call_event", "request_id": call_id, "state": "INCOMINGCALL"})

        def send(self, command):
            sent.append(command)

        def close(self):
            pass

    async def blocked(url, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "SerialBridge", Bridge)
    monkeypatch.setattr(realtime_agent, "connect", blocked)
    with TestClient(main.app) as client:
        main.app.state.service.agent_settings.save(AgentUpdate(enabled=True, api_key="test-key"))
        with client.websocket_connect(
            "/ws/call", headers={"origin": "http://localhost:9011"}
        ) as ws:
            while ws.receive_json().get("state") != "incoming":
                pass
            ws.send_json(
                {"action": "call_agent_answer", "request_id": str(uuid4()), "call_id": call_id}
            )
            while ws.receive_json().get("state") != "agent_connecting":
                pass
        for _ in range(20):
            if client.get("/api/call").json()["state"] == "incoming":
                break
        assert client.get("/api/call").json()["state"] == "incoming"
        assert sent == []


def test_upstream_error_hangs_up_without_retry(tmp_path, monkeypatch):
    async def run():
        service, call_id, sent, audio = build_service(tmp_path)
        socket = FakeSocket()
        connected = 0

        async def connect(url, **kwargs):
            nonlocal connected
            connected += 1
            return socket

        monkeypatch.setattr(realtime_agent, "connect", connect)
        await service.calls.answer_agent({"request_id": str(uuid4()), "call_id": call_id}, object())
        service.calls.event(
            {
                "type": "call_event",
                "request_id": call_id,
                "state": "stream_started",
                "sample_rate": 16000,
            }
        )
        agent = service.calls.agent
        socket.events.put_nowait(
            json.dumps({"type": "error", "error": {"message": "private-error"}})
        )
        await asyncio.wait_for(agent.task, 1)
        assert service.calls.state["state"] == "hanging_up"
        assert service.calls.state["agent_error"] == "agent_connection_failed"
        assert sent[-1]["action"] == "call_hangup"
        assert connected == 1 and socket.closed and service.calls.agent is None
        service.calls.close()
        service.store.close()

    asyncio.run(run())

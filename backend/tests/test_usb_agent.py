import asyncio
import json
import queue
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
from usb_agent import decode_command, encode_event, load_config, session  # noqa: E402

from app.protocol import FrameParser, encode_audio_input, encode_command


def test_agent_config_and_binary_protocol(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {"server_url": "http://example.com/ws/device", "device_id": "x", "token": "secret"}
        )
    )
    with pytest.raises(ValueError):
        load_config(path)
    for packet in [b"bad", b"PCM_START:" + b"x" * 60 + b":PCM_END"]:
        with pytest.raises(ValueError):
            decode_command(packet)
    pcm = b"\x01\x02" * 320
    call_id = str(uuid4())
    packet = encode_audio_input(call_id, 1, pcm)
    assert decode_command(packet) == packet
    event = {
        "type": "call_pcm",
        "request_id": call_id,
        "sequence": 1,
        "sample_rate": 16000,
        "pcm": pcm,
    }
    assert FrameParser().feed(encode_event(event)) == [event]


def test_agent_usb_ws_audio_and_disconnect_do_not_replay():
    async def scenario():
        loop = asyncio.get_running_loop()
        instances = []
        pcm_received = asyncio.Event()
        pcm = b"\x01\x02" * 320
        call_id, sms_id, device_id = str(uuid4()), str(uuid4()), str(uuid4())

        class USB:
            def __init__(self, port, emit):
                self.emit = emit
                self.commands = queue.Queue()
                self.sent, self.audio = [], []
                self.stop = threading.Event()
                self.thread = threading.Thread(target=self.work, daemon=True)
                instances.append(self)

            def discover(self):
                return "simulated-vuart"

            def start(self):
                self.thread.start()

            def work(self):
                self.emit({"type": "transport", "connected": True})
                self.emit({"type": "status_response", "mobile": {}, "call": {}})
                while not self.stop.is_set():
                    try:
                        command = self.commands.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    self.sent.append(command)
                    self.emit(
                        {
                            "type": "command_written",
                            "action": command["action"],
                            "request_id": command.get("request_id"),
                        }
                    )
                    if command["action"] == "send_sms":
                        self.emit(
                            {"type": "sms_send_result", "request_id": sms_id, "success": True}
                        )
                    if command["action"] == "call_live":
                        self.emit(
                            {
                                "type": "call_event",
                                "request_id": call_id,
                                "state": "stream_started",
                                "sample_rate": 16000,
                            }
                        )
                        self.emit(
                            {
                                "type": "call_pcm",
                                "request_id": call_id,
                                "sample_rate": 16000,
                                "sequence": 1,
                                "pcm": pcm,
                            }
                        )

            def send(self, command):
                self.commands.put_nowait(command)

            def send_audio(self, packet):
                self.audio.append(packet)
                loop.call_soon_threadsafe(pcm_received.set)
                return False

            def clear_audio(self):
                pass

            def close(self):
                self.stop.set()
                self.thread.join(timeout=1)

        rounds = 0

        async def api(ws):
            nonlocal rounds
            rounds += 1
            login = json.loads(await ws.recv())
            assert login == {
                "type": "device_login",
                "device_id": device_id,
                "token": "test-only",
                "serial_port": "simulated-vuart",
            }
            await ws.send(json.dumps({"type": "device_login_ok", "device_id": device_id}))
            parser = FrameParser()
            events = []

            async def until(kind):
                while not any(e["type"] == kind for e in events):
                    events.extend(parser.feed(await asyncio.wait_for(ws.recv(), 3)))

            await until("status_response")
            if rounds == 2:
                await ws.close()
                return
            await ws.send(
                encode_command(
                    {
                        "action": "send_sms",
                        "request_id": sms_id,
                        "to": "10000",
                        "content": "模拟发送",
                    }
                )
            )
            await until("sms_send_result")
            await ws.send(
                encode_command({"action": "call_live", "request_id": call_id, "to": "10000"})
            )
            await until("call_pcm")
            assert next(e for e in events if e["type"] == "call_pcm")["pcm"] == pcm
            await ws.send(encode_audio_input(call_id, 2, pcm))
            await asyncio.wait_for(pcm_received.wait(), 3)
            await ws.close()

        async with websockets.serve(api, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            config = {
                "server_url": f"ws://127.0.0.1:{port}/ws/device",
                "device_id": device_id,
                "token": "test-only",
                "serial_port": "simulated-vuart",
            }
            await asyncio.wait_for(session(config, USB), 10)
            assert [c["action"] for c in instances[0].sent] == [
                "send_sms",
                "call_live",
                "call_hangup",
            ]
            assert instances[0].audio == [encode_audio_input(call_id, 2, pcm)]
            assert not instances[0].thread.is_alive()
            await asyncio.wait_for(session(config, USB), 10)
            assert instances[1].sent == []
            assert not instances[1].thread.is_alive()

    asyncio.run(scenario())

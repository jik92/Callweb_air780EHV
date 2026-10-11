"""OpenAI Realtime audio bridge. No reconnect, bounded buffers, paced phone playback."""

import asyncio
import base64
import contextlib
import json
import struct
from collections import deque
from urllib.parse import urlencode

import numpy as np
import soxr
from websockets.asyncio.client import connect


class RealtimeAgent:
    def __init__(self, calls, call_id, owner, config):
        self.calls = calls
        self.call_id = call_id
        self.owner = owner
        self.config = config
        self.ws = None
        self.task = None
        self.stopped = False
        self.input = asyncio.Queue(maxsize=12)
        self.output = deque()
        self.pending = b""
        self.output_item = None
        self.interrupted_item = None
        self.content_index = 0
        self.played = {}
        self.sequence = 0
        self.rate = None
        self.up_resampler = self.down_resampler = None

    def current(self):
        return (
            not self.stopped
            and self.calls.agent is self
            and self.calls.state.get("call_id") == self.call_id
            and self.calls.state["state"] == "streaming"
        )

    async def prepare(self):
        try:
            async with asyncio.timeout(12):
                self.ws = await connect(
                    (
                        {
                            "aihubmix": "wss://aihubmix.com/v1/realtime?",
                            "openai": "wss://api.openai.com/v1/realtime?",
                        }[self.config["provider"]]
                    )
                    + urlencode({"model": self.config["model"]}),
                    additional_headers={"Authorization": "Bearer " + self.config["api_key"]},
                    open_timeout=10,
                    close_timeout=2,
                    max_queue=16,
                    max_size=1_000_000,
                    proxy=None,
                )
                await self.send(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "model": self.config["model"],
                            "instructions": self.config["instructions"],
                            "output_modalities": ["audio"],
                            "max_output_tokens": 1024,
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                    "turn_detection": {
                                        "type": "server_vad",
                                        "create_response": True,
                                        "interrupt_response": True,
                                        "silence_duration_ms": 600,
                                    },
                                },
                                "output": {
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                    "voice": self.config["voice"],
                                },
                            },
                        },
                    }
                )
                while True:
                    event = json.loads(await self.ws.recv())
                    if event.get("type") == "session.updated":
                        return
                    if event.get("type") == "error":
                        raise ValueError("Realtime 拒绝配置，请检查模型、声音及 API 权限")
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise ValueError("Realtime 连接失败，请检查 API Key、网络与模型权限") from None

    async def send(self, event):
        await self.ws.send(json.dumps(event, ensure_ascii=False))

    def start(self, rate):
        if self.task or self.stopped:
            return
        if rate not in (8000, 16000):
            raise ValueError("设备采样率不受支持")
        self.rate = rate
        self.up_resampler = soxr.ResampleStream(rate, 24000, 1, dtype="int16", quality="LQ")
        self.down_resampler = soxr.ResampleStream(24000, rate, 1, dtype="int16", quality="LQ")
        self.task = asyncio.create_task(self.run())

    def feed(self, pcm):
        if self.current():
            try:
                self.input.put_nowait(pcm)
            except asyncio.QueueFull:
                self.fail("agent_input_overflow")

    def status(self, status):
        if self.current() and self.calls.state.get("agent_status") != status:
            self.calls.state["agent_status"] = status
            self.calls.publish()

    def fail(self, reason):
        if self.calls.agent is self and self.calls.active():
            self.calls.state["agent_error"] = reason
            self.calls.hangup(reason)

    async def run(self):
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.receive())
                group.create_task(self.send_input())
                group.create_task(self.play())
                if self.config["greeting"].strip():
                    await self.send(
                        {
                            "type": "response.create",
                            "response": {
                                "instructions": "根据场景指令开始对话。第一句话说出："
                                + self.config["greeting"],
                            },
                        }
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            self.fail("agent_connection_failed")
        finally:
            if self.ws:
                with contextlib.suppress(Exception):
                    await self.ws.close()

    async def send_input(self):
        while self.current():
            pcm = await self.input.get()
            converted = self.up_resampler.resample_chunk(np.frombuffer(pcm, dtype="<i2"))
            if converted.size:
                await self.send(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(converted.astype("<i2").tobytes()).decode(),
                    }
                )

    async def receive(self):
        async for raw in self.ws:
            if not self.current():
                return
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "error":
                raise ValueError("Realtime session error")
            if kind == "input_audio_buffer.speech_started":
                await self.interrupt()
                self.status("listening")
            elif kind == "response.output_audio.delta":
                self.queue_audio(event)
            elif kind == "response.output_audio.done":
                tail = self.down_resampler.resample_chunk(np.empty(0, dtype="int16"), last=True)
                self.pending += tail.astype("<i2").tobytes()
                self.flush_output(final=True)
                self.down_resampler = soxr.ResampleStream(
                    24000, self.rate, 1, dtype="int16", quality="LQ"
                )
            elif kind == "response.done" and event.get("response", {}).get("status") == "failed":
                raise ValueError("Realtime response failed")
        raise ConnectionError("Realtime closed")

    def queue_audio(self, event):
        item = event.get("item_id")
        if item == self.interrupted_item:
            return
        if self.output_item != item:
            self.output_item = item
            self.content_index = event.get("content_index", 0)
            self.played.setdefault(item, 0)
        pcm = base64.b64decode(event["delta"], validate=True)
        converted = self.down_resampler.resample_chunk(np.frombuffer(pcm, dtype="<i2"))
        self.pending += converted.astype("<i2").tobytes()
        self.flush_output()

    def flush_output(self, final=False):
        size = self.rate // 50 * 2  # One 20 ms mono PCM16 frame.
        while len(self.pending) >= size or (final and self.pending):
            if len(self.output) >= 1500:  # At most 30 seconds, never unlimited model output.
                raise ValueError("Agent output buffer full")
            pcm, self.pending = self.pending[:size], self.pending[size:]
            self.output.append((self.output_item, pcm))

    async def interrupt(self):
        self.output.clear()
        self.pending = b""
        if hasattr(self.calls.service.bridge, "clear_audio"):
            self.calls.service.bridge.clear_audio()
        self.down_resampler = soxr.ResampleStream(24000, self.rate, 1, dtype="int16", quality="LQ")
        if self.output_item:
            self.interrupted_item = self.output_item
            await self.send(
                {
                    "type": "conversation.item.truncate",
                    "item_id": self.output_item,
                    "content_index": self.content_index,
                    "audio_end_ms": self.played.get(self.output_item, 0) * 1000 // (self.rate * 2),
                }
            )
        self.output_item = None

    async def play(self):
        silence = b"\x00" * (self.rate // 50 * 2)
        deadline = asyncio.get_running_loop().time()
        while self.current():
            if self.output:
                item, pcm = self.output.popleft()
                self.played[item] = self.played.get(item, 0) + len(pcm)
                pcm = pcm.ljust(len(silence), b"\x00")
                self.status("speaking")
            else:
                pcm = silence
                self.status("listening")
            self.calls.audio(struct.pack("<I", self.sequence) + pcm, self.owner)
            self.sequence += 1
            now = asyncio.get_running_loop().time()
            deadline = max(deadline + 0.02, now)
            await asyncio.sleep(max(0, deadline - now))

    def stop(self):
        self.stopped = True
        self.output.clear()
        self.pending = b""
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
        if self.ws and not self.task:
            asyncio.create_task(self.ws.close())

    async def close(self):
        self.stop()
        if self.task and self.task is not asyncio.current_task():
            await asyncio.gather(self.task, return_exceptions=True)
        if self.ws:
            await self.ws.close()

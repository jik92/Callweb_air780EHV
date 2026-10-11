"""One browser owns a live call; audio is bounded and never replayed after reconnect."""

import asyncio
import json
import queue
import struct
import time
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from .protocol import encode_audio_input
from .realtime_agent import RealtimeAgent
from .recording import Recording

TERMINAL = {"idle", "ended", "failed", "unknown"}


class Calls:
    def __init__(self, service):
        self.service = service
        self.clients = {}
        self.owner = None
        self.last_audio = 0
        self.last_sequence = -1
        self.last_down_sequence = -1
        self.state = {"type": "call_state", "state": "idle", "call_id": None}
        self.recording = None
        self.agent = None

    def publish(self):
        if self.state.get("call_id"):
            self.service.store.call_update(self.state)
        for messages in self.clients.values():
            self.put(messages, dict(self.state))

    @staticmethod
    def put(messages, item):
        if messages.full():
            messages.get_nowait()
        messages.put_nowait(item)

    def active(self):
        return self.state["state"] not in TERMINAL

    def command(self, raw, owner):
        if not isinstance(raw, dict):
            raise ValueError("命令必须为对象")
        request_id = str(UUID(raw.get("request_id", "")))
        action = raw.get("action")
        if action == "call_dial":
            number = raw.get("to", "")
            if not isinstance(number, str) or not number.removeprefix("+").isdigit():
                raise ValueError("请输入有效号码")
            if not 5 <= len(number.removeprefix("+")) <= 20:
                raise ValueError("号码长度应为 5–20 位")
            existing = self.service.store.call_get(request_id)
            if existing:
                if existing["phone"] != number:
                    raise ValueError("请求 ID 已用于其他号码")
                return {"type": "command_ack", "request_id": request_id, "duplicate": True}
            self.service.store.call_prepare(request_id, number)
            try:
                call = (self.service.device or {}).get("call", {})
                if not self.service.connection["ready"] or not call.get("live_supported"):
                    raise ValueError("设备尚未就绪或尚未部署实时通话固件")
                if not call.get("ready") or not call.get("audio_ready"):
                    raise ValueError("设备通话音频尚未就绪")
                if self.active() or call.get("busy"):
                    raise ValueError("设备已有通话，请先挂断")
                self.service.bridge.send(
                    {"action": "call_live", "request_id": request_id, "to": number}
                )
            except (ValueError, queue.Full) as exc:
                self.service.store.call_update(
                    {
                        "call_id": request_id,
                        "state": "failed",
                        "reason": str(exc) or "queue_full",
                        "ended_at": time.time(),
                    }
                )
                raise
            self.owner = owner
            self.last_sequence = -1
            self.last_down_sequence = -1
            self.last_audio = time.monotonic()
            self.state = {
                "type": "call_state",
                "state": "dialing",
                "call_id": request_id,
                "to": number,
                "started_at": time.time(),
                "sample_rate": None,
                "up_bytes": 0,
                "down_bytes": 0,
                "dropped_frames": 0,
                "device_up_bytes": 0,
            }
            self.publish()
        elif action in ("call_answer", "call_reject"):
            if self.state["state"] != "incoming" or raw.get("call_id") != self.state.get("call_id"):
                raise ValueError("来电已结束或已由其他页面处理")
            if not self.service.connection["ready"]:
                raise ValueError("设备尚未就绪")
            if action == "call_reject":
                self.hangup("user_rejected")
            else:
                call = (self.service.device or {}).get("call", {})
                if not call.get("audio_ready"):
                    raise ValueError("设备通话音频尚未就绪")
                self.service.bridge.send(
                    {"action": "call_answer", "request_id": self.state["call_id"]}
                )
                self.owner = owner
                self.last_audio = time.monotonic()
                self.state.update(state="answering", transition_at=time.time())
                self.publish()
        elif action == "call_hangup":
            if self.owner != owner or raw.get("call_id") != self.state.get("call_id"):
                raise ValueError("只能挂断当前页面发起的通话")
            self.hangup("user_hangup")
        elif action == "ping":
            return {"type": "pong", "request_id": request_id}
        else:
            raise ValueError("不支持的通话命令")
        return {"type": "command_ack", "request_id": request_id}

    async def answer_agent(self, raw, owner):
        if not isinstance(raw, dict):
            raise ValueError("命令必须为对象")
        request_id = str(UUID(raw.get("request_id", "")))
        call_id = raw.get("call_id")
        if self.state["state"] != "incoming" or call_id != self.state.get("call_id"):
            raise ValueError("来电已结束或已由其他页面处理")
        device_call = (self.service.device or {}).get("call", {})
        if not self.service.connection["ready"] or not device_call.get("audio_ready"):
            raise ValueError("设备通话音频尚未就绪")
        config = self.service.agent_settings.session_config()
        agent = RealtimeAgent(self, call_id, owner, config)
        self.agent = agent
        self.owner = owner
        self.state.update(
            state="agent_connecting",
            mode="agent",
            agent_status="connecting",
            transition_at=time.time(),
        )
        self.publish()
        try:
            await agent.prepare()
            if self.agent is not agent or self.state["state"] != "agent_connecting":
                raise ValueError("来电已结束，Agent未接听")
            self.service.bridge.send({"action": "call_answer", "request_id": call_id})
            self.state.update(state="answering", agent_status="ready", transition_at=time.time())
            self.publish()
        except BaseException:
            if self.agent is agent:
                self.stop_agent()
                if self.state["state"] == "agent_connecting":
                    self.owner = None
                    self.state.update(state="incoming", mode="browser", agent_status="error")
                    self.publish()
            await agent.close()
            raise
        return {"type": "command_ack", "request_id": request_id}

    def stop_agent(self):
        if self.agent:
            agent, self.agent = self.agent, None
            agent.stop()
            self.state["agent_status"] = "stopped"
            if hasattr(self.service.bridge, "clear_audio"):
                self.service.bridge.clear_audio()

    def hangup(self, reason):
        if not self.active():
            return
        self.stop_agent()
        try:
            self.service.bridge.send({"action": "call_hangup", "request_id": self.state["call_id"]})
            self.state.update(state="hanging_up", reason=reason, transition_at=time.time())
        except queue.Full:
            self.finish_recording()
            self.state.update(state="unknown", reason="hangup_queue_full", ended_at=time.time())
        self.publish()

    def audio(self, data, owner):
        if owner != self.owner or self.state["state"] != "streaming":
            return
        if len(data) < 6 or len(data) > 1284 or len(data) % 2:
            raise ValueError("无效音频帧")
        sequence = struct.unpack_from("<I", data)[0]
        if sequence <= self.last_sequence:
            return
        self.last_sequence = sequence
        self.last_audio = time.monotonic()
        pcm = data[4:]
        self.record("up", pcm)
        self.state["up_bytes"] += len(pcm)
        dropped = self.service.bridge.send_audio(
            encode_audio_input(self.state["call_id"], sequence, pcm)
        )
        self.state["dropped_frames"] += int(dropped)

    def record(self, channel, pcm):
        if self.recording:
            try:
                self.recording.write(channel, pcm)
            except (OSError, ValueError) as exc:
                self.state["recording_error"] = str(exc)
                self.finish_recording()

    def finish_recording(self):
        if self.recording:
            recording, self.recording = self.recording, None
            try:
                recording.close()
            except OSError as exc:
                self.state["recording_error"] = str(exc)

    def close(self):
        self.stop_agent()
        self.finish_recording()
        if self.active():
            self.state.update(state="unknown", reason="service_restarted", ended_at=time.time())
        self.publish()

    def event(self, event):
        kind = event.get("type")
        if kind == "transport" and not event.get("connected") and self.active():
            self.stop_agent()
            self.finish_recording()
            self.state.update(state="unknown", reason="serial_disconnected", ended_at=time.time())
            self.publish()
        if kind == "status_response":
            device_call = event.get("call", {})
            if device_call.get("state") == "incoming":
                self.incoming(device_call.get("request_id"), device_call.get("to", ""))
            if device_call.get("request_id") == self.state.get("call_id"):
                self.state["device_up_bytes"] = device_call.get("up_bytes", 0)
                self.state["device_dropped_bytes"] = device_call.get("dropped_bytes", 0)
                self.publish()
        if kind == "call_event" and event.get("state") == "INCOMINGCALL":
            self.incoming(event.get("request_id"), event.get("to", ""))
        if event.get("request_id") != self.state.get("call_id"):
            return
        if kind == "call_pcm" and self.state["state"] == "streaming":
            if event["sequence"] <= self.last_down_sequence:
                return
            self.last_down_sequence = event["sequence"]
            pcm = event["pcm"]
            self.record("down", pcm)
            self.state["down_bytes"] += len(pcm)
            if pcm:
                peak = max(abs(sample[0]) for sample in struct.iter_unpack("<h", pcm))
                self.state["down_peak"] = max(self.state.get("down_peak", 0), peak)
            if self.agent:
                self.agent.feed(pcm)
            messages = self.clients.get(self.owner)
            if messages is not None and not self.agent:
                self.put(
                    messages, struct.pack("<HI", event["sample_rate"], event["sequence"]) + pcm
                )
        elif kind == "call_event":
            if not self.active():
                return
            state = event.get("state")
            if state == "stream_started":
                self.state.update(state="streaming", sample_rate=event["sample_rate"])
                self.state.setdefault("connected_at", time.time())
                if not self.recording and not self.state.get("recording_error"):
                    try:
                        self.recording = Recording(
                            self.service.store.recordings / self.state["call_id"],
                            event["sample_rate"],
                        )
                    except (OSError, ValueError) as exc:
                        self.state["recording_error"] = str(exc)
                self.last_audio = time.monotonic()
                if self.agent:
                    self.agent.start(event["sample_rate"])
            elif state in ("CONNECTED", "SPEECH_START") and self.state["state"] in (
                "dialing",
                "answering",
            ):
                self.state.update(state="connected", connected_at=time.time())
            self.state["device_event"] = state
            self.publish()
        elif kind == "call_result":
            self.stop_agent()
            self.finish_recording()
            self.state.update(
                state="ended" if event.get("connected") else "failed",
                reason=self.state.get("reason") or event.get("reason"),
                ended_at=time.time(),
                device_up_bytes=event.get("up_bytes", 0),
                device_dropped_bytes=event.get("dropped_bytes", 0),
            )
            self.owner = None
            self.publish()

    def incoming(self, call_id, number):
        try:
            call_id = str(UUID(call_id))
        except (ValueError, TypeError, AttributeError):
            return
        if self.active() or self.service.store.call_get(call_id):
            return
        self.finish_recording()
        self.service.store.call_prepare(call_id, number)
        self.owner = None
        self.last_sequence = self.last_down_sequence = -1
        self.state = {
            "type": "call_state",
            "state": "incoming",
            "call_id": call_id,
            "to": number,
            "direction": "incoming",
            "started_at": time.time(),
            "up_bytes": 0,
            "down_bytes": 0,
            "dropped_frames": 0,
            "device_up_bytes": 0,
        }
        self.publish()

    def tick(self):
        if self.state["state"] == "streaming" and time.monotonic() - self.last_audio > 5:
            self.hangup("browser_audio_timeout")
        if (
            self.state["state"]
            in ("dialing", "agent_connecting", "answering", "connected", "hanging_up")
            and time.time()
            - self.state.get("transition_at", self.state.get("started_at", time.time()))
            > 100
        ):
            self.stop_agent()
            self.finish_recording()
            self.state.update(state="unknown", reason="call_result_timeout", ended_at=time.time())
            self.publish()
        if self.active():
            self.publish()

    async def socket(self, ws: WebSocket):
        await ws.accept()
        owner = object()
        messages = asyncio.Queue(maxsize=16)
        self.clients[owner] = messages
        messages.put_nowait(dict(self.state))

        async def send():
            while True:
                item = await messages.get()
                if isinstance(item, bytes):
                    await ws.send_bytes(item)
                else:
                    await ws.send_json(item)

        async def answer_and_reply(raw):
            try:
                response = await self.answer_agent(raw, owner)
            except (ValueError, TypeError, AttributeError, queue.Full) as exc:
                response = {
                    "type": "command_error",
                    "error": str(exc) or "设备命令队列已满",
                    "request_id": raw.get("request_id"),
                }
            self.put(messages, response)

        sender = asyncio.create_task(send())
        answering = None
        try:
            while True:
                item = await ws.receive()
                if item["type"] == "websocket.disconnect":
                    break
                if item.get("bytes") is not None:
                    if not self.agent:
                        self.audio(item["bytes"], owner)
                    continue
                raw = None
                try:
                    raw = json.loads(item.get("text", ""))
                    if isinstance(raw, dict) and raw.get("action") == "call_agent_answer":
                        if answering and not answering.done():
                            raise ValueError("Agent正在连接，请等待")
                        answering = asyncio.create_task(answer_and_reply(raw))
                        continue
                    response = self.command(raw, owner)
                except (ValueError, TypeError, AttributeError, queue.Full) as exc:
                    response = {
                        "type": "command_error",
                        "error": str(exc) or "设备命令队列已满",
                        "request_id": raw.get("request_id") if isinstance(raw, dict) else None,
                    }
                self.put(messages, response)
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            if answering and not answering.done():
                answering.cancel()
                await asyncio.gather(answering, return_exceptions=True)
            if self.owner == owner:
                self.hangup("browser_disconnected")
            self.clients.pop(owner, None)
            sender.cancel()
            try:
                await sender
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                pass

"""Persistent remote bindings and one bounded transport per authenticated device."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import math
import queue
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .protocol import FrameParser, encode_command


class DeviceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    server_url: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("请输入设备名称")
        return value.strip()

    @field_validator("server_url")
    @classmethod
    def valid_url(cls, value):
        from urllib.parse import urlsplit

        if not value:
            return value
        parts = urlsplit(value)
        if (
            parts.scheme not in ("ws", "wss")
            or not parts.hostname
            or parts.path != "/ws/device"
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
        ):
            raise ValueError("请填写 ws://或wss://域名/ws/device")
        return value


class DeviceRegistry:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL,
            created_at REAL NOT NULL, last_seen REAL)""")
        self.conn.commit()
        path.chmod(0o600)

    def list(self):
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT id,name,created_at,last_seen FROM devices ORDER BY created_at"
            )
        ]

    def bind(self, name):
        if len(self.list()) >= 100:
            raise ValueError("最多绑定 100 台远程设备")
        device_id, token = str(uuid4()), secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO devices VALUES (?,?,?,?,NULL)",
            (device_id, name, hashlib.sha256(token.encode()).hexdigest(), time.time()),
        )
        self.conn.commit()
        return device_id, token

    def authenticate(self, device_id, token):
        if not isinstance(device_id, str) or not isinstance(token, str) or len(token) > 200:
            return False
        row = self.conn.execute(
            "SELECT token_hash FROM devices WHERE id=?", (device_id,)
        ).fetchone()
        return bool(
            row
            and hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        )

    def touch(self, device_id):
        self.conn.execute("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), device_id))
        self.conn.commit()

    def close(self):
        self.conn.close()


class RemoteBridge:
    def __init__(self, ws):
        self.ws = ws
        self.commands = asyncio.Queue(maxsize=32)
        self.audio = asyncio.Queue(maxsize=6)
        self.connected = True

    def send(self, command):
        if not self.connected:
            raise queue.Full
        try:
            self.commands.put_nowait(command)
        except asyncio.QueueFull:
            raise queue.Full from None

    def send_audio(self, frame):
        dropped = self.audio.full()
        if dropped:
            self.audio.get_nowait()
        if self.connected:
            self.audio.put_nowait(frame)
        return dropped or not self.connected

    def clear_audio(self):
        while not self.audio.empty():
            self.audio.get_nowait()

    async def writer(self, service):
        next_status, next_inbox = 0, 0
        while self.connected:
            now = time.monotonic()
            if now >= next_status:
                self.send({"action": "get_status"})
                next_status = now + 10
            if now >= next_inbox:
                self.send({"action": "get_inbox"})
                next_inbox = now + 60
            if not self.commands.empty():
                command = self.commands.get_nowait()
                await asyncio.wait_for(self.ws.send_bytes(encode_command(command)), 3)
                service.events.put_nowait(
                    {"type": "command_written", "request_id": command.get("request_id")}
                )
            elif not self.audio.empty():
                await asyncio.wait_for(self.ws.send_bytes(self.audio.get_nowait()), 3)
            else:
                await asyncio.sleep(0.01)


def valid_event(event):
    kind = event.get("type")
    if kind == "status_response":
        mobile, call = event.get("mobile"), event.get("call", {})
        if not isinstance(mobile, dict) or not isinstance(call, dict):
            return False
        if not all(isinstance(event.get(key, ""), str) for key in ("project", "version")):
            return False
        if not all(isinstance(mobile.get(key, ""), str) for key in ("number", "iccid", "imsi")):
            return False
        if not all(isinstance(mobile.get(key, False), bool) for key in ("sim_ready", "sms_ready")):
            return False
        if not all(
            type(mobile.get(key, 0)) is int
            for key in ("sim_slot", "net_status", "csq", "rssi", "rsrp", "rsrq", "snr")
        ):
            return False
        if not all(
            type(call.get(key, False)) is bool
            for key in ("ready", "audio_ready", "live_supported", "busy")
        ):
            return False
        if not all(type(call.get(key, 0)) is int for key in ("up_bytes", "dropped_bytes")):
            return False
        return all(
            call.get(key) is None or isinstance(call[key], str)
            for key in ("state", "request_id", "to")
        )

    if kind == "incoming_sms":
        return (
            isinstance(event.get("from"), str)
            and isinstance(event.get("content"), str)
            and len(event["content"].encode()) <= 4096
            and type(event.get("received_at")) in (int, float)
            and math.isfinite(event["received_at"])
            and 0 < event["received_at"] < 1e12
        )
    if kind == "inbox_response":
        messages = event.get("messages")
        return (
            isinstance(messages, list)
            and len(messages) <= 20
            and all(
                isinstance(item, dict) and valid_event({**item, "type": "incoming_sms"})
                for item in messages
            )
        )
    if kind in (
        "call_accepted",
        "call_event",
        "call_result",
        "call_pcm",
        "sms_send_result",
        "sms_send_accepted",
    ):
        try:
            UUID(event.get("request_id", ""))
        except (ValueError, TypeError, AttributeError):
            return False
        if not all(
            event.get(key) is None or isinstance(event[key], str)
            for key in ("state", "to", "reason", "error")
        ):
            return False
        if kind == "call_pcm":
            return (
                isinstance(event.get("pcm"), bytes)
                and len(event["pcm"]) <= 4096
                and len(event["pcm"]) % 2 == 0
                and event.get("sample_rate") in (8000, 16000)
                and type(event.get("sequence")) is int
                and 0 <= event["sequence"] <= 0xFFFFFFFF
            )
        if kind == "call_event" and event.get("state") == "stream_started":
            return event.get("sample_rate") in (8000, 16000)
        if kind == "call_result":
            return type(event.get("connected", False)) is bool
        if kind == "sms_send_result":
            return type(event.get("success")) is bool
        return True
    return False


async def device_socket(ws: WebSocket, manager):
    await ws.accept()
    service, bridge, writer, receiver = None, None, None, None
    try:
        raw = await asyncio.wait_for(ws.receive_text(), 10)
        if len(raw) > 1024:
            await ws.close(code=1008)
            return
        login = json.loads(raw)
        if (
            not isinstance(login, dict)
            or login.get("type") != "device_login"
            or not manager.registry.authenticate(login.get("device_id"), login.get("token"))
        ):
            await ws.close(code=1008)
            return
        service = manager.remote_service(login["device_id"])
        if isinstance(service.bridge, RemoteBridge) and service.bridge.connected:
            await ws.close(code=1008, reason="device_already_online")
            service = None
            return
        bridge = RemoteBridge(ws)
        service.bridge = bridge
        port = login.get("serial_port")
        port = f"设备 Agent · {port}" if isinstance(port, str) and len(port) <= 200 else "WebSocket"
        # Apply transport transition before the reader can enqueue any device data.
        service.events.put_nowait({"type": "transport", "connected": True, "port": port})
        await ws.send_json({"type": "device_login_ok", "device_id": login["device_id"]})
        manager.registry.touch(login["device_id"])
        writer = asyncio.create_task(bridge.writer(service))
        parser = FrameParser()
        while True:
            if writer.done():
                await writer
            receiver = asyncio.create_task(ws.receive())
            done, _ = await asyncio.wait(
                [receiver, writer], timeout=35, return_when=asyncio.FIRST_COMPLETED
            )
            if writer in done:
                await writer
                break
            if receiver not in done:
                break
            packet = await receiver
            receiver = None
            if packet["type"] == "websocket.disconnect":
                break
            data = packet.get("bytes")
            if not isinstance(data, bytes) or len(data) > 65536:
                await ws.close(code=1008)
                break
            for event in parser.feed(data):
                if service.events.qsize() >= 256:
                    await ws.close(code=1008)
                    return
                if not valid_event(event):
                    await ws.close(code=1008)
                    return
                if event["type"] == "status_response":
                    manager.registry.touch(login["device_id"])
                if event["type"] == "call_pcm" and service.events.qsize() >= 128:
                    continue
                service.events.put_nowait(event)
            if len(parser.buffer) > 65536:
                await ws.close(code=1008)
                break
    except (
        WebSocketDisconnect,
        TimeoutError,
        ValueError,
        TypeError,
        queue.Full,
        asyncio.QueueFull,
        RuntimeError,
    ):
        pass
    finally:
        if receiver:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receiver
        if bridge:
            bridge.connected = False
            bridge.clear_audio()
            if writer:
                writer.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError,
                    RuntimeError,
                    TimeoutError,
                    queue.Full,
                    asyncio.QueueFull,
                ):
                    await writer
            if service.events.full():
                service.events.get_nowait()
            service.events.put_nowait(
                {"type": "transport", "connected": False, "error": "device_disconnected"}
            )
        with contextlib.suppress(RuntimeError):
            await ws.close()

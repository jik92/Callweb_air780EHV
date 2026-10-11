import asyncio
import contextlib
import fcntl
import json
import os
import queue
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .calls import Calls
from .recording import mix
from .serial_bridge import SerialBridge, available_ports
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("AIR780_DATA_DIR", str(ROOT.parent / "data")))
FRONTEND = ROOT.parent / "frontend" / "dist"
ALLOWED_ORIGINS = {
    "http://localhost:9011",
    "http://127.0.0.1:9011",
    "http://localhost:9010",
    "http://127.0.0.1:9010",
}


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get_status", "get_inbox", "send_sms", "ping"]
    request_id: UUID
    to: str | None = Field(default=None, pattern=r"^\+?\d{5,20}$")
    content: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        if value is not None:
            if not value.strip() or len(value.encode()) > 512:
                raise ValueError("短信内容不能为空且不能超过 512 UTF-8 字节")
            if "CMD_START:" in value or ":CMD_END" in value or ":SMS_END" in value:
                raise ValueError("短信内容包含设备协议分隔符")
        return value


class Service:
    def __init__(self, store: Store):
        self.store = store
        self.device = None
        self.connection = {"connected": False, "ready": False, "port": None, "error": None}
        self.clients: set[asyncio.Queue] = set()
        self.events = asyncio.Queue()
        self.bridge = None
        self.last_seen = None
        self.calls = Calls(self)

    def snapshot(self):
        return {
            "type": "snapshot",
            "bridge": {**self.connection, "last_seen": self.last_seen},
            "device": self.device,
            "messages": self.store.list(),
        }

    def broadcast(self):
        snapshot = self.snapshot()
        for client in self.clients:
            if client.full():
                client.get_nowait()
            client.put_nowait(snapshot)

    async def consume(self):
        tick_at = time.monotonic()
        while True:
            if time.monotonic() - tick_at >= 1:
                self.calls.tick()
                tick_at = time.monotonic()
            try:
                event = await asyncio.wait_for(self.events.get(), timeout=5)
            except TimeoutError:
                self.calls.tick()
                self.store.expire()
                self.broadcast()
                continue
            self.calls.event(event)
            kind = event.get("type")
            if kind == "call_pcm":
                continue
            sim_id = (self.device or {}).get("mobile", {}).get("iccid", "")
            if kind == "transport":
                self.connection.update(
                    {
                        "connected": event["connected"],
                        "ready": False,
                        "port": event.get("port"),
                        "error": event.get("error"),
                    }
                )
                self.device = None
                if not event["connected"]:
                    self.store.mark_unknown("serial_disconnected")
            elif kind == "status_response":
                self.device = event
                self.last_seen = time.time()
                self.connection.update(ready=True, error=None)
            elif kind == "inbox_response":
                for message in event.get("messages", []) or []:
                    if isinstance(message, dict):
                        self.store.incoming(message, sim_id)
            elif kind == "incoming_sms":
                self.store.incoming(event, sim_id)
            elif kind in ("command_written", "sms_send_accepted"):
                if event.get("request_id"):
                    row = self.store.get(event["request_id"])
                    if row and row["state"] == "queued":
                        self.store.update(event["request_id"], "sending")
            elif kind == "sms_send_result":
                self.store.update(
                    event["request_id"],
                    "submitted" if event.get("success") else "failed",
                    event.get("error"),
                )
            self.broadcast()

    def command(self, raw: dict) -> dict:
        command = Command.model_validate(raw)
        request_id = str(command.request_id)
        if command.action == "ping":
            return {"type": "pong", "request_id": request_id}
        if command.action == "send_sms":
            existing = self.store.get(request_id)
            if existing:
                if existing["phone"] != command.to or existing["content"] != command.content:
                    raise ValueError("相同请求 ID 不能用于不同短信")
                return {"type": "command_ack", "request_id": request_id, "duplicate": True}
            if not command.to or command.content is None:
                raise ValueError("请填写收件号码和短信内容")
        if not self.connection["ready"]:
            raise ValueError("设备尚未连接，请检查 USB 和串口占用")
        if command.action == "send_sms":
            if not self.device["mobile"].get("sms_ready"):
                raise ValueError("设备短信功能尚未就绪")
            if any(row["state"] in ("queued", "sending") for row in self.store.list()):
                raise ValueError("已有短信正在发送，请等待结果")
            self.store.prepare(
                request_id, command.to, command.content, self.device["mobile"].get("iccid", "")
            )
        payload = command.model_dump(mode="json", exclude_none=True)
        try:
            self.bridge.send(payload)
        except queue.Full as exc:
            if command.action == "send_sms":
                self.store.update(request_id, "failed", "queue_full")
            raise ValueError("设备命令队列已满") from exc
        self.broadcast()
        return {"type": "command_ack", "request_id": request_id, "duplicate": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(DATA.mkdir, parents=True, exist_ok=True)
    lock = (DATA / "serial.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("已有桥接服务运行；串口服务只允许一个 worker") from None
    store = Store(DATA / "console.sqlite3")
    service = Service(store)
    app.state.service = service
    loop = asyncio.get_running_loop()
    service.bridge = SerialBridge(
        os.getenv("AIR780_SERIAL_PORT"),
        lambda event: loop.call_soon_threadsafe(service.events.put_nowait, event),
    )
    consumer = asyncio.create_task(service.consume())
    service.bridge.start()
    try:
        yield
    finally:
        await asyncio.to_thread(service.bridge.close)
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
        service.calls.close()
        store.close()
        lock.close()


app = FastAPI(title="Air780EHV 本地控制台", lifespan=lifespan)


@app.get("/api/state")
async def state():
    return app.state.service.snapshot()


@app.get("/api/call")
async def call_state():
    return app.state.service.calls.state


@app.get("/api/calls")
async def call_history():
    return app.state.service.store.call_list()


recording_lock = asyncio.Lock()


@app.get("/api/calls/{call_id}/recording")
async def call_recording(call_id: UUID):
    store = app.state.service.store
    row = store.call_get(str(call_id))
    if not row or row["state"] not in ("ended", "failed", "unknown"):
        raise HTTPException(404, "录音尚不可用")
    async with recording_lock:
        try:
            path = await asyncio.to_thread(mix, store.recordings / str(call_id))
        except (OSError, ValueError, wave.Error, EOFError) as exc:
            raise HTTPException(404, "录音文件不可用") from exc
    if not path:
        raise HTTPException(404, "这次拨号没有录音")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"call-{call_id}.wav",
        content_disposition_type="inline",
    )


@app.get("/api/ports")
async def ports():
    return await asyncio.to_thread(available_ports)


@app.get("/api/health")
async def health():
    return {"ok": True, "device_ready": app.state.service.connection["ready"]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if ws.headers.get("origin") not in ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    service = app.state.service
    events = asyncio.Queue(maxsize=8)
    service.clients.add(events)
    events.put_nowait(service.snapshot())

    async def sender():
        while True:
            await ws.send_json(await events.get())

    task = asyncio.create_task(sender())
    try:
        while True:
            text = await ws.receive_text()
            raw = None
            try:
                raw = json.loads(text)
                response = service.command(raw)
            except (ValidationError, ValueError, TypeError) as exc:
                response = {
                    "type": "command_error",
                    "request_id": raw.get("request_id") if isinstance(raw, dict) else None,
                    "error": str(exc),
                }
            await events.put(response)
    except WebSocketDisconnect:
        pass
    finally:
        service.clients.discard(events)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await task


@app.websocket("/ws/call")
async def call_socket(ws: WebSocket):
    if ws.headers.get("origin") not in ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await app.state.service.calls.socket(ws)


if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND / "index.html")

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

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .agent_settings import AgentSettings, AgentUpdate
from .calls import Calls
from .devices import DeviceBinding, DeviceRegistry, device_socket
from .dingtalk import DingTalkNotifier, DingTalkSettings, DingTalkUpdate, send_notification
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

ALLOWED_ORIGINS.update(filter(None, os.getenv("AIR780_ALLOWED_ORIGINS", "").split(",")))


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "get_status", "get_inbox", "send_sms", "ping", "set_sim_number", "clear_sim_number"
    ]
    request_id: UUID
    to: str | None = Field(default=None, pattern=r"^\+?\d{5,20}$")
    content: str | None = None
    sim_id: str | None = None

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
    def __init__(self, store: Store, device_id="local"):
        self.store = store
        self.device_id = device_id
        self.device = None
        self.connection = {"connected": False, "ready": False, "port": None, "error": None}
        self.clients: set[asyncio.Queue] = set()
        self.events = asyncio.Queue()
        self.bridge = None
        self.last_seen = None
        self.agent_settings = AgentSettings(store.recordings.parent / "agent")
        self.calls = Calls(self)
        self.dingtalk_settings = DingTalkSettings(store.recordings.parent / "dingtalk")
        self.dingtalk = DingTalkNotifier(store, self.dingtalk_settings)

    def snapshot(self):
        device = self.device
        if device:
            mobile = dict(device.get("mobile", {}))
            saved = self.store.sim_number(mobile.get("iccid", ""))
            mobile["number_source"] = "sim" if mobile.get("number") else "unavailable"
            mobile["manual_number"] = saved["phone"] if saved else ""
            if not mobile.get("number") and saved:
                mobile.update(
                    number=saved["phone"],
                    number_source="manual",
                    number_confirmed_at=saved["confirmed_at"],
                )
            device = {**device, "mobile": mobile}
        return {
            "type": "snapshot",
            "device_id": self.device_id,
            "bridge": {**self.connection, "last_seen": self.last_seen},
            "device": device,
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
                self.dingtalk.enqueue(self.store.incoming(event, sim_id))
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
        if command.action in ("set_sim_number", "clear_sim_number"):
            mobile = (self.device or {}).get("mobile", {})
            sim_id = mobile.get("iccid")
            if not mobile.get("sim_ready") or not sim_id:
                raise ValueError("SIM 卡未就绪或无法读取 ICCID")
            if command.sim_id != sim_id:
                raise ValueError("SIM 卡已变化，请关闭窗口后重新设置")
            if command.action == "set_sim_number":
                if not command.to:
                    raise ValueError("请输入已确认的本机号码")
                self.store.set_sim_number(sim_id, command.to)
            else:
                self.store.clear_sim_number(sim_id)
            self.broadcast()
            return {"type": "command_ack", "request_id": request_id}
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


class DeviceManager:
    def __init__(self, local, directory):
        self.local = local
        self.directory = directory
        self.registry = DeviceRegistry(directory / "devices.sqlite3")
        self.services = {"local": local}
        self.tasks = []

    def remote_service(self, device_id):
        if device_id not in self.services:
            UUID(device_id)  # Keep per-device paths inside the managed directory.
            directory = self.directory / "devices" / device_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            service = Service(Store(directory / "console.sqlite3"), device_id)
            self.services[device_id] = service
            self.tasks.extend(
                [
                    asyncio.create_task(service.consume()),
                    asyncio.create_task(service.dingtalk.run()),
                ]
            )
        return self.services[device_id]

    def get(self, device_id):
        if device_id == "local":
            return self.local
        if not any(row["id"] == device_id for row in self.registry.list()):
            raise HTTPException(404, "设备尚未绑定")
        return self.remote_service(device_id)

    def list(self):
        result = []
        rows = self.registry.list()
        if os.getenv("AIR780_ENABLE_LOCAL_SERIAL", "1") != "0":
            rows.insert(
                0, {"id": "local", "name": "本机 USB 设备", "last_seen": self.local.last_seen}
            )
        for row in rows:
            service = self.services.get(row["id"])
            connected = bool(service and service.connection["connected"])
            ready = bool(service and service.connection["ready"])
            result.append(
                {
                    **row,
                    "online": connected,
                    "ready": ready,
                    "transport": "usb" if row["id"] == "local" else "websocket",
                    "version": (service.device or {}).get("version") if service else None,
                    "last_seen": service.last_seen
                    if service and service.last_seen
                    else row["last_seen"],
                    "call_active": bool(service and service.calls.active()),
                }
            )
        return sorted(result, key=lambda row: not row["online"])

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for device_id, service in self.services.items():
            if device_id == "local":
                continue
            agent = service.calls.agent
            service.calls.close()
            if agent:
                await agent.close()
            service.store.close()
        self.registry.close()


def request_service(request):
    device_id = request.query_params.get("device_id", "local")
    return app.state.devices.get(device_id)


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
    app.state.devices = DeviceManager(service, DATA)
    loop = asyncio.get_running_loop()
    service.bridge = SerialBridge(
        os.getenv("AIR780_SERIAL_PORT"),
        lambda event: loop.call_soon_threadsafe(service.events.put_nowait, event),
    )
    consumer = asyncio.create_task(service.consume())
    notifier = asyncio.create_task(service.dingtalk.run())
    local_enabled = os.getenv("AIR780_ENABLE_LOCAL_SERIAL", "1") != "0"
    if local_enabled:
        service.bridge.start()
    try:
        yield
    finally:
        if local_enabled:
            await asyncio.to_thread(service.bridge.close)
        notifier.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await notifier
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
        agent = service.calls.agent
        service.calls.close()
        if agent:
            await agent.close()
        await app.state.devices.close()
        store.close()
        lock.close()


app = FastAPI(title="Air780EHV 本地控制台", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Never return submitted API keys in validation errors.
    return JSONResponse(
        status_code=422,
        content={"detail": [{"loc": error["loc"], "msg": error["msg"]} for error in exc.errors()]},
    )


@app.get("/api/devices")
async def devices():
    return app.state.devices.list()


@app.post("/api/devices")
async def bind_device(request: Request, binding: DeviceBinding):
    if request.headers.get("origin") not in ALLOWED_ORIGINS:
        raise HTTPException(403, "不允许的请求来源")
    try:
        device_id, token = app.state.devices.registry.bind(binding.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"device_id": device_id, "token": token, "server_url": binding.server_url}


@app.websocket("/ws/device")
async def remote_device_socket(ws: WebSocket):
    await device_socket(ws, app.state.devices)


@app.get("/api/state")
async def state(request: Request):
    return request_service(request).snapshot()


@app.get("/api/agent")
async def agent_settings(request: Request):
    return request_service(request).agent_settings.snapshot()


@app.put("/api/agent")
async def save_agent_settings(request: Request, update: AgentUpdate):
    if request.headers.get("origin") not in ALLOWED_ORIGINS:
        raise HTTPException(403, "不允许的请求来源")
    try:
        return request_service(request).agent_settings.save(update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/agent/test")
async def test_agent_settings(request: Request):
    if request.headers.get("origin") not in ALLOWED_ORIGINS:
        raise HTTPException(403, "不允许的请求来源")
    from .realtime_agent import RealtimeAgent

    service = request_service(request)
    try:
        agent = RealtimeAgent(service.calls, None, None, service.agent_settings.session_config())
        await agent.prepare()
        await agent.close()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "message": "Realtime 连接和场景配置验证成功，未接听或拨打电话"}


@app.get("/api/dingtalk")
async def dingtalk_settings(request: Request):
    service = request_service(request)
    return {**service.dingtalk_settings.snapshot(), "deliveries": service.store.push_list()}


@app.put("/api/dingtalk")
async def save_dingtalk_settings(request: Request, update: DingTalkUpdate):
    if request.headers.get("origin") not in ALLOWED_ORIGINS:
        raise HTTPException(403, "不允许的请求来源")
    service = request_service(request)
    try:
        data = service.dingtalk_settings.save(update)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {**data, "deliveries": service.store.push_list()}


@app.post("/api/dingtalk/test")
async def test_dingtalk_settings(request: Request):
    if request.headers.get("origin") not in ALLOWED_ORIGINS:
        raise HTTPException(403, "不允许的请求来源")
    service = request_service(request)
    if getattr(service, "dingtalk_test_at", 0) > time.monotonic() - 10:
        raise HTTPException(429, "请等待 10 秒后再测试")
    try:
        config = service.dingtalk_settings.config(test=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    service.dingtalk_test_at = time.monotonic()
    state, error = await send_notification(
        config, "智能语音客服 · 短信钉钉推送测试\nWebhook 与加签连接测试；此消息不包含真实短信。"
    )
    if state != "sent":
        raise HTTPException(502, error)
    return {"ok": True, "message": "钉钉已接受测试推送，请在群内查看"}


@app.get("/api/call")
async def call_state(request: Request):
    return request_service(request).calls.state


@app.get("/api/calls")
async def call_history(request: Request):
    return request_service(request).store.call_list()


recording_lock = asyncio.Lock()


@app.get("/api/calls/{call_id}/recording")
async def call_recording(call_id: UUID, request: Request):
    store = request_service(request).store
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


@app.get("/api/device-gateway/health")
async def device_gateway_health():
    return {"ok": True, "transport": "websocket"}


@app.get("/api/health")
async def health():
    return {"ok": True, "device_ready": app.state.service.connection["ready"]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if ws.headers.get("origin") not in ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    try:
        service = request_service(ws)
    except HTTPException:
        await ws.close(code=1008)
        return
    await ws.accept()
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
    try:
        service = request_service(ws)
    except HTTPException:
        await ws.close(code=1008)
        return
    await service.calls.socket(ws)


if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND / "index.html")

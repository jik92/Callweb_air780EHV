"""Explicit USB network changes; reconnects never replay the command."""

import asyncio
import queue
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool


class UsbNetworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    mode: Literal["ecm", "rndis"] = "ecm"


class UsbNetwork:
    def __init__(self, service):
        self.service = service
        self.waiter = None
        self.state = {"state": "idle", "enabled": False, "mode": "ecm"}

    def consume(self, event):
        kind = event.get("type")
        if kind == "status_response" and isinstance(event.get("usb_network"), dict):
            data = event["usb_network"]
            self.state = {
                "state": "switching" if data.get("switching") else "configured",
                "enabled": data.get("enabled") is True,
                "mode": data.get("mode", "ecm"),
            }
        if not self.waiter:
            return
        request_id, future = self.waiter
        if future.done():
            return
        if kind == "usb_network_result" and event.get("request_id") == request_id:
            future.set_result(event)
        elif kind == "transport" and not event.get("connected"):
            future.set_exception(ConnectionError())

    async def configure(self, request):
        service = self.service
        if not service.connection["ready"]:
            raise HTTPException(503, "设备未连接；USB 切换后请等待重连，无法恢复时重新上电")
        if not (service.device or {}).get("usb_network_supported"):
            raise HTTPException(409, "请更新设备 Lua 脚本以支持 USB 网卡")
        if service.calls.active() or (service.device or {}).get("call", {}).get("busy"):
            raise HTTPException(409, "请结束通话后切换 USB 网卡")
        if any(row["state"] in ("queued", "sending") for row in service.store.list()):
            raise HTTPException(409, "请等待短信发送完成后切换 USB 网卡")
        if (
            self.waiter
            or self.state["state"] == "switching"
            or service.network_waiter
            or service.location.waiter
        ):
            raise HTTPException(409, "请等待当前设备网络操作完成")
        if self.state["enabled"] and request.enabled and self.state["mode"] != request.mode:
            raise HTTPException(409, "请先关闭网卡，再切换模式")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.waiter = (request_id, future)
        self.state = {**self.state, "state": "switching"}
        service.broadcast()
        try:
            service.bridge.send(
                {"action": "usb_network", "request_id": request_id, **request.model_dump()}
            )
            result = await asyncio.wait_for(future, 10)
            self.state = {
                "state": "configured" if result.get("success") else "failed",
                "enabled": result.get("enabled") is True,
                "mode": result.get("mode", request.mode),
                "error": result.get("error"),
            }
        except (TimeoutError, ConnectionError):
            self.state = {
                **self.state,
                "state": "unknown",
                "error": "设备未确认结果；未自动重试，请等待 USB 重连",
            }
        except queue.Full:
            self.state = {**self.state, "state": "failed", "error": "设备命令队列已满"}
        finally:
            self.waiter = None
            service.broadcast()
        return self.state

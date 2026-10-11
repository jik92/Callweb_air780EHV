"""Explicit, bounded network probes executed by the USB device, never by the API host."""

import asyncio
import queue
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


class NetworkTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=1024)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        value = value.strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
            or any(marker in value for marker in (":CMD_END", "CMD_START:", ":SMS_END"))
        ):
            raise ValueError("请输入完整的 HTTP/HTTPS 地址，不含账号、密码或片段")
        try:
            _ = parsed.port
        except ValueError:
            raise ValueError("URL 端口无效") from None
        return value


async def probe(service, request):
    if not service.connection["ready"]:
        raise HTTPException(503, "设备未连接")
    if not (service.device or {}).get("network_test_supported"):
        raise HTTPException(409, "请更新设备 Lua 脚本后使用网络测试")
    if service.calls.active() or (service.device or {}).get("call", {}).get("busy"):
        raise HTTPException(409, "请结束通话后再测试网络")
    if service.network_waiter is not None:
        raise HTTPException(409, "已有网络测试进行中")
    if service.location.waiter is not None:
        raise HTTPException(409, "请等待基站定位完成")
    if service.usb_network.waiter or service.usb_network.state["state"] == "switching":
        raise HTTPException(409, "请等待 USB 网卡切换完成")
    request_id = str(uuid4())
    future = asyncio.get_running_loop().create_future()
    service.network_waiter = (request_id, future)
    try:
        service.bridge.send(
            {"action": "network_test", "request_id": request_id, "url": request.url}
        )
        result = await asyncio.wait_for(future, 25)
        return {
            **result,
            "url": request.url,
            "method": result.get("method", "HEAD"),
            "source": "device_4g",
        }
    except queue.Full:
        raise HTTPException(503, "设备命令队列已满") from None
    except TimeoutError:
        raise HTTPException(504, "设备未在 25 秒内返回测试结果；未自动重试") from None
    except ConnectionError:
        raise HTTPException(503, "测试期间 USB 连接中断；未自动重试") from None
    finally:
        service.network_waiter = None

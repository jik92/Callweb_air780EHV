"""Device LBS coordinates with cached, low-frequency reverse geocoding."""

import asyncio
import math
import os
import queue
import time
from uuid import uuid4

import httpx
from fastapi import HTTPException


class Location:
    def __init__(self, service):
        self.service = service
        self.state = {"state": "idle"}
        self.waiter = None
        self.cache = {}
        self.last_geocode = 0
        self.geocode_lock = asyncio.Lock()
        self.generation = 0

    def consume(self, event):
        if event.get("type") == "transport":
            self.generation += 1
            self.state = {"state": "idle"}
            if self.waiter and not self.waiter[1].done():
                self.waiter[1].set_exception(ConnectionError())
        elif self.waiter and event.get("type") == "location_result":
            request_id, future = self.waiter
            if event.get("request_id") == request_id and not future.done():
                future.set_result(event)

    async def address(self, latitude, longitude):
        key = (round(latitude, 5), round(longitude, 5))
        async with self.geocode_lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 86400:
                return cached[1]
            delay = 1.1 - (time.monotonic() - self.last_geocode)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_geocode = time.monotonic()
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    os.getenv("AIR780_GEOCODER_URL", "https://nominatim.openstreetmap.org/reverse"),
                    params={
                        "lat": latitude,
                        "lon": longitude,
                        "format": "jsonv2",
                        "accept-language": "zh-CN,zh,en",
                        "zoom": 14,
                        "addressdetails": 1,
                    },
                    headers={
                        "User-Agent": "Air780Console/1.0 (https://github.com/jik92/Callweb_air780EHV)"
                    },
                )
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict) or not data.get("display_name"):
                raise ValueError("address unavailable")
            parts = data.get("address") or {}
            values = [
                parts.get("state"),
                parts.get("state_district"),
                parts.get("city") or parts.get("municipality"),
                parts.get("district") or parts.get("county"),
                parts.get("town") or parts.get("suburb") or parts.get("village"),
            ]
            address = " · ".join(dict.fromkeys(str(v) for v in values if v))
            address = (address or str(data["display_name"]))[:500]
            if len(self.cache) >= 64:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), address)
            return address

    async def refresh(self):
        service = self.service
        if not service.connection["ready"]:
            raise HTTPException(503, "设备未连接")
        if not (service.device or {}).get("location_supported"):
            raise HTTPException(409, "请更新设备 Lua 脚本以支持基站定位")
        if service.calls.active() or (service.device or {}).get("call", {}).get("busy"):
            raise HTTPException(409, "请结束通话后更新位置")
        if self.waiter or service.network_waiter:
            raise HTTPException(409, "请等待当前设备网络请求完成")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.waiter = (request_id, future)
        generation = self.generation
        self.state = {"state": "locating"}
        service.broadcast()
        try:
            service.bridge.send({"action": "locate", "request_id": request_id})
            result = await asyncio.wait_for(future, 25)
            if not result.get("success"):
                self.state = {"state": "failed", "error": result.get("error", "基站定位失败")}
            else:
                lat, lon = float(result["latitude"]), float(result["longitude"])
                if not (
                    math.isfinite(lat)
                    and math.isfinite(lon)
                    and -90 <= lat <= 90
                    and -180 <= lon <= 180
                ):
                    raise ValueError("invalid coordinates")
                self.state = {
                    "state": "resolving",
                    "latitude": lat,
                    "longitude": lon,
                    "coordinate_system": "WGS84",
                    "located_at": time.time(),
                }
                service.broadcast()
                try:
                    address = await self.address(lat, lon)
                    if generation != self.generation or not service.connection["ready"]:
                        raise ConnectionError()
                    self.state.update(state="ready", address=address)
                except (httpx.HTTPError, ValueError, TypeError):
                    self.state.update(state="partial", error="已获取坐标，地址解析暂不可用")
        except queue.Full:
            self.state = {"state": "failed", "error": "设备命令队列已满"}
        except TimeoutError:
            self.state = {"state": "failed", "error": "设备定位超时；未自动重试"}
        except ConnectionError:
            self.state = {"state": "failed", "error": "定位期间 USB 连接中断"}
        except (ValueError, KeyError, TypeError):
            self.state = {"state": "failed", "error": "设备返回的定位坐标无效"}
        finally:
            self.waiter = None
            service.broadcast()
        return self.state

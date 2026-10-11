"""Release only the matching local development console, preserving its busy guard."""

import httpx
from fastapi import HTTPException


async def release_development_bridge(port: str | None):
    if not port:
        return
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:9010", timeout=6, trust_env=False
    ) as client:
        try:
            schema = await client.get("/openapi.json")
            if schema.status_code != 200 or schema.json().get("info", {}).get("title") != (
                "Air780EHV 本地控制台"
            ):
                return
            state = await client.get("/api/state")
            state.raise_for_status()
            bridge = state.json().get("bridge", {})
            if bridge.get("port") != port or not bridge.get("connected"):
                return
            response = await client.post(
                "/api/flash/bridge/pause", headers={"Origin": "http://127.0.0.1:9011"}
            )
            if response.status_code != 200:
                detail = response.json().get("detail", "开发控制台未能释放串口")
                raise HTTPException(response.status_code, f"开发控制台：{detail}")
            if not response.json().get("paused"):
                raise HTTPException(503, "开发控制台尚未释放串口，请稍后重试")
        except httpx.ConnectError:
            return
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "无法确认开发控制台已释放串口，请稍后重试") from exc

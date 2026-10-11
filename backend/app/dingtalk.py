"""Signed DingTalk SMS notifications; credentials never enter public snapshots."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_settings import AgentSettings

ENDPOINT = "https://oapi.dingtalk.com/robot/send"


class DingTalkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    webhook: str | None = Field(default=None, max_length=500)
    secret: str | None = Field(default=None, max_length=200)
    clear_credentials: bool = False

    @field_validator("webhook")
    @classmethod
    def valid_webhook(cls, value):
        if not value:
            return value
        parts = urlsplit(value)
        query = parse_qs(parts.query)
        tokens = query.get("access_token", [])
        if (
            parts.scheme != "https"
            or parts.netloc != "oapi.dingtalk.com"
            or parts.path != "/robot/send"
            or parts.fragment
            or set(query) != {"access_token"}
            or len(tokens) != 1
            or not re.fullmatch(r"[a-zA-Z0-9_-]{16,200}", tokens[0])
        ):
            raise ValueError("请填写钉钉自定义机器人的 HTTPS Webhook 地址")
        return value

    @field_validator("secret")
    @classmethod
    def valid_secret(cls, value):
        if value and not re.fullmatch(r"SEC[a-zA-Z0-9]{16,197}", value):
            raise ValueError("加签密钥应为 SEC 开头的机器人密钥")
        return value


class DingTalkSettings:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.json"

    def read(self):
        if not self.path.exists():
            return DingTalkUpdate().model_dump(exclude={"clear_credentials"})
        return DingTalkUpdate.model_validate(json.loads(self.path.read_text())).model_dump(
            exclude={"clear_credentials"}
        )

    def snapshot(self):
        data = self.read()
        has_webhook, has_secret = bool(data["webhook"]), bool(data["secret"])
        return {
            "enabled": data["enabled"],
            "has_webhook": has_webhook,
            "has_secret": has_secret,
            "ready": data["enabled"] and has_webhook and has_secret,
        }

    def save(self, update):
        old = self.read()
        data = update.model_dump(exclude={"clear_credentials"})
        for field in ("webhook", "secret"):
            data[field] = None if update.clear_credentials else data[field] or old[field]
        if data["enabled"] and not (data["webhook"] and data["secret"]):
            raise ValueError("启用推送前请配置 Webhook 和加签密钥")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        AgentSettings.write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        return self.snapshot()

    def config(self, *, test=False):
        data = self.read()
        if not (data["webhook"] and data["secret"]):
            raise ValueError("请先保存 Webhook 和加签密钥")
        if not test and not data["enabled"]:
            raise ValueError("短信推送未启用")
        return data


def signed_params(config, timestamp):
    secret = config["secret"]
    signature = hmac.new(
        secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256
    ).digest()
    return {
        "access_token": parse_qs(urlsplit(config["webhook"]).query)["access_token"][0],
        "timestamp": str(timestamp),
        "sign": base64.b64encode(signature).decode(),
    }


async def send_notification(config, text):
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                ENDPOINT,
                params=signed_params(config, int(time.time() * 1000)),
                json={"msgtype": "text", "text": {"content": text}},
            )
            if response.status_code != 200:
                return "unknown", "钉钉 HTTP 响应异常，投递结果未知"
            result = response.json()
            if not isinstance(result, dict) or type(result.get("errcode")) is not int:
                return "unknown", "钉钉返回格式异常，投递结果未知"
            if result["errcode"] != 0:
                return "failed", f"钉钉拒绝推送（错误码 {result['errcode']}），请检查机器人安全设置"
            return "sent", None
    except (httpx.HTTPError, ValueError):
        return "unknown", "网络连接或响应异常，投递结果未知；未自动重试"


class DingTalkNotifier:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.queue = asyncio.Queue(maxsize=100)

    def enqueue(self, message):
        if not message or not self.settings.snapshot()["ready"]:
            return
        if not self.store.push_claim(message["id"]):
            return
        try:
            self.queue.put_nowait((message, self.settings.config()))
        except asyncio.QueueFull:
            self.store.push_update(message["id"], "failed", "推送队列已满，未发送")

    async def run(self):
        while True:
            message, config = await self.queue.get()
            try:
                if not self.settings.snapshot()["ready"]:
                    self.store.push_update(message["id"], "skipped", "推送已关闭，未发送")
                    continue
                self.store.push_update(message["id"], "sending")
                received = time.strftime(
                    "%Y-%m-%d %H:%M:%S %z", time.localtime(message["timestamp"])
                )
                text = (
                    f"智能语音客服 · 收到短信\n短信编号：{message['id'][3:19]}\n"
                    f"发送号码：{message['phone']}\n"
                    f"接收时间：{received}\n短信内容：\n{message['content']}"
                )
                state, error = await send_notification(config, text)
                self.store.push_update(message["id"], state, error)
            finally:
                self.queue.task_done()
                # Pace automatic submissions and leave room for manual connection tests.
                await asyncio.sleep(5)

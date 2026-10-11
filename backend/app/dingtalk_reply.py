"""DingTalk Stream replies to explicitly referenced, locally saved SMS only."""

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
import websockets
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .agent_settings import AgentSettings

TOPIC = "/v1.0/im/bot/messages/get"
OPEN = "https://api.dingtalk.com/v1.0/gateway/connections/open"


class ReplyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    allow_sending: bool = False
    client_id: str = Field(default="", max_length=200)
    client_secret: str | None = Field(default=None, max_length=500)
    conversation_id: str = Field(default="", max_length=300)
    user_ids: list[str] = Field(default_factory=list, max_length=100)
    clear_secret: bool = False

    @field_validator("client_id", "client_secret", "conversation_id")
    @classmethod
    def clean_value(cls, value):
        if value and (value != value.strip() or any(c.isspace() for c in value)):
            raise ValueError("配置不能包含空白字符")
        return value

    @field_validator("user_ids")
    @classmethod
    def valid_users(cls, values):
        if any(not v or len(v) > 200 or any(c.isspace() for c in v) for v in values):
            raise ValueError("用户 ID 不能为空或包含空白")
        return list(dict.fromkeys(values))


class ReplySettings:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "reply.json"

    def read(self):
        if not self.path.exists():
            return ReplyUpdate().model_dump(exclude={"clear_secret"})
        return ReplyUpdate.model_validate(json.loads(self.path.read_text())).model_dump(
            exclude={"clear_secret"}
        )

    def snapshot(self):
        data = self.read()
        secret = data.pop("client_secret")
        return {**data, "has_secret": bool(secret)}

    def save(self, update):
        old = self.read()
        if (
            old["client_id"] != update.client_id
            and old["client_secret"]
            and not (update.client_secret or update.clear_secret)
        ):
            raise ValueError("更换 Client ID 时请填写对应 Client Secret")
        data = update.model_dump(exclude={"clear_secret"})
        data["client_secret"] = (
            None if update.clear_secret else data["client_secret"] or old["client_secret"]
        )
        if data["enabled"] and not (data["client_id"] and data["client_secret"]):
            raise ValueError("连接前请配置应用 Client ID 和 Client Secret")
        if data["allow_sending"] and not data["enabled"]:
            raise ValueError("允许回复短信前请开启连接 Stream 机器人并配置应用凭据")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        AgentSettings.write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        return self.snapshot()


def session_url(value):
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return (
        parts.scheme == "https"
        and parts.netloc == "oapi.dingtalk.com"
        and parts.path == "/robot/sendBySession"
        and bool(parts.query)
        and not parts.fragment
    )


def reply_reference(message):
    text = message.get("text", {})
    content = text.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("请填写要回复的短信内容")
    # Use the raw callback: SDK TextContent preserves repliedMsg as an extension.
    quoted = text.get("repliedMsg", {}).get("content", {}).get("text", "")
    if isinstance(quoted, str) and quoted:
        refs = re.findall(r"(?m)^短信编号：[ \t]*([0-9a-f]{16})[ \t]*$", quoted)
        if len(refs) != 1:
            raise ValueError("引用消息没有唯一短信编号；请引用本应用新推送的短信")
        body = re.sub(r"^回复(?:[：:]\s*|\s+|$)", "", content.strip(), count=1)
        return refs[0], body
    match = re.fullmatch(r"回复\s+([0-9a-f]{16})\s+([\s\S]+)", content.strip())
    if match:
        return match[1], match[2].strip()
    raise ValueError("未收到引用信息，请改用：回复 短信编号 回复内容；不会猜测收件号码")


async def reply_text(message, text):
    url = message.get("sessionWebhook")
    if not session_url(url):
        return False
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = await client.post(url, json={"msgtype": "text", "text": {"content": text}})
            data = response.json()
            return (
                response.status_code == 200 and isinstance(data, dict) and data.get("errcode") == 0
            )
    except (httpx.HTTPError, ValueError):
        return False


class DingTalkReplies:
    def __init__(self, service):
        self.service = service
        self.settings = ReplySettings(service.store.recordings.parent / "dingtalk")
        self.task = None
        self.workers = set()
        self.status = "disabled"
        self.error = None
        self.last_sender = None
        self.accept_after = time.time() * 1000

    def snapshot(self):
        return {
            **self.settings.snapshot(),
            "status": self.status,
            "error": self.error,
            "last_sender": self.last_sender,
            "requests": self.service.store.reply_list(),
        }

    async def restart(self):
        # Stop receiving new commands; already submitted SMS still get a result receipt.
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.error = None
        if self.settings.read()["enabled"]:
            self.status = "connecting"
            self.task = asyncio.create_task(self.run())
        else:
            self.status = "disabled"

    async def close(self):
        tasks = ([self.task] if self.task else []) + list(self.workers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        self.workers.clear()

    async def handle(self, message):
        config = self.settings.read()
        if (
            not config["enabled"]
            or message.get("conversationType") != "2"
            or message.get("isInAtList") is not True
        ):
            return
        sender = message.get("senderStaffId")
        self.last_sender = {"conversation_id": message.get("conversationId"), "user_id": sender}
        if (
            not config["allow_sending"]
            or not message.get("conversationId")
            or not sender
            or (
                config["conversation_id"]
                and message.get("conversationId") != config["conversation_id"]
            )
            or (config["user_ids"] and sender not in config["user_ids"])
        ):
            return
        if (
            message.get("msgtype") != "text"
            or not message.get("msgId")
            or not session_url(message.get("sessionWebhook"))
        ):
            return
        created = message.get("createAt")
        if (
            not isinstance(created, (int, float))
            or created < self.accept_after
            or not 0 <= time.time() * 1000 - created <= 300_000
        ):
            return  # Replayed historical commands must never become fresh SMS.
        request_id = str(
            uuid5(
                NAMESPACE_URL,
                json.dumps(
                    [
                        config["client_id"],
                        message.get("senderCorpId"),
                        message["conversationId"],
                        message["msgId"],
                    ]
                ),
            )
        )
        if not self.service.store.reply_claim(request_id):
            return
        try:
            ref, content = reply_reference(message)
            original = self.service.store.reply_source(ref)
            if not original:
                raise ValueError("未找到唯一且已成功推送的本机短信；未发送")
            mobile = (self.service.device or {}).get("mobile", {})
            if not original["sim_id"] or original["sim_id"] != mobile.get("iccid"):
                raise ValueError("当前 SIM 与原短信不一致或设备未就绪；未发送")
            self.service.command(
                {
                    "action": "send_sms",
                    "request_id": request_id,
                    "to": original["phone"],
                    "content": content,
                }
            )
            self.service.store.reply_update(request_id, "sending", ref)
        except (ValueError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = ValueError("号码或内容不合法：短信最多 512 UTF-8 字节，不能含协议分隔符")
            self.service.store.reply_update(request_id, "failed", error=str(exc))
            await reply_text(message, f"短信未发送：{exc}")
            return
        await reply_text(message, f"已接受回复请求 · 短信编号：{ref}；正在等待模块结果。")
        try:
            deadline = time.monotonic() + 95
            while time.monotonic() < deadline:
                row = self.service.store.get(request_id)
                if row["state"] not in ("queued", "sending"):
                    break
                await asyncio.sleep(0.5)
            else:
                self.service.store.update(request_id, "unknown", "result_timeout")
                row = self.service.store.get(request_id)
            state = row["state"]
            self.service.store.reply_update(request_id, state, ref, row["error"])
            outcome = {
                "submitted": "模块已提交短信中心，不代表对方手机已收到",
                "failed": "模块报告发送失败",
                "unknown": "发送结果未知，请先核实，未自动重试",
            }[state]
            await reply_text(message, f"短信编号：{ref}\n{outcome}")
        finally:
            row = self.service.store.get(request_id)
            self.service.store.reply_update(request_id, row["state"], ref, row["error"])

    async def frame(self, frame, socket):
        headers = frame.get("headers", {})
        payload = frame.get("data", "{}")
        data = json.loads(payload) if isinstance(payload, str) else payload
        kind, topic = frame.get("type"), headers.get("topic")
        code = 200
        if kind == "CALLBACK" and topic == TOPIC:
            if len(self.workers) >= 32:
                code = 500  # No command admitted; server may redeliver.
            elif isinstance(data, dict):
                task = asyncio.create_task(self.handle(data))
                self.workers.add(task)
                task.add_done_callback(self.worker_done)
        await socket.send(
            json.dumps(
                {
                    "code": code,
                    "headers": {
                        "messageId": headers.get("messageId"),
                        "contentType": "application/json",
                    },
                    "message": "OK" if code == 200 else "busy",
                    "data": json.dumps(data if kind == "SYSTEM" else {"response": None}),
                }
            )
        )
        if kind == "SYSTEM" and topic == "disconnect":
            await socket.close()

    def worker_done(self, task):
        self.workers.discard(task)
        if not task.cancelled() and task.exception():
            self.error = "处理消息异常，未自动重发短信；请检查本机发送记录"

    async def run(self):
        while True:
            config = self.settings.read()
            try:
                self.status = "connecting"
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    response = await client.post(
                        OPEN,
                        json={
                            "clientId": config["client_id"],
                            "clientSecret": config["client_secret"],
                            "subscriptions": [{"type": "CALLBACK", "topic": TOPIC}],
                            "ua": "air780-console/1.0",
                            "localIp": "",
                        },
                    )
                    response.raise_for_status()
                    connection = response.json()
                endpoint = connection["endpoint"]
                parts = urlsplit(endpoint)
                if (
                    parts.scheme != "wss"
                    or not parts.hostname
                    or not parts.hostname.endswith(".dingtalk.com")
                    or parts.query
                ):
                    raise ValueError("invalid endpoint")
                async with websockets.connect(
                    endpoint + "?ticket=" + quote(connection["ticket"], safe=""),
                    proxy=None,
                    max_size=256 * 1024,
                ) as socket:
                    self.accept_after = time.time() * 1000
                    self.status, self.error = "connected", None
                    async for raw in socket:
                        await self.frame(json.loads(raw), socket)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = "reconnecting"
                self.error = "Stream 连接失败，请检查应用凭据、机器人发布状态和电脑网络"
            await asyncio.sleep(5)

"""Application-robot SMS notifications, independent of the custom Webhook."""

import asyncio
import json
import time

import httpx

from .dingtalk import notification_text

TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
SEND_URL = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"


def push_config(settings):
    data = settings.read()
    target = data["push_conversation_id"] or data["conversation_id"]
    if not (data["enabled"] and data["push_enabled"] and target):
        return None
    config = {key: data[key] for key in ("client_id", "client_secret", "robot_code")}
    config["push_conversation_id"] = target
    return config


class StreamNotifier:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.queue = asyncio.Queue(maxsize=100)
        self.token = None
        self.token_credentials = None
        self.token_until = 0

    def enqueue(self, message):
        config = push_config(self.settings)
        if not message or not config:
            return
        if not self.store.stream_push_claim(message["id"], config["push_conversation_id"]):
            return
        try:
            self.queue.put_nowait((message, config))
        except asyncio.QueueFull:
            self.store.stream_push_update(message["id"], "failed", "推送队列已满，未发送")

    async def send(self, config, text):
        credentials = (config["client_id"], config["client_secret"])
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            if self.token_credentials != credentials or time.monotonic() >= self.token_until:
                self.token = None
                try:
                    response = await client.post(
                        TOKEN_URL, json={"appKey": credentials[0], "appSecret": credentials[1]}
                    )
                    result = response.json()
                    if response.status_code != 200 or not isinstance(result, dict):
                        raise ValueError("token rejected")
                    token, expiry = result.get("accessToken"), result.get("expireIn")
                    if not isinstance(token, str) or not token or type(expiry) not in (int, float):
                        raise ValueError("invalid token")
                    self.token, self.token_credentials = token, credentials
                    self.token_until = time.monotonic() + max(0, expiry - 300)
                except (httpx.HTTPError, ValueError):
                    return "failed", "获取应用访问令牌失败，未发送；请检查应用凭据和网络"
            try:
                response = await client.post(
                    SEND_URL,
                    headers={"x-acs-dingtalk-access-token": self.token},
                    json={
                        "robotCode": config["robot_code"] or config["client_id"],
                        "openConversationId": config["push_conversation_id"],
                        "msgKey": "sampleText",
                        "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                    },
                )
                if response.status_code == 401:
                    self.token_until = 0
                if 400 <= response.status_code < 500:
                    return "failed", "钉钉拒绝推送；请检查机器人编码、目标群和应用消息权限"
                result = response.json()
                if response.status_code == 200 and isinstance(result, dict):
                    if isinstance(result.get("processQueryKey"), str) and result["processQueryKey"]:
                        return "sent", None
                    if result.get("code") or result.get("errcode"):
                        return "failed", "钉钉拒绝推送；请检查机器人编码、目标群和应用消息权限"
                return "unknown", "钉钉响应异常，投递结果未知；未自动重试"
            except (httpx.HTTPError, ValueError):
                return "unknown", "网络连接或响应异常，投递结果未知；未自动重试"

    async def run(self):
        while True:
            message, config = await self.queue.get()
            try:
                if config != push_config(self.settings):
                    self.store.stream_push_update(
                        message["id"], "skipped", "推送已关闭或配置已变更，未发送"
                    )
                    continue
                self.store.stream_push_update(message["id"], "sending")
                state, error = await self.send(config, notification_text(message))
                self.store.stream_push_update(message["id"], state, error)
            except asyncio.CancelledError:
                self.store.stream_push_update(
                    message["id"], "unknown", "服务停止，投递结果未知；未自动重试"
                )
                raise
            except Exception:
                self.store.stream_push_update(
                    message["id"], "unknown", "推送异常，投递结果未知；未自动重试"
                )
            finally:
                self.queue.task_done()
            await asyncio.sleep(5)

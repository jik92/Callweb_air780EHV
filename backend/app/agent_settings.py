"""Local agent configuration. Secrets and scene instructions stay in ignored data/."""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_INSTRUCTIONS = """# 电话助手

## 身份和目标
你是电话中的 AI 助手。用自然、简短的中文回答，并根据来电人的需求提供帮助。

## 回复方式
- 每次只讲一到两句话，需要确认时一次只问一个问题。
- 对方说话时停止回答，先听完再回复。
- 没有配置的业务信息先询问，不编造价格、承诺、订单或办理结果。
- 不执行短信、拨号、付款等操作；你只能进行语音回复。

## 场景信息
请在这里填写业务背景、服务范围、常见问题与标准回答。
"""


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    provider: Literal["aihubmix", "openai"] = "aihubmix"
    model: str = Field(
        default="gpt-realtime-2.1", pattern=r"^gpt-realtime[a-zA-Z0-9._-]*$", max_length=100
    )
    voice: Literal[
        "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"
    ] = "marin"
    greeting: str = Field(default="您好，我是 AI 电话助手，请问有什么可以帮您？", max_length=500)
    instructions: str = Field(default=DEFAULT_INSTRUCTIONS, min_length=1, max_length=32000)
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False

    @field_validator("instructions")
    @classmethod
    def nonempty_instructions(cls, value):
        if not value.strip():
            raise ValueError("AGENT.md 不能为空")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None and (any(c.isspace() for c in value) or not value.isascii()):
            raise ValueError("API Key 不能包含空白或非 ASCII 字符")
        return value


class AgentSettings:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.json"
        self.markdown = directory / "AGENT.md"

    def read(self):
        if not self.path.exists():
            return AgentUpdate().model_dump(exclude={"clear_api_key"})
        data = json.loads(self.path.read_text())
        if self.markdown.exists():
            data["instructions"] = self.markdown.read_text()
        return AgentUpdate.model_validate(data).model_dump(exclude={"clear_api_key"})

    def snapshot(self):
        data = self.read()
        saved_key = data.pop("api_key", None)
        has_key = bool(saved_key or os.getenv(self.env_name(data)))
        return {
            **data,
            "has_api_key": has_key,
            "key_source": "local" if saved_key else "environment" if has_key else "none",
            "ready": data["enabled"] and has_key,
        }

    def save(self, update: AgentUpdate):
        old = self.read()
        if (
            old["provider"] != update.provider
            and old.get("api_key")
            and not (update.api_key or update.clear_api_key)
        ):
            raise ValueError("切换提供方时请填写对应 API Key，或移除原 Key 使用环境变量")
        data = update.model_dump(exclude={"clear_api_key"})
        data["api_key"] = None if update.clear_api_key else update.api_key or old.get("api_key")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        self.write(self.markdown, update.instructions)
        return self.snapshot()

    @staticmethod
    def write(path, text):
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".agent-")
        try:
            with os.fdopen(fd, "w") as file:
                file.write(text)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def env_name(data):
        return "AIHUBMIX_API_KEY" if data["provider"] == "aihubmix" else "OPENAI_API_KEY"

    def session_config(self):
        data = self.read()
        if not data["enabled"]:
            raise ValueError("请先在 AGENT设置中启用并保存 Agent")
        data["api_key"] = data.get("api_key") or os.getenv(self.env_name(data))
        if not data["api_key"]:
            raise ValueError("请先配置对应服务的 API Key")
        return data

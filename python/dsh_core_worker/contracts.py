"""Validated public requests and capability vocabulary for the worker boundary."""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extracted.contracts.model_service.schemas.provider_types import ProviderKind

Capability = Literal[
    "chat",
    "vision",
    "tools",
    "image_generation",
    "video_generation",
    "video_enhancement",
    "speech_synthesis",
    "speech_recognition",
    "voice",
    "audio_generation",
]
CAPABILITIES = list(get_args(Capability))
PROVIDERS = list(get_args(ProviderKind))
AGENTS = ["codex", "claude-code", "opencode", "dsh", "pi"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelEntry(RequestModel):
    id: str = Field(min_length=1, max_length=160)
    label: str = Field(default="", max_length=200)
    capabilities: list[Capability] = Field(default_factory=lambda: ["chat"])
    enabled: bool = True


class Connection(RequestModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    kind: ProviderKind
    base_url: str = Field(default="", max_length=1024)
    credential_ref: str = Field(default="", pattern=r"^$|^[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = True
    models: list[ModelEntry] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_connection(self):
        from .extracted.runtime.http.outbound_urls import (
            normalize_provider_base_url,
        )

        if len({m.id for m in self.models}) != len(self.models):
            raise ValueError("模型 ID 重复")
        local = self.kind.startswith("local_")
        if local and (self.base_url or self.credential_ref):
            raise ValueError("本地模型通过本地运行环境管理")
        if not local:
            default = {
                "deepseek": "https://api.deepseek.com",
                "openai": "https://api.openai.com/v1",
                "anthropic": "https://api.anthropic.com/v1",
                "volcengine_video": "https://ark.cn-beijing.volces.com/api/v3",
                "autodl_video": "https://www.autodl.art",
            }.get(self.kind, "")
            self.base_url = self.base_url or default
            normalize = normalize_provider_base_url
            self.base_url = normalize(self.base_url)
            if not self.base_url:
                raise ValueError("请填写供应商地址")
        return self


class AgentConnection(RequestModel):
    id: Literal["codex", "claude-code", "opencode", "dsh", "pi"]
    executable: str = Field(default="", max_length=4096)
    enabled: bool = True
    model: str = Field(default="", max_length=160)
    options: dict[str, Any] = Field(default_factory=dict)


class HistoryMessage(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=100000)
    resource_ids: list[str] = Field(default_factory=list, max_length=5)


class Execution(RequestModel):
    request_id: str = Field(min_length=1, max_length=160)
    application_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(default="", max_length=160)
    kind: Literal["model", "agent", "local", "integration"] = "model"
    capability: Capability = "chat"
    connection_id: str = Field(default="", max_length=160)
    model: str = Field(default="", max_length=160)
    prompt: str = Field(default="", max_length=100000)
    messages: list[HistoryMessage] = Field(default_factory=list, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict)
    resource_ids: list[str] = Field(default_factory=list, max_length=30)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class CoreError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", **details):
        super().__init__(message)
        self.code, self.details = code, details

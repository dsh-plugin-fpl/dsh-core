"""Public HTTP snapshots; provider configuration remains inside the execution service."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Capability, HistoryMessage


class BridgeOut(BaseModel):
    id: str
    name: str
    connected: bool
    kind: Literal["dsh", "application"]
    application_id: str | None


class BridgeListOut(BaseModel):
    items: list[BridgeOut]


class ResourceOut(BaseModel):
    id: str
    name: str
    mime_type: str
    byte_size: int
    application_id: str
    sha256: str


class ResourceListOut(BaseModel):
    items: list[ResourceOut]


class ExecutionSnapshotOut(BaseModel):
    request_id: str
    application_id: str
    session_id: str
    kind: Literal["model", "agent", "local", "integration"]
    capability: Capability
    connection_id: str
    model: str
    prompt: str
    parameters: dict[str, Any]
    resource_ids: list[str]
    tools: list[dict[str, Any]]
    messages: list[HistoryMessage] = Field(default_factory=list)


class TaskErrorOut(BaseModel):
    code: str
    message: str


class TaskResultOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    resources: list[ResourceOut] = Field(default_factory=list)
    usage: dict[str, Any] | None = None


class TaskOut(BaseModel):
    id: str
    request: ExecutionSnapshotOut
    status: Literal[
        "queued",
        "running",
        "processing",
        "completed",
        "failed",
        "cancelled",
        "recoverable",
        "interrupted",
    ]
    progress: int
    text: str
    result: TaskResultOut | None
    error: TaskErrorOut | None
    created_at: float
    updated_at: float
    provider_task_id: str
    can_resume: bool = False

    @model_validator(mode="before")
    @classmethod
    def resume_capability(cls, data):
        if isinstance(data, dict):
            from .retired_providers import retired_task

            request = data.get("request", {})
            data = {
                **data,
                "can_resume": not retired_task(data) and data.get("status")
                in ("failed", "cancelled", "interrupted", "recoverable")
                and bool(
                    data.get("provider_task_id")
                    or data.get("result_url")
                    or (
                        request.get("kind") == "local"
                        and request.get("capability") == "video_enhancement"
                    )
                ),
            }
        return data


class TaskListOut(BaseModel):
    items: list[TaskOut]

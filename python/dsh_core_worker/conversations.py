"""Durable test conversations and their task-backed assistant messages."""

import time
from datetime import UTC, datetime
from uuid import uuid4

from .contracts import CoreError


def iso(timestamp=None):
    return datetime.fromtimestamp(timestamp or time.time(), UTC).isoformat()


class Conversations:
    def __init__(self, runtime, catalog):
        self.runtime, self.catalog = runtime, catalog

    def create(self, model_id=None):
        if model_id:
            self.catalog.find(model_id)
        identity = str(uuid4())
        value = {
            "id": identity,
            "title": "新对话",
            "model_id": model_id,
            "model_slug": self.catalog.find(model_id)[1]["slug"] if model_id else "",
            "system_prompt": "",
            "temperature": 0.7,
            "max_tokens": 4096,
            "messages": [],
            "draft_attachments": [],
            "created_at": iso(),
            "updated_at": iso(),
        }
        self.runtime.store.save_config("conversation:" + identity, value, 0)
        return self.get(identity)

    def get(self, identity):
        state = self.runtime.store.config("conversation:" + identity, None)
        if not state["revision"] or not state["value"]:
            raise CoreError("对话不存在", "not_found")
        value = state["value"]
        for message in value["messages"]:
            if message.get("task_id"):
                task = self.runtime.store.task(message["task_id"])
                message["content"] = task["text"]
                message["status"] = {
                    "completed": "completed",
                    "failed": "error",
                    "interrupted": "error",
                    "cancelled": "cancelled",
                }.get(task["status"], "pending")
                message["error_message"] = (task.get("error") or {}).get("message")
        return {**value, "revision": state["revision"]}

    def list(self):
        keys = self.runtime.store.db.execute(
            "SELECT id FROM configs WHERE id LIKE 'conversation:%' AND value != 'null'"
        ).fetchall()
        return {
            "items": sorted(
                [self.get(k[0].split(":", 1)[1]) for k in keys],
                key=lambda c: c["updated_at"],
                reverse=True,
            )
        }

    def save(self, identity, changes, revision):
        value = self.get(identity)
        value.pop("revision")
        if changes.get("model_id"):
            changes["model_slug"] = self.catalog.find(changes["model_id"])[1]["slug"]
        value.update(changes, updated_at=iso())
        self.runtime.store.save_config("conversation:" + identity, value, revision)
        return self.get(identity)

    async def send(self, identity, body, regenerate=False):
        conversation = self.get(identity)
        if conversation["revision"] != body["expected_revision"]:
            raise CoreError("对话已更新，请刷新后重试", "revision_conflict", current=conversation)
        if any(m["status"] == "pending" for m in conversation["messages"]):
            raise CoreError("请等待当前回复完成或停止后再发送", "task_busy")
        messages = list(conversation["messages"])
        content = str(body.get("content", "")).strip()
        attachments = body.get("attachment_ids", [])
        if regenerate:
            if not messages or messages[-1]["role"] != "assistant":
                raise CoreError("没有可以重新生成的回复")
            messages.pop()
        else:
            if not content and not attachments:
                raise CoreError("请输入消息或添加图片")
            draft = {a["id"]: a for a in conversation["draft_attachments"]}
            if any(a not in draft for a in attachments):
                raise CoreError("图片附件不存在")
            messages.append(
                {
                    "id": str(uuid4()),
                    "turn_id": str(uuid4()),
                    "role": "user",
                    "content": content,
                    "model_slug": "",
                    "status": "completed",
                    "error_message": None,
                    "created_at": iso(),
                    "attachments": [draft[a] for a in attachments],
                }
            )
        identity_model = conversation["model_id"] or self.catalog.view()["default_model_ids"].get(
            "chat"
        )
        if not identity_model:
            raise CoreError("请先选择对话模型")
        _provider, model = self.catalog.find(identity_model)
        if "chat" not in model["capabilities"]:
            raise CoreError("所选模型不支持对话")
        history = [
            {
                "role": m["role"],
                "content": m["content"],
                "resource_ids": [a["id"] for a in m.get("attachments", [])],
            }
            for m in messages
            if m["status"] == "completed"
        ]
        request_id = f"conversation-{identity}-{conversation['revision']}"
        task = await self.runtime.invoke(
            "task.submit",
            self.catalog.execution(
                identity_model,
                request_id=request_id,
                application_id="core",
                session_id=identity,
                capability="chat",
                prompt=messages[-1]["content"],
                messages=history,
                resource_ids=history[-1]["resource_ids"],
                parameters={
                    "system": conversation["system_prompt"],
                    "temperature": conversation["temperature"],
                    "max_tokens": conversation["max_tokens"],
                },
            ),
        )
        message = {
            "id": str(uuid4()),
            "turn_id": messages[-1]["turn_id"],
            "task_id": task["id"],
            "role": "assistant",
            "content": "",
            "model_slug": model["slug"],
            "status": "pending",
            "error_message": None,
            "created_at": iso(),
            "attachments": [],
        }
        messages.append(message)
        value = self.save(
            identity,
            {
                "messages": messages,
                "draft_attachments": [],
                "title": content[:40]
                if conversation["title"] == "新对话"
                else conversation["title"],
                "model_id": identity_model,
                "model_slug": model["slug"],
            },
            conversation["revision"],
        )
        return task, value, message

    def attachment(self, resource):
        from PIL import Image

        path, info = self.runtime.resources.path(resource["id"])
        try:
            with Image.open(path) as image:
                if image.width * image.height > 40_000_000:
                    raise CoreError("图片像素超出限制")
                width, height = image.size
        except (OSError, ValueError) as error:
            raise CoreError("请上传可读取的图片") from error
        return {
            "id": info["id"],
            "original_filename": info["name"],
            "mime_type": info["mime_type"],
            "byte_size": info["byte_size"],
            "width": width,
            "height": height,
            "created_at": iso(),
        }

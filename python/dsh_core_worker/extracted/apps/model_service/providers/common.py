"""Shared task states and public error redaction for remote adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dsh_core_worker.extracted.contracts.model_service.task_execution import TaskExecutionInfo

_BEARER = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_DATA_URI = re.compile(r"(?i)(data:[^;,\s]+;base64,)[A-Za-z0-9+/=]+")
_SECRET_FIELD = re.compile(r'(?i)((?:api[_-]?key|token)["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+')


def sanitize_provider_message(value: str) -> str:
    value = _BEARER.sub(r"\1[REDACTED]", value)
    value = _DATA_URI.sub(r"\1[REDACTED]", value)
    return _SECRET_FIELD.sub(r"\1[REDACTED]", value)


@dataclass(frozen=True)
class ProviderTaskStatus:
    status: str
    progress: int = 0
    result_url: str = ""
    error_message: str = ""
    execution_info: TaskExecutionInfo | None = None

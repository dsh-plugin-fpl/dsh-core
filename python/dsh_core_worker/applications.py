"""Plugin-advertised dashboard entries; registration never grants filesystem access."""

from copy import deepcopy
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator

from .contracts import CoreError, RequestModel

Category = Literal["visual", "knowledge", "media", "development"]
Icon = Literal["app", "image", "notebook", "film", "code", "audio", "presentation", "table"]


class DashboardEntry(RequestModel):
    description: str = Field(default="", max_length=500)
    category: Category = "development"
    capabilities: list[str] = Field(default_factory=list, max_length=12)
    package_name: str = Field(default="", max_length=180)
    version: str = Field(default="", max_length=80)
    url: str = Field(default="", max_length=2048)
    icon: Icon = "app"
    icon_url: str = Field(default="", max_length=2048)

    @field_validator("capabilities")
    @classmethod
    def labels(cls, values):
        if any(not v.strip() or len(v) > 40 for v in values):
            raise ValueError("应用标签无效")
        return list(dict.fromkeys(values))

    @field_validator("url", "icon_url")
    @classmethod
    def local_url(cls, value):
        if not value:
            return value
        if any(ord(c) <= 32 for c in value) or "\\" in value:
            raise ValueError("应用地址无效")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username
            or parsed.password
            or parsed.port == 0
        ):
            raise ValueError("应用入口必须是本机 HTTP(S) 地址")
        return value


class ApplicationRegistration(RequestModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    tools: list[dict] = Field(default_factory=list, max_length=100)
    dashboard: DashboardEntry | None = None


class ApplicationOut(DashboardEntry):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)
    id: str
    application_id: str
    bridge_id: str
    bridge_name: str
    name: str
    status: Literal["ready", "offline", "no_entry"]


class ApplicationListOut(RequestModel):
    items: list[ApplicationOut]


class Applications:
    def __init__(self, store, access, online):
        self.store, self.access, self.online = store, access, online

    def replace(self, bridge_id, registrations):
        rows = [ApplicationRegistration.model_validate(row) for row in registrations]
        if len(rows) > 200 or len({row.id for row in rows}) != len(rows):
            raise CoreError("应用目录重复或超出数量限制")
        entries = {
            row.id: {"name": row.name, **row.dashboard.model_dump()}
            for row in rows
            if row.dashboard is not None
        }
        current = self.store.config("application-catalog", {})
        value = deepcopy(current["value"])
        value[bridge_id] = entries
        if value != current["value"]:
            self.store.save_config("application-catalog", value, current["revision"])
        return {row.id: row.model_dump() for row in rows}

    def items(self):
        saved = self.store.config("application-catalog", {})["value"]
        result = []
        for pair in self.access.bridges():
            bridge = self.online.get(pair["id"])
            for identity, entry in saved.get(pair["id"], {}).items():
                status = "offline" if not bridge else "no_entry" if not entry["url"] else "ready"
                # A live connection only exposes currently registered applications.
                if bridge and identity not in bridge.applications:
                    continue
                result.append(
                    {
                        **entry,
                        "id": f"b-{pair['id']}-{identity}",
                        "application_id": identity,
                        "bridge_id": pair["id"],
                        "bridge_name": pair["name"],
                        "status": status,
                    }
                )
        return sorted(result, key=lambda row: (row["name"], row["id"]))

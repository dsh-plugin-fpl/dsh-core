import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Actor:
    user_id: str
    platform_role: str
    auth_kind: str
    scopes: tuple[str, ...]
    session_id: str | None = None
    api_key_id: str | None = None
    local: bool = False
    reauthenticated_at: str | None = None
    purpose: str = "studio"
    api_key_name: str | None = None
    api_key_prefix: str | None = None
    client_ip: str | None = None
    desktop_ticket_sha256: str | None = None

    def __post_init__(self):
        if self.purpose not in {"studio", "management"}:
            raise ValueError("会话用途无效")
        if type(self.local) is not bool or not isinstance(self.scopes, tuple):
            raise ValueError("认证上下文类型无效")
        if not self.user_id or self.platform_role not in {"admin", "user"}:
            raise ValueError("账号上下文无效")
        if self.auth_kind not in {"session", "management_api_key"}:
            raise ValueError("认证方式无效")
        if any((not isinstance(scope, str) or not scope for scope in self.scopes)):
            raise ValueError("认证权限无效")
        if self.auth_kind == "session":
            if not isinstance(self.session_id, str) or not self.session_id or self.api_key_id:
                raise ValueError("Session 上下文无效")
            if self.api_key_name is not None or self.api_key_prefix is not None:
                raise ValueError("Session 不含 API Key 元数据")
        elif not isinstance(self.api_key_id, str) or not self.api_key_id or self.session_id:
            raise ValueError("API Key 上下文无效")
        if self.client_ip is not None:
            from ipaddress import ip_address

            if not isinstance(self.client_ip, str) or len(self.client_ip) > 64:
                raise ValueError("审计客户端地址无效")
            ip_address(self.client_ip)
        if self.reauthenticated_at is not None:
            datetime.fromisoformat(self.reauthenticated_at)
        if self.local and self.auth_kind != "session":
            raise ValueError("主机权限只允许本机浏览器 Session")
        if self.desktop_ticket_sha256 is not None and (
            self.auth_kind != "session"
            or self.purpose != "studio"
            or self.platform_role != "admin"
            or (not isinstance(self.desktop_ticket_sha256, str))
            or (not re.fullmatch("[0-9a-f]{64}", self.desktop_ticket_sha256))
        ):
            raise ValueError("桌面票据上下文无效")
        for value, limit in ((self.api_key_name, 64), (self.api_key_prefix, 16)):
            if value is not None and (not isinstance(value, str) or len(value) > limit):
                raise ValueError("API Key 归因元数据无效")

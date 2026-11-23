"""Explicit owner-configured HTTP endpoints, including local MCP services."""

import ipaddress
from urllib.parse import urlsplit


def normalize_integration_http_url(value):
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("服务地址无效") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port == 0
        or "\\" in value
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("仅支持不含凭据的 HTTP(S) 服务地址")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
        raise ValueError("服务地址不可用")
    return value


async def require_safe_integration_http_url(value):
    return normalize_integration_http_url(value)

from ..contracts.errors import IntegrationPermissionError


def require_integration_owner(principal):
    if not principal.allows("configuration.manage"):
        raise IntegrationPermissionError("需要本机所有者身份")

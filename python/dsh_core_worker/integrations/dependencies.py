"""Local-owner HTTP composition; identity is verified by Core's session middleware."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import Depends, Request

OWNER = "local-owner"
TOOL_BASE_URL = "https://core.invalid/demo"


@dataclass(frozen=True)
class Settings:
    runtime_path: Path
    storage_trash_path: Path
    fpl_skill_archive_max_bytes: int = 20 * 1024 * 1024
    fpl_skill_expanded_max_bytes: int = 100 * 1024 * 1024
    fpl_skill_max_files: int = 1000


class IncomingFile(Protocol):
    async def read(self, size: int = -1) -> bytes: ...
    async def close(self) -> None: ...


class Principal:
    def allows(self, _):
        return True


async def principal():
    return Principal()


async def owner():
    return OWNER


async def session(request: Request):
    async with request.app.state.integrations.database.sessions() as value:
        value.info["owner_user_id"] = OWNER
        yield value


StudioAuth = Annotated[Principal, Depends(principal)]
SessionDep = Annotated[object, Depends(session)]
OwnerDep = Annotated[str, Depends(owner)]


def require_owner_id(value, **_):
    if value != OWNER:
        raise ValueError("Invalid Core owner")
    return value

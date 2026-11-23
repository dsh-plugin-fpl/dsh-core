"""Explicit idempotent invocations, with redacted durable Core receipts."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ...contracts import Execution
from ...http_schemas import TaskOut
from ..dependencies import OwnerDep, SessionDep, StudioAuth
from .catalog_http import _owner


class IntegrationCallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    input: dict = Field(default_factory=dict)


class McpCallIn(IntegrationCallIn):
    tool_name: str = Field(min_length=1, max_length=240)


router = APIRouter(prefix="/v1/integrations", tags=["integration-calls"])


async def invoke(request, session, identity, body, kind):
    manager = request.app.state.integrations
    catalog = manager.mcp_catalog(session) if kind == "mcp" else manager.tool_catalog(session)
    snapshot = await catalog.get(identity)
    execution = Execution(
        request_id=body.request_id,
        application_id="core",
        kind="integration",
        capability="tools",
        connection_id=identity,
        model=kind + ".call",
        prompt=snapshot.name,
        parameters=body.model_dump(exclude={"request_id"}),
    )

    async def operation():
        return (
            await catalog.execute(identity, body.tool_name, body.input, body.expected_revision)
            if kind == "mcp"
            else await catalog.execute(identity, body.input, body.expected_revision)
        )

    return await manager.jobs.record(execution, operation, receipt=True)


@router.post("/tool-apis/{identity}/call", response_model=TaskOut)
async def tool_call(
    request: Request,
    identity: str,
    body: IntegrationCallIn,
    session: SessionDep,
    principal: StudioAuth,
    owner_user_id: OwnerDep,
):
    _owner(principal)
    return await invoke(request, session, identity, body, "tool_api")


@router.post("/mcp/{identity}/call", response_model=TaskOut)
async def mcp_call(
    request: Request,
    identity: str,
    body: McpCallIn,
    session: SessionDep,
    principal: StudioAuth,
    owner_user_id: OwnerDep,
):
    _owner(principal)
    return await invoke(request, session, identity, body, "mcp")

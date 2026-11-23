"""Explicit integration assembly for the independently authenticated local Core."""

from pathlib import Path

from fastapi.responses import JSONResponse

from .database import Database
from .dependencies import OWNER, Settings
from .external.catalog import ExternalConnections
from .jobs import IntegrationJobs
from .mcp.catalog import McpCatalog
from .mcp.client import OfficialMcpDiscoveryClient
from .skills.archive import SkillArchiveStorage
from .skills.catalog import SkillCatalog
from .support.revisions import RevisionConflictError
from .support.secrets import SecretCipher
from .tool_apis.catalog import ToolApiCatalog


class Integrations:
    def __init__(self, root, runtime, access):
        self.root = Path(root).resolve()
        self.database = Database(self.root)
        self.runtime = runtime
        self.settings = Settings(self.root, self.root / "trash")
        self.cipher = SecretCipher((access.root / "secret.key").read_text())
        self.jobs = IntegrationJobs(runtime)
        self.mcp_client = OfficialMcpDiscoveryClient(self.settings)
        self.tool_transport = None
        self.feishu_transport = None

    def tool_catalog(self, session):
        return ToolApiCatalog(
            session, OWNER, cipher=self.cipher, transport=self.tool_transport, execution=self.jobs
        )

    def mcp_catalog(self, session):
        return McpCatalog(
            session, OWNER, cipher=self.cipher, client=self.mcp_client, execution=self.jobs
        )

    def skill_catalog(self, session):
        return SkillCatalog(session, OWNER, storage=SkillArchiveStorage(self.settings, OWNER))

    def external_catalog(self, session):
        return ExternalConnections(
            session, OWNER, cipher=self.cipher, transport=self.feishu_transport, execution=self.jobs
        )

    async def close(self):
        await self.jobs.close()
        await self.database.close()


def register(app):
    from .transport.catalog_http import router
    from .transport.connections_http import router as external

    app.include_router(router, prefix="/v1")
    app.include_router(external, prefix="/v1")
    from .transfers import router as transfers

    app.include_router(transfers)
    from .transport.calls_http import router as calls

    app.include_router(calls)

    async def revision(_request, error):
        return JSONResponse(
            {
                "code": "revision_conflict",
                "detail": "配置已发生变化，请核对后重试",
                "current": error.snapshot,
            },
            409,
        )

    app.add_exception_handler(RevisionConflictError, revision)

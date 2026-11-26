"""Project attachments with durable source receipts and immutable maintenance copies."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .host import CoreError


class ProjectFileIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    source: Literal["core", "application"]
    source_id: str = Field(min_length=1, max_length=64)


class ProjectFiles:
    """Receipts use the account store so Core export/import includes them."""

    key = "product-project-file-receipts"

    def __init__(self, store):
        self.store = store

    def attach(self, owner, project, source, identity, value):
        current = self.store.config(self.key, [])
        items = current["value"]
        if not any(
            x["project_id"] == project and x["source"] == source and x["source_id"] == identity
            for x in items
        ):
            self.store.save_config(
                self.key,
                [*items, {**value, "project_id": project, "source": source, "source_id": identity}],
                current["revision"],
            )
        return value

    def detach(self, project, source, identity):
        current = self.store.config(self.key, [])
        items = [
            x
            for x in current["value"]
            if (x["project_id"], x["source"], x["source_id"]) != (project, source, identity)
        ]
        self.store.save_config(self.key, items, current["revision"])

    def items(self, owner, project=None):
        return [
            x
            for x in self.store.config(self.key, [])["value"]
            if project is None or x["project_id"] == project
        ]


def install_project_files(app, host, access, catalog, transport, application_id):
    """Calls only the product's published routes with its own request-bound assertion."""
    product = application_id.removeprefix("dsh-")
    base = "/v1/" + product + ("/knowledge-bases" if product == "thought-workshop" else "/projects")

    async def business(request, method, path, **kwargs):
        actor = access.session(request.cookies.get(access.cookie, ""))
        operation = catalog.match(method, path)
        if not operation:
            raise HTTPException(404, "project_operation_unavailable")
        assertion = access.assertion(actor, operation.service, method, path)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://" + operation.service + ":8080"
        ) as client:
            response = await client.request(
                method, path, headers={"X-Fpl-Context": assertion}, **kwargs
            )
        if not response.is_success:
            raise HTTPException(response.status_code, "project_or_file_unavailable")
        return response.json()

    async def application_files(request):
        folders = await business(request, "GET", "/v1/file-folders")
        items = []
        for folder in [None, *folders["items"]]:
            result = await business(
                request,
                "GET",
                "/v1/files",
                params={**({"folder_id": folder["id"]} if folder else {}), "include_svg": "true"},
            )
            items.extend(result["items"])
        return {"items": items}

    async def copy_application_file(request, info, resources):
        path = "/v1/files/" + info["id"] + "/download"
        actor = access.session(request.cookies.get(access.cookie, ""))
        operation = catalog.match("GET", path)
        assertion = access.assertion(actor, operation.service, "GET", path)
        digest, size = hashlib.sha256(), 0
        with TemporaryDirectory(
            prefix="project-file-import-", dir=resources.store.root
        ) as temporary:
            original = Path(temporary) / "original"
            component = transport.components.get(operation.service)
            if component is None:
                raise HTTPException(503, "source_component_unavailable")
            headers = [(b"x-fpl-context", assertion.encode())]
            scope = {
                **request.scope,
                "method": "GET",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": headers,
                "app": component,
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "extensions": {},
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            with original.open("wb") as output:

                async def send(message):
                    nonlocal size
                    if message["type"] == "http.response.start" and message["status"] != 200:
                        raise HTTPException(message["status"], "source_file_unavailable")
                    if message["type"] == "http.response.body":
                        chunk = message.get("body", b"")
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)

                await component(scope, receive, send)
            if size != info["byte_size"]:
                raise HTTPException(409, "source_file_integrity")
            from dsh_core_worker.file_library import FileLibrary

            # An immutable maintenance copy makes the project independent of legacy cleanup.
            row = {
                **info,
                "source_folder_id": info.get("folder_id"),
                "folder_id": None,
                "core_library_hidden": True,
            }
            return FileLibrary(resources).import_original(
                row,
                original,
                digest.hexdigest(),
                str(uuid5(NAMESPACE_URL, "project-file-import:" + product)),
            )

    async def context(request, project=None, write=False):
        profile = host.profile(request.cookies.get(access.cookie, ""))
        actor = access.session(request.cookies.get(access.cookie, ""))
        if "studio:private_read" not in actor.scopes:
            raise HTTPException(403, "content_read_required")
        if write and "studio:write" not in actor.scopes:
            raise HTTPException(403, "content_write_required")
        if project:
            try:
                UUID(project)
            except ValueError:
                raise HTTPException(422, "invalid_project_id") from None
            await business(request, "GET", base + "/" + project)
        tenant = await host.app.state.account_services.ensure(profile)
        return profile["id"], tenant.state.runtime.resources

    @app.get("/v1/app/file-projects")
    async def projects(request: Request):
        await context(request)
        result = await business(request, "GET", base)
        if product == "presentation-workshop":
            while len(result["items"]) < result.get("total", len(result["items"])):
                page = await business(
                    request, "GET", base, params={"offset": len(result["items"]), "limit": 200}
                )
                if not page["items"]:
                    break
                result["items"].extend(page["items"])
        return {"items": result} if isinstance(result, list) else result

    @app.get("/v1/app/project-files")
    async def project_files(request: Request, project_id: str):
        owner, resources = await context(request, project_id)
        receipts = ProjectFiles(resources.store)
        return {"items": receipts.items(owner, project_id)}

    @app.get("/v1/app/legacy-application-files")
    async def legacy_application_files(request: Request):
        await context(request)
        return await application_files(request)

    @app.get("/v1/app/legacy-file-receipts")
    async def legacy_receipts(request: Request):
        owner, resources = await context(request)
        receipts = ProjectFiles(resources.store)
        return {"items": receipts.items(owner)}

    @app.get("/v1/app/project-file-content")
    async def project_file_content(
        request: Request,
        project_id: str,
        source_id: str,
        source: Literal["core", "application"] = "core",
    ):
        owner, resources = await context(request, project_id)
        receipt = next(
            (
                item
                for item in ProjectFiles(resources.store).items(owner, project_id)
                if item["source"] == source and item["source_id"] == source_id
            ),
            None,
        )
        if receipt is None:
            raise HTTPException(404, "project_file_not_found")
        try:
            path, info = resources.path(receipt.get("resource_id", source_id))
        except CoreError as error:
            raise HTTPException(409, error.code) from None
        return FileResponse(path, media_type=info["mime_type"], filename=receipt["name"])

    @app.delete("/v1/app/project-files")
    async def detach(request: Request, body: ProjectFileIn):
        _, resources = await context(request, body.project_id, write=True)
        ProjectFiles(resources.store).detach(body.project_id, body.source, body.source_id)
        return {"removed": True}

    @app.post("/v1/app/project-files")
    async def attach(request: Request, body: ProjectFileIn):
        owner, resources = await context(request, body.project_id, write=True)
        existing = next(
            (
                item
                for item in ProjectFiles(resources.store).items(owner, body.project_id)
                if item["source"] == body.source and item["source_id"] == body.source_id
            ),
            None,
        )
        if existing is not None:
            return existing
        if body.source == "core":
            try:
                _path, info = resources.path(body.source_id)
                from dsh_core_worker.file_library import FileLibrary

                info = FileLibrary(resources).entry(body.source_id)
            except CoreError as error:
                raise HTTPException(409, error.code) from None
            value = {
                **info,
                "name": info["name"],
                "content_url": "/v1/app/project-file-content?project_id="
                + body.project_id
                + "&source_id="
                + info["id"],
            }
        elif body.source == "application":
            # Published description and the immutable file ID retain existing references.
            result = await application_files(request)
            info = next((x for x in result["items"] if x["id"] == body.source_id), None)
            if info is None:
                raise HTTPException(404, "legacy_file_unavailable")
            try:
                copied = await copy_application_file(request, info, resources)
            except (CoreError, ValueError):
                raise HTTPException(409, "source_file_integrity") from None
            except OSError:
                raise HTTPException(503, "file_import_interrupted") from None
            value = {
                **info,
                "sha256": copied["sha256"],
                "resource_id": copied["id"],
                "name": info.get("display_name") or info["original_filename"],
                "content_url": "/v1/app/project-file-content?project_id="
                + body.project_id
                + "&source=application&source_id="
                + info["id"],
            }
        else:
            raise HTTPException(422, "invalid_file_source")
        ProjectFiles(resources.store).attach(
            owner, body.project_id, body.source, body.source_id, value
        )
        return {
            **value,
            "project_id": body.project_id,
            "source": body.source,
            "source_id": body.source_id,
        }

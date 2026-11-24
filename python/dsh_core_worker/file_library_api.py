"""Public account-isolated browsing and editing of Core's file library."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from .file_library import FileLibrary, LibraryEntry, LibraryFolder, LibraryPage


class LibraryTransferRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_form(request: Request):
            # Parse with explicit collection limits before FastAPI reuses the form.
            # Its default 1000 fields would otherwise reject large directory imports.
            await request.form(max_files=5000, max_fields=5002)
            return await handler(request)

        return bounded_form


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    parent_id: str | None = None


class FolderPatch(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    parent_id: str | None = None


class EntryPatch(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    folder_id: str | None = None


def install(app):
    def library():
        value = getattr(app.state, "file_library", None)
        if value is None:
            value = app.state.file_library = FileLibrary(app.state.runtime.resources)
        return value

    @app.get("/v1/file-library", response_model=LibraryPage)
    async def page(
        folder_id: str | None = None,
        after: str | None = None,
        search: str = "",
        limit: int = Query(100, ge=1, le=200),
        kind: Literal["all", "image", "video", "audio", "pdf", "text", "file"] = "all",
        sort: Literal["updated", "name", "size"] = "updated",
    ):
        return library().page(folder_id, after, search, limit, kind, sort)

    @app.get("/v1/file-library/folders", response_model=list[LibraryFolder])
    async def folders():
        with library().database() as db:
            return [
                library().folder_value(row)
                for row in db.execute("SELECT * FROM library_folders ORDER BY name,id")
            ]

    @app.post("/v1/file-library/folders", response_model=LibraryFolder)
    async def create_folder(body: FolderCreate):
        return library().create_folder(body.name, body.parent_id)

    @app.patch("/v1/file-library/folders/{identity}", response_model=LibraryFolder)
    async def patch_folder(identity: str, body: FolderPatch):
        changes = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if changes.get("name", "") is None:
            raise ValueError("名称不能为空")
        return library().patch_folder(identity, body.expected_revision, changes)

    @app.delete("/v1/file-library/folders/{identity}")
    async def delete_folder(identity: str, expected_revision: int = Query(ge=1)):
        return library().delete_folder(identity, expected_revision)

    @app.get("/v1/file-library/entries/{identity}/content")
    async def entry_content(identity: str, download: bool = False):
        from fastapi.responses import FileResponse

        value = library().entry(identity)
        path, _ = library().resources.path(identity)
        safe = (
            value["mime_type"].startswith(("image/", "audio/", "video/"))
            and value["mime_type"] != "image/svg+xml"
        )
        return FileResponse(
            path,
            media_type=value["mime_type"] if safe else "application/octet-stream",
            filename=value["name"],
            content_disposition_type="attachment" if download or not safe else "inline",
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )

    @app.patch("/v1/file-library/entries/{identity}", response_model=LibraryEntry)
    async def patch_entry(identity: str, body: EntryPatch):
        changes = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if changes.get("name", "") is None:
            raise ValueError("名称不能为空")
        return library().patch_entry(identity, body.expected_revision, changes)

    @app.delete("/v1/file-library/entries/{identity}", response_model=LibraryEntry)
    async def delete_entry(identity: str, expected_revision: int = Query(ge=1)):
        return library().patch_entry(identity, expected_revision, {}, remove=True)

    import hashlib
    import json
    import zipfile

    from fastapi import File, Form, UploadFile
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask
    from starlette.concurrency import run_in_threadpool

    from .file_library_transfers import (
        MAX_BYTES,
        MAX_ENTRIES,
        LibraryImport,
        build_export,
        export_manifest,
        relative_path,
        validate_paths,
        zip_members,
    )

    imports = APIRouter(route_class=LibraryTransferRoute)

    @imports.post("/v1/file-library/archive-imports")
    async def import_archive(
        request_id: Annotated[str, Form()],
        archive: Annotated[UploadFile, File()],
        parent_id: Annotated[str | None, Form()] = None,
    ):
        try:
            if archive.size is None or archive.size > MAX_BYTES:
                raise ValueError("ZIP 不能超过 256 MB")
            digest = hashlib.sha256()
            while chunk := await archive.read(1024 * 1024):
                digest.update(chunk)
            await archive.seek(0)
            with zipfile.ZipFile(archive.file) as zipped:
                members = zip_members(zipped)
                job = LibraryImport(library(), request_id, parent_id, digest.hexdigest())
                if job.completed:
                    return job.completed
                for path, member in members:
                    if member.is_dir():
                        job.folder(path)
                    else:
                        data = await run_in_threadpool(zipped.read, member)
                        job.file(path, data)
                return job.finish()
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError("ZIP 无法读取或已损坏") from error
        finally:
            await archive.close()

    @imports.post("/v1/file-library/directory-imports")
    async def import_directory(
        request_id: Annotated[str, Form()],
        paths: Annotated[list[str], Form()],
        files: Annotated[list[UploadFile], File()],
        parent_id: Annotated[str | None, Form()] = None,
    ):
        try:
            if (
                len(files) != len(paths)
                or len(files) > MAX_ENTRIES
                or sum(file.size or 0 for file in files) > MAX_BYTES
            ):
                raise ValueError("单次最多导入 5000 个文件、合计 256 MB")
            paths = [relative_path(path) for path in paths]
            if len({path.casefold() for path in paths}) != len(paths):
                raise ValueError("文件路径重复")
            validate_paths([(path, False) for path in paths])
            digest = hashlib.sha256(
                json.dumps(
                    list(zip(paths, [file.size for file in files])), ensure_ascii=False
                ).encode()
            )
            for file in files:
                while chunk := await file.read(1024 * 1024):
                    digest.update(chunk)
                await file.seek(0)
            job = LibraryImport(library(), request_id, parent_id, digest.hexdigest())
            if job.completed:
                return job.completed
            for path, file in zip(paths, files):
                job.file(path, await file.read())
            return job.finish()
        finally:
            for file in files:
                await file.close()

    @app.get("/v1/file-library/imports/{request_id}")
    async def import_status(request_id: str):
        from uuid import UUID

        from .contracts import CoreError

        receipt = library().store.config("library-import:" + str(UUID(request_id)), None)
        if not receipt["revision"]:
            raise CoreError("导入回执不存在", "not_found")
        return {
            key: receipt["value"][key] for key in ("status", "result") if key in receipt["value"]
        }

    @app.get("/v1/file-library/folder-export")
    async def export_folder(folder_id: str | None = None):
        manifest, directory = export_manifest(library(), folder_id)
        stream = await run_in_threadpool(build_export, manifest, directory)

        def chunks():
            try:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
            finally:
                stream.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="files.zip"'},
            background=BackgroundTask(stream.close),
        )

    app.include_router(imports)

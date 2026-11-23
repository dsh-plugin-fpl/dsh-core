"""Public account-isolated browsing over Core's owned file library."""

from fastapi import Query

from .file_library import FileLibrary, LibraryPage


def install(app):
    @app.get("/v1/file-library", response_model=LibraryPage)
    async def page(
        folder_id: str | None = None,
        after: str | None = None,
        search: str = "",
        limit: int = Query(100, ge=1, le=200),
    ):
        library = getattr(app.state, "file_library", None)
        if library is None:
            library = app.state.file_library = FileLibrary(app.state.runtime.resources)
        return library.page(folder_id, after, search, limit)

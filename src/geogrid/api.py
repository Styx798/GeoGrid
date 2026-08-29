from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from geogrid import __version__
from geogrid.domain import ContractError
from geogrid.store import ReviewStore, RevisionConflict


class CommandRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    command_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


def create_app(project_dir: Path) -> FastAPI:
    store = ReviewStore(project_dir)
    web_dir = Path(__file__).resolve().parent / "web"
    vendor_asset = web_dir / "vendor" / "konva.min.js"
    if not vendor_asset.is_file():
        raise RuntimeError("缺少 Konva 构建产物；请先在 frontend 目录运行 npm install 和 npm run build")

    app = FastAPI(
        title="GeoGrid Review API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.review_store = store

    @app.exception_handler(RevisionConflict)
    async def revision_conflict_handler(_: Request, exc: RevisionConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "state": store.state_for_client()})

    @app.exception_handler(ContractError)
    async def contract_error_handler(_: Request, exc: ContractError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "project_id": store.manifest["project_id"],
            "coordinate_frame": store.session["coordinate_frame"],
        }

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return store.state_for_client()

    @app.get("/api/image")
    def image() -> FileResponse:
        return FileResponse(store.image_path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/command")
    def command(request: CommandRequest) -> dict[str, Any]:
        return store.apply_command(request.command_type, request.payload, request.expected_revision)

    @app.post("/api/undo")
    def undo(request: RevisionRequest) -> dict[str, Any]:
        return store.undo(request.expected_revision)

    @app.post("/api/redo")
    def redo(request: RevisionRequest) -> dict[str, Any]:
        return store.redo(request.expected_revision)

    @app.get("/api/readiness")
    def readiness() -> dict[str, Any]:
        return store.readiness()

    @app.post("/api/export")
    def export() -> dict[str, Any]:
        mesh, target = store.export_reviewed_mesh()
        return {
            "schema": mesh["schema"],
            "node_count": len(mesh["nodes"]),
            "edge_count": len(mesh["edges"]),
            "cell_count": len(mesh["cells"]),
            "path": str(target),
            "download_url": "/download/reviewed_mesh.json",
        }

    @app.get("/download/reviewed_mesh.json")
    def download_export() -> FileResponse:
        store.exported_mesh()
        target = store.project_dir / "exports" / "reviewed_mesh.json"
        return FileResponse(target, media_type="application/json", filename="reviewed_mesh.json")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=web_dir), name="static")
    return app

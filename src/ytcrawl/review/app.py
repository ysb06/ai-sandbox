from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ytcrawl.config import get_config
from ytcrawl.db import core
from ytcrawl.review import schemas
import ytcrawl.review.service as service

STATIC_DIR = Path(__file__).resolve().parent / "static"
_application: FastAPI | None = None


def create_app(
    *,
    db_url: str | None = None,
    media_root: str | Path | None = None,
) -> FastAPI:
    if db_url is None or media_root is None:
        config = get_config()
        selected_db_url = config.remote_db_url if db_url is None else db_url
        selected_media_root_value = (
            config.remote_media_root if media_root is None else media_root
        )
    else:
        selected_db_url = db_url
        selected_media_root_value = media_root

    selected_media_root = Path(selected_media_root_value).resolve()
    core.configure(selected_db_url)
    core.create_all()
    app = FastAPI(title="ytcrawl review")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/videos", response_model=schemas.VideoListResponse)
    def list_videos(
        start_id: int = Query(1, ge=1),
        rows: int = Query(50, ge=1, le=100),
        username: str | None = Query(None),
    ) -> schemas.VideoListResponse:
        return service.list_videos(
            start_id=start_id,
            rows=rows,
            username=username,
        )

    @app.get("/api/videos/{video_ref_id}", response_model=schemas.VideoDetailResponse)
    def get_video(video_ref_id: int) -> schemas.VideoDetailResponse:
        response = service.get_video_detail(
            media_root=selected_media_root,
            video_ref_id=video_ref_id,
        )
        if response is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        return response

    @app.get(
        "/api/videos/{video_ref_id}/review/{username}",
        response_model=schemas.ReviewResponse,
    )
    def get_review(video_ref_id: int, username: str) -> schemas.ReviewResponse:
        response = service.get_review(
            video_ref_id=video_ref_id,
            username=username,
        )
        if response is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        return response

    @app.put(
        "/api/videos/{video_ref_id}/review/{username}",
        response_model=schemas.ReviewResponse,
    )
    def put_review(
        video_ref_id: int,
        username: str,
        request: schemas.ReviewUpdateRequest,
    ) -> schemas.ReviewResponse:
        response = service.upsert_review(
            video_ref_id=video_ref_id,
            username=username,
            status=request.status,
            note=request.note,
            segments=request.segments,
        )
        if response is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        return response

    @app.get("/media/videos/{video_ref_id}")
    def get_video_media(video_ref_id: int) -> FileResponse:
        path = service.resolve_media_path(
            video_ref_id=video_ref_id,
            media_root=selected_media_root,
        )
        if path is None:
            raise HTTPException(status_code=404, detail="Video file not found.")
        return FileResponse(
            path,
            filename=path.name,
            content_disposition_type="inline",
        )

    return app


def __getattr__(name: str) -> FastAPI:
    """Build the configured ASGI app only when the public ``app`` is used."""
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    global _application
    if _application is None:
        _application = create_app()
    return _application

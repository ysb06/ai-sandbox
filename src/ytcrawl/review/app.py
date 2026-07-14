from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ytcrawl.db import core
from ytcrawl.review import schemas
import ytcrawl.review.service as service

DEFAULT_DB_URL = "sqlite:///results/ytcrawl.sqlite3"
DEFAULT_MEDIA_ROOT = Path("results/ytcrawl").resolve()
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    db_url: str | None = None,
    media_root: str | Path | None = None,
) -> FastAPI:
    selected_db_url = db_url or os.environ.get("YTCRAWL_DB_URL", DEFAULT_DB_URL)
    selected_media_root = Path(
        media_root or os.environ.get("YTCRAWL_MEDIA_ROOT", DEFAULT_MEDIA_ROOT)
    ).resolve()
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
            media_type="video/mp4",
            filename=path.name,
            content_disposition_type="inline",
        )

    return app


app = create_app()

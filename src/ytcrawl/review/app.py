from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from ytcrawl.db import core
from ytcrawl.review import schemas
import ytcrawl.review.service as service

DEFAULT_DB_URL = "sqlite:///results/ytcrawl.sqlite3"
DEFAULT_MEDIA_ROOT = Path("results/ytcrawl").resolve()


engine = core.create_engine_for_url(DEFAULT_DB_URL)
core.create_all(engine)
app = FastAPI(title="ytcrawl review")


@app.get("/", response_class=HTMLResponse)
def hello_world() -> str:
    return """
    <!doctype html>
    <html lang="ko">
        <head>
        <meta charset="utf-8">
        <title>ytcrawl review</title>
        </head>
        <body>
        <h1>Hello World!</h1>
        </body>
    </html>
    """


@app.get("/api/videos", response_model=schemas.VideoListResponse)
def list_videos(
    start_id: int = Query(1, ge=1),
    rows: int = Query(50, ge=1, le=100),
) -> schemas.VideoListResponse:
    return service.list_videos(engine=engine, start_id=start_id, rows=rows)


@app.get("/api/videos/{video_ref_id}", response_model=schemas.VideoDetailResponse)
def get_video(video_ref_id: int) -> schemas.VideoDetailResponse:
    response = service.get_video_detail(
        engine=engine,
        media_root=DEFAULT_MEDIA_ROOT,
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
        engine=engine,
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
        engine=engine,
        video_ref_id=video_ref_id,
        username=username,
        status=request.status,
        note=request.note,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    return response


@app.get("/media/videos/{video_ref_id}")
def get_video_media(video_ref_id: int) -> FileResponse:
    path = service.resolve_media_path(
        engine=engine,
        video_ref_id=video_ref_id,
        media_root=DEFAULT_MEDIA_ROOT,
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
    )

import logging
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ytcrawln.download.sftp import SFTPDownloadError, SFTPTimeoutError
from ytcrawln.review import schemas, service
from ytcrawln.review.media_cache import (
    MediaCacheError,
    ensure_local_video,
    media_filename,
)

router = APIRouter()
_LOGGER = logging.getLogger(__name__)


async def _ensure_media_file(
    source: str | FilePath | None,
    request: Request,
    *,
    video_ref_id: int,
) -> FilePath | None:
    try:
        return await ensure_local_video(source, cache_root=request.app.state.cache_root)
    except (MediaCacheError, SFTPDownloadError) as error:
        # Source URLs and raw exception messages can contain private paths or
        # credentials. Keep diagnostics limited to safe stage/type labels.
        _LOGGER.warning(
            "Review media failure video_ref_id=%s stage=%s reason=%s",
            video_ref_id,
            error.stage,
            error.reason,
        )
        if isinstance(error, SFTPTimeoutError):
            status_code, detail = 504, "Remote media download timed out."
        elif isinstance(error, MediaCacheError):
            status_code, detail = 500, "Local media cache is unavailable."
        else:
            status_code, detail = 502, "Remote media download failed."
        raise HTTPException(status_code=status_code, detail=detail) from None


@router.get(
    "/videos/{video_ref_id}",
    response_model=schemas.VideoDetailResponse,
    tags=["videos"],
)
async def get_video_details(
    request: Request,
    video_ref_id: Annotated[int, Path(ge=1)],
) -> schemas.VideoDetailResponse:
    data = await run_in_threadpool(
        service.get_video_detail,
        video_ref_id,
        media_root=request.app.state.media_root,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    path = await _ensure_media_file(data.source, request, video_ref_id=video_ref_id)
    return schemas.VideoDetailResponse(
        video=data.video,
        detail=data.detail,
        media=schemas.MediaInfo(
            available=path is not None,
            url=f"/videos/media/{video_ref_id}" if path is not None else None,
        ),
    )


@router.api_route(
    "/videos/media/{video_ref_id}",
    methods=["GET", "HEAD"],
    response_class=FileResponse,
    tags=["videos"],
)
async def get_video_media(
    request: Request,
    video_ref_id: Annotated[int, Path(ge=1)],
) -> FileResponse:
    source = await run_in_threadpool(
        service.resolve_media_path,
        video_ref_id,
        media_root=request.app.state.media_root,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Video file not found.")
    path = await _ensure_media_file(source, request, video_ref_id=video_ref_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(
        path,
        filename=media_filename(source),
        content_disposition_type="inline",
    )

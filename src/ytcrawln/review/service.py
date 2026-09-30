from math import isfinite
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ytcrawln.db import core
from ytcrawln.db.tables.face_in_video import FaceInVideo
from ytcrawln.db.tables.videos import Videos
from ytcrawln.db.tables.videos_detail import VideoDetail
from ytcrawln.review.schemas import (
    ClipItem,
    ClipListResponse,
    MediaInfo,
    VideoDetailResponse,
    VideoInfo,
    VideoMetadata,
)


class ClipDataError(RuntimeError):
    """A candidate has inconsistent or invalid detection timestamps."""


def list_clips() -> ClipListResponse:
    """Return every distinct video/frame candidate without changing the database."""
    candidates = (
        select(
            FaceInVideo.video_ref_id,
            FaceInVideo.frame,
            func.min(FaceInVideo.time_sec).label("time_sec"),
            func.max(FaceInVideo.time_sec).label("max_time_sec"),
        )
        .group_by(FaceInVideo.video_ref_id, FaceInVideo.frame)
        .subquery()
    )
    statement = (
        select(
            candidates.c.video_ref_id,
            candidates.c.frame,
            candidates.c.time_sec,
            candidates.c.max_time_sec,
            Videos.title,
        )
        .select_from(candidates)
        .outerjoin(Videos, Videos.id == candidates.c.video_ref_id)
        .order_by(candidates.c.video_ref_id, candidates.c.frame)
    )

    with Session(core.get_engine()) as session:
        items = []
        for row in session.execute(statement):
            time_sec = row.time_sec
            items.append(
                ClipItem(
                    video_ref_id=row.video_ref_id,
                    frame=row.frame,
                    time_sec=time_sec,
                    title=row.title,
                )
            )

    return ClipListResponse(items=items)


def get_video_detail(
    video_ref_id: int,
    *,
    media_root: Path,
) -> VideoDetailResponse | None:
    """Return stored metadata and local file availability for one video."""
    statement = (
        select(Videos, VideoDetail)
        .outerjoin(VideoDetail, VideoDetail.video_ref_id == Videos.id)
        .where(Videos.id == video_ref_id)
    )
    with Session(core.get_engine()) as session:
        row = session.execute(statement).one_or_none()
        if row is None:
            return None

        video, detail = row
        video_info = VideoInfo(
            id=video.id,
            video_id=video.video_id,
            title=video.title,
            description=video.description,
            publishTime=video.publishTime,
            kind=video.kind,
            etag=video.etag,
        )
        detail_info = (
            VideoMetadata(
                duration=detail.duration,
                resolution=detail.resolution,
                has_caption=detail.has_caption,
                language=detail.language,
                audio_language=detail.audio_language,
                license=detail.license,
                view_count=detail.view_count,
                like_count=detail.like_count,
                comment_count=detail.comment_count,
                recording_date=detail.recording_date,
                location=detail.location,
                is_synthetic_marked=detail.is_synthetic_marked,
                tags=detail.tags,
                rating=detail.rating,
                other_info=detail.other_info,
            )
            if detail is not None
            else None
        )
        stored_path = video.path

    path = _resolve_video_file_path(stored_path, media_root)
    return VideoDetailResponse(
        video=video_info,
        detail=detail_info,
        media=MediaInfo(
            available=path is not None,
            url=f"/videos/media/{video_ref_id}" if path is not None else None,
        ),
    )


def _resolve_video_file_path(stored_path: str | None, media_root: Path) -> Path | None:
    if not stored_path:
        return None

    try:
        path = Path(stored_path).expanduser()
        if not path.is_absolute():
            path = media_root.expanduser() / path
        path = path.resolve()
        return path if path.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None

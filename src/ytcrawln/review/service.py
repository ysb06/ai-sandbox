from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ytcrawln.db import core
from ytcrawln.db.tables.clip_candidates import ClipCandidate
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


class ClipCandidatesNotInitializedError(RuntimeError):
    """The manual candidate registration command has not prepared the table."""


def list_clips() -> ClipListResponse:
    """Return every stored clip candidate without changing the database."""
    statement = (
        select(
            ClipCandidate.id,
            ClipCandidate.video_ref_id,
            ClipCandidate.frame,
            ClipCandidate.time_sec,
            Videos.title,
        )
        .select_from(ClipCandidate)
        .outerjoin(Videos, Videos.id == ClipCandidate.video_ref_id)
        .order_by(ClipCandidate.video_ref_id, ClipCandidate.frame)
    )

    with Session(core.get_engine()) as session:
        if not inspect(session.connection()).has_table(ClipCandidate.__tablename__):
            raise ClipCandidatesNotInitializedError(
                "검수 후보 테이블이 없습니다. 프로젝트 루트에서 "
                "pdm run python -m ytcrawln.db.importers.clip_candidates "
                "명령을 실행한 뒤 페이지를 새로고침해 주세요."
            )
        items = []
        for row in session.execute(statement):
            items.append(
                ClipItem(
                    id=row.id,
                    video_ref_id=row.video_ref_id,
                    frame=row.frame,
                    time_sec=row.time_sec,
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


def resolve_media_path(video_ref_id: int, *, media_root: Path) -> Path | None:
    """Resolve an existing video file using the same rules as the detail API."""
    statement = select(Videos.path).where(Videos.id == video_ref_id)
    with Session(core.get_engine()) as session:
        stored_path = session.execute(statement).scalar_one_or_none()

    return _resolve_video_file_path(stored_path, media_root)


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

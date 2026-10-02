from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ytcrawln.db import core
from ytcrawln.db.tables.clip_candidates import ClipCandidate
from ytcrawln.db.tables.clip_reviews import ClipReview
from ytcrawln.db.tables.videos import Videos
from ytcrawln.db.tables.videos_detail import VideoDetail
from ytcrawln.review.schemas import (
    ClipItem,
    ClipListResponse,
    MediaInfo,
    ReviewItem,
    ReviewListResponse,
    ReviewSaveRequest,
    VideoDetailResponse,
    VideoInfo,
    VideoMetadata,
)
from ytcrawln.utils import is_sftp_source


class ClipCandidatesNotInitializedError(RuntimeError):
    """The manual candidate registration command has not prepared the table."""


class ClipReviewsNotInitializedError(RuntimeError):
    """The database initialization command has not prepared the review table."""


class ClipCandidateNotFoundError(LookupError):
    """The requested review candidate does not exist."""


def _ensure_clip_tables(session: Session, *, reviews: bool = False) -> None:
    inspector = inspect(session.connection())
    if not inspector.has_table(ClipCandidate.__tablename__):
        raise ClipCandidatesNotInitializedError(
            "검수 후보 테이블이 없습니다. 프로젝트 루트에서 "
            "pdm run python -m ytcrawln.db.importers.clip_candidates "
            "명령을 실행한 뒤 페이지를 새로고침해 주세요."
        )
    if reviews and not inspector.has_table(ClipReview.__tablename__):
        raise ClipReviewsNotInitializedError(
            "검수 기록 테이블이 없습니다. 프로젝트 루트에서 "
            "pdm run python -m ytcrawln 명령을 실행해 주세요. "
            "별도 설정을 사용한다면 Review 서버와 같은 --config를 지정해 주세요."
        )


def _require_clip_candidate(session: Session, clip_id: int) -> None:
    candidate_id = session.scalar(
        select(ClipCandidate.id).where(ClipCandidate.id == clip_id)
    )
    if candidate_id is None:
        raise ClipCandidateNotFoundError("Clip candidate not found.")


def list_clips(username: str | None = None) -> ClipListResponse:
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
    if username is not None:
        # Keep unreviewed candidates by filtering the reviewer in ON, not WHERE.
        statement = statement.add_columns(ClipReview).outerjoin(
            ClipReview,
            and_(
                ClipReview.clip_candidate_id == ClipCandidate.id,
                ClipReview.username == username,
            ),
        )

    with Session(core.get_engine()) as session:
        _ensure_clip_tables(session, reviews=username is not None)
        items = []
        for row in session.execute(statement):
            review = row.ClipReview if username is not None else None
            items.append(
                ClipItem(
                    id=row.id,
                    video_ref_id=row.video_ref_id,
                    frame=row.frame,
                    time_sec=row.time_sec,
                    title=row.title,
                    my_review=(
                        ReviewItem.model_validate(review) if review is not None else None
                    ),
                )
            )

    return ClipListResponse(items=items)


def list_clip_reviews(clip_id: int) -> ReviewListResponse:
    """Return every reviewer's latest saved decision for an existing candidate."""
    with Session(core.get_engine()) as session:
        _ensure_clip_tables(session, reviews=True)
        _require_clip_candidate(session, clip_id)
        statement = (
            select(ClipReview)
            .where(ClipReview.clip_candidate_id == clip_id)
            .order_by(ClipReview.updated_at.desc(), ClipReview.id.desc())
        )
        items = [
            ReviewItem.model_validate(review)
            for review in session.scalars(statement)
        ]
    return ReviewListResponse(clip_candidate_id=clip_id, items=items)


def save_clip_review(clip_id: int, request: ReviewSaveRequest) -> ReviewItem:
    """Create or update one review and return only after a successful commit."""
    with core.session_scope() as session:
        _ensure_clip_tables(session, reviews=True)
        _require_clip_candidate(session, clip_id)
        now = datetime.now(timezone.utc)
        updates = {
            "status": request.status,
            "note": request.note,
            # SQLite upserts do not invoke the model's Python onupdate callback.
            "updated_at": now,
        }
        statement = (
            insert(ClipReview)
            .values(
                clip_candidate_id=clip_id,
                username=request.username,
                created_at=now,
                **updates,
            )
            .on_conflict_do_update(
                index_elements=["clip_candidate_id", "username"],
                set_=updates,
            )
        )
        session.execute(statement)
        saved = session.execute(
            select(ClipReview).where(
                ClipReview.clip_candidate_id == clip_id,
                ClipReview.username == request.username,
            )
        ).scalar_one()
        response = ReviewItem.model_validate(saved)

    # session_scope commits on exit; a failed commit must never yield success.
    return response


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
    if not stored_path or is_sftp_source(stored_path):
        return None

    try:
        path = Path(stored_path).expanduser()
        if not path.is_absolute():
            path = media_root.expanduser() / path
        path = path.resolve()
        return path if path.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawl.db.core import Base


class VideoDownloadAttempt(Base):
    __tablename__ = "video_download_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downloader: Mapped[str] = mapped_column(String(128), nullable=False)
    format_selector: Mapped[str] = mapped_column(Text, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_attempt(session: Session, id: int) -> VideoDownloadAttempt:
    attempt = session.get(VideoDownloadAttempt, id)
    if attempt is None:
        raise ValueError(f"Video download attempt not found: {id}")
    return attempt


def create_download_attempt(
    session: Session,
    *,
    video_ref_id: int,
    downloader: str,
    format_selector: str,
) -> VideoDownloadAttempt:
    attempt = VideoDownloadAttempt(
        video_ref_id=video_ref_id,
        downloader=downloader,
        format_selector=format_selector,
    )
    session.add(attempt)
    session.flush()
    return attempt


def mark_download_attempt_succeeded(
    session: Session,
    *,
    id: int,
    file_size_bytes: int | None,
) -> VideoDownloadAttempt:
    attempt = _get_attempt(session, id)
    attempt.finished_at = _now_utc()
    attempt.file_size_bytes = file_size_bytes
    attempt.error_type = None
    attempt.error_message = None
    session.flush()
    return attempt


def mark_download_attempt_failed(
    session: Session,
    *,
    id: int,
    error_type: str,
    error_message: str,
) -> VideoDownloadAttempt:
    attempt = _get_attempt(session, id)
    attempt.finished_at = _now_utc()
    attempt.error_type = error_type
    attempt.error_message = error_message
    session.flush()
    return attempt


def find_attempts_for_video(
    session: Session,
    *,
    video_ref_id: int,
) -> tuple[VideoDownloadAttempt, ...]:
    return tuple(
        session.scalars(
            select(VideoDownloadAttempt)
            .where(VideoDownloadAttempt.video_ref_id == video_ref_id)
            .order_by(VideoDownloadAttempt.id)
        )
    )


def find_latest_attempt_for_video(
    session: Session,
    *,
    video_ref_id: int,
) -> VideoDownloadAttempt | None:
    return session.scalars(
        select(VideoDownloadAttempt)
        .where(VideoDownloadAttempt.video_ref_id == video_ref_id)
        .order_by(VideoDownloadAttempt.id.desc())
    ).first()

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy import delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawl.db.core import Base


class VideoReviewSegment(Base):
    __tablename__ = "video_review_segments"
    __table_args__ = (
        CheckConstraint(
            "start_ms >= 0",
            name="ck_video_review_segments_start_ms_nonnegative",
        ),
        CheckConstraint(
            "end_ms > start_ms",
            name="ck_video_review_segments_end_after_start",
        ),
        UniqueConstraint(
            "review_id",
            "start_ms",
            "end_ms",
            name="uq_video_review_segments_review_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("video_reviews.id"),
        nullable=False,
        index=True,
    )
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)


def find_segments_for_review(
    session: Session,
    *,
    review_id: int,
) -> tuple[VideoReviewSegment, ...]:
    return tuple(
        session.scalars(
            select(VideoReviewSegment)
            .where(VideoReviewSegment.review_id == review_id)
            .order_by(
                VideoReviewSegment.start_ms,
                VideoReviewSegment.end_ms,
                VideoReviewSegment.id,
            )
        )
    )


def replace_segments_for_review(
    session: Session,
    *,
    review_id: int,
    segments: Sequence[tuple[int, int]],
) -> tuple[VideoReviewSegment, ...]:
    session.execute(
        delete(VideoReviewSegment).where(VideoReviewSegment.review_id == review_id)
    )
    rows = tuple(
        VideoReviewSegment(
            review_id=review_id,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for start_ms, end_ms in sorted(segments)
    )
    session.add_all(rows)
    session.flush()
    return rows

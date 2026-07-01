from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawl.db.core import Base

REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_ACCEPTED = "accepted"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUS_NEEDS_REVIEW = "needs_review"
REVIEW_STATUSES = (
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_NEEDS_REVIEW,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class VideoReview(Base):
    __tablename__ = "video_reviews"
    __table_args__ = (
        UniqueConstraint(
            "username",
            "video_ref_id",
            name="uq_video_reviews_username_video_ref_id",
        ),
        CheckConstraint(
            "status in ('pending', 'accepted', 'rejected', 'needs_review')",
            name="ck_video_reviews_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    video_ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=REVIEW_STATUS_PENDING,
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now_utc,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now_utc,
        onupdate=_now_utc,
        nullable=False,
    )

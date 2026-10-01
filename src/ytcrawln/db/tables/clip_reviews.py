from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawln.db.core import Base

REVIEW_STATUS_ACCEPTED = "accepted"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUS_NEEDS_REVIEW = "needs_review"
REVIEW_STATUSES = (
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_NEEDS_REVIEW,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ClipReview(Base):
    """The latest review for one clip candidate and reviewer."""

    __tablename__ = "clip_reviews"
    __table_args__ = (
        UniqueConstraint(
            "clip_candidate_id",
            "username",
            name="uq_clip_reviews_clip_candidate_id_username",
        ),
        CheckConstraint(
            "status in ('accepted', 'rejected', 'needs_review')",
            name="ck_clip_reviews_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clip_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("clip_candidates.id"),
        nullable=False,
    )
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
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

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawln.db.core import Base


class VideoDetail(Base):
    __tablename__ = "videos_detail"
    __table_args__ = (
        UniqueConstraint("video_ref_id", name="uq_videos_detail_video_ref_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
        index=True,
    )
    duration: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    has_caption: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rating: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    other_info: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    is_synthetic_marked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

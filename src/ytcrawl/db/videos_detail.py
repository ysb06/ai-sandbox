from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawl.db.core import Base


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
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rating_key_prefix(key: str) -> str:
    if key.endswith("Rating"):
        return key[: -len("Rating")].lower()
    return key.lower()


def _extract_rating(
    content_rating: dict[str, Any],
    status: dict[str, Any],
) -> list[str]:
    rating = [
        f"{_rating_key_prefix(str(key))}-{str(value).lower()}"
        for key, value in content_rating.items()
        if value not in (None, "")
    ]
    if status.get("madeForKids") is True:
        rating.append("youtube-kids")
    return rating


def _extract_other_info(
    topic_details: dict[str, Any],
    status: dict[str, Any],
) -> list[str]:
    other_info = list(_list_value(topic_details.get("topicCategories")))
    if status.get("containsSyntheticMedia") is True:
        other_info.append("containsSyntheticMedia")
    return other_info

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


def extract_video_detail_values(item: dict[str, Any]) -> dict[str, Any]:
    snippet = _dict_value(item.get("snippet"))
    content_details = _dict_value(item.get("contentDetails"))
    status = _dict_value(item.get("status"))
    statistics = _dict_value(item.get("statistics"))
    recording_details = _dict_value(item.get("recordingDetails"))
    topic_details = _dict_value(item.get("topicDetails"))

    return {
        "duration": content_details.get("duration"),
        "resolution": content_details.get("definition"),
        "has_caption": _parse_bool(content_details.get("caption")),
        "tags": list(_list_value(snippet.get("tags"))),
        "language": snippet.get("defaultLanguage"),
        "audio_language": snippet.get("defaultAudioLanguage"),
        "license": status.get("license"),
        "view_count": _parse_int(statistics.get("viewCount")),
        "like_count": _parse_int(statistics.get("likeCount")),
        "comment_count": _parse_int(statistics.get("commentCount")),
        "recording_date": recording_details.get("recordingDate"),
        "location": recording_details.get("location"),
        "rating": _extract_rating(
            _dict_value(content_details.get("contentRating")),
            status,
        ),
        "other_info": _extract_other_info(topic_details, status),
        "raw": item,
    }


def create_or_update_video_detail(
    session: Session,
    *,
    video_ref_id: int,
    item: dict[str, Any],
) -> VideoDetail:
    values = extract_video_detail_values(item)
    detail = session.scalars(
        select(VideoDetail).where(VideoDetail.video_ref_id == video_ref_id)
    ).first()
    if detail is None:
        detail = VideoDetail(video_ref_id=video_ref_id, **values)
        session.add(detail)
    else:
        for key, value in values.items():
            setattr(detail, key, value)

    session.flush()
    return detail

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawl.db.core import Base


@dataclass(frozen=True)
class VideoRecord:
    id: int
    video_id: str | None


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_id: Mapped[int] = mapped_column(
        ForeignKey("youtube_search_runs.id"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    video_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publishTime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)


def create_videos_from_search_response(
    session: Session,
    *,
    search_id: int,
    response: dict[str, Any],
) -> tuple[VideoRecord, ...]:
    video_rows: list[Video] = []
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        item_id = item.get("id", {})
        video = Video(
            search_id=search_id,
            kind=item.get("kind"),
            etag=item.get("etag"),
            video_id=item_id.get("videoId"),
            title=snippet.get("title"),
            description=snippet.get("description"),
            publishTime=snippet.get("publishTime") or item.get("publishTime"),
        )
        session.add(video)
        video_rows.append(video)

    session.flush()
    return tuple(VideoRecord(id=video.id, video_id=video.video_id) for video in video_rows)


def find_videos_for_search(
    session: Session,
    *,
    search_id: int,
) -> tuple[Video, ...]:
    return tuple(
        session.scalars(select(Video).where(Video.search_id == search_id).order_by(Video.id))
    )


def find_videos_from_id(
    session: Session,
    *,
    start_id: int,
    rows: int,
) -> tuple[Video, ...]:
    return tuple(
        session.scalars(
            select(Video)
            .where(Video.id >= start_id)
            .order_by(Video.id)
            .limit(rows)
        )
    )


def find_video_by_id(
    session: Session,
    *,
    video_ref_id: int,
) -> Video | None:
    return session.get(Video, video_ref_id)


def update_video_path(
    session: Session,
    *,
    id: int,
    path: str,
) -> None:
    video = session.get(Video, id)
    if video is not None:
        video.path = path


def _to_video_records(video_rows: tuple[Video, ...]) -> tuple[VideoRecord, ...]:
    return tuple(VideoRecord(id=video.id, video_id=video.video_id) for video in video_rows)


def find_video_records_for_search(
    session: Session,
    *,
    search_id: int,
) -> tuple[VideoRecord, ...]:
    return _to_video_records(find_videos_for_search(session, search_id=search_id))


def find_video_records_by_video_ids(
    session: Session,
    *,
    video_ids: list[str],
) -> tuple[VideoRecord, ...]:
    if not video_ids:
        return ()
    video_rows = tuple(
        session.scalars(
            select(Video)
            .where(Video.video_id.in_(video_ids))
            .order_by(Video.id)
        )
    )
    return _to_video_records(video_rows)


def find_video_records_without_path(
    session: Session,
) -> tuple[VideoRecord, ...]:
    video_rows = tuple(
        session.scalars(select(Video).where(Video.path.is_(None)).order_by(Video.id))
    )
    return _to_video_records(video_rows)

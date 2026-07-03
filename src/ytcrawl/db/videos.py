from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, inspect, select, text
from sqlalchemy.engine import Engine
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
    embed_code: Mapped[str | None] = mapped_column(Text, nullable=True)


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


def update_video_embed_code(
    session: Session,
    *,
    id: int,
    embed_code: str | None,
) -> None:
    video = session.get(Video, id)
    if video is not None:
        video.embed_code = embed_code


def extract_embed_code(raw: Any) -> str | None:
    value = _json_value(raw)
    if not isinstance(value, dict):
        return None

    player = _dict_value(value.get("player"))
    embed_code = player.get("embedHtml")
    if isinstance(embed_code, str) and embed_code:
        return embed_code

    content_details = _dict_value(value.get("contentDetails"))
    nested_player = _dict_value(content_details.get("player"))
    nested_embed_code = nested_player.get("embedHtml")
    if isinstance(nested_embed_code, str) and nested_embed_code:
        return nested_embed_code

    return None


def migrate_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "videos" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("videos")}
    with engine.begin() as connection:
        if "embed_code" not in columns:
            connection.execute(text("ALTER TABLE videos ADD COLUMN embed_code TEXT"))

        if "videos_detail" not in table_names:
            return
        detail_columns = {
            column["name"] for column in inspector.get_columns("videos_detail")
        }
        if not {"video_ref_id", "raw"}.issubset(detail_columns):
            return

        rows = connection.execute(
            text(
                """
                SELECT videos.id AS id,
                       videos.embed_code AS embed_code,
                       videos_detail.raw AS raw
                FROM videos
                JOIN videos_detail
                  ON videos_detail.video_ref_id = videos.id
                """
            )
        ).mappings()
        for row in rows:
            if row["embed_code"] is not None:
                continue
            embed_code = extract_embed_code(row["raw"])
            if embed_code is None:
                continue
            connection.execute(
                text(
                    """
                    UPDATE videos
                    SET embed_code = :embed_code
                    WHERE id = :id
                    """
                ),
                {"id": row["id"], "embed_code": embed_code},
            )


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


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

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawl.db.core import Base


class YouTubeSearchRun(Base):
    __tablename__ = "youtube_search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collection_method: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playlist_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_after: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_before: Mapped[str | None] = mapped_column(String(32), nullable=True)
    part: Mapped[str] = mapped_column(String(32), nullable=False)
    search_type: Mapped[str] = mapped_column(String(32), nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    safe_search: Mapped[str | None] = mapped_column(String(16), nullable=True)
    video_license: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_page_token: Mapped[str | None] = mapped_column(String(256), nullable=True)
    total_results: Mapped[int | None] = mapped_column(Integer, nullable=True)
    results_per_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)


def find_latest_matching_search_run(
    session: Session,
    *,
    request_hash: str,
) -> YouTubeSearchRun | None:
    return session.scalars(
        select(YouTubeSearchRun)
        .where(YouTubeSearchRun.request_hash == request_hash)
        .order_by(YouTubeSearchRun.executed_at.desc(), YouTubeSearchRun.id.desc())
    ).first()


def find_search_run_by_id(
    session: Session,
    *,
    search_id: int,
) -> YouTubeSearchRun | None:
    return session.get(YouTubeSearchRun, search_id)


def find_search_run_ids_by_request_hash(
    session: Session,
    *,
    request_hash: str,
) -> tuple[int, ...]:
    return tuple(
        session.scalars(
            select(YouTubeSearchRun.id)
            .where(YouTubeSearchRun.request_hash == request_hash)
            .order_by(YouTubeSearchRun.page, YouTubeSearchRun.id)
        )
    )


def create_search_run(
    session: Session,
    *,
    query: str,
    channel_id: str | None = None,
    video_license: str = "creativeCommon",
    published_after: str | None,
    published_before: str | None,
    fixed_params: dict[str, Any],
    request_hash: str,
    page: int,
    response: dict[str, Any],
) -> YouTubeSearchRun:
    return _create_collection_run(
        session,
        collection_method="search",
        query=query,
        channel_id=channel_id,
        playlist_id=None,
        video_license=video_license,
        published_after=published_after,
        published_before=published_before,
        fixed_params=fixed_params,
        request_hash=request_hash,
        page=page,
        response=response,
        item_count=len(response.get("items", [])),
    )


def create_channel_upload_run(
    session: Session,
    *,
    channel_id: str,
    playlist_id: str,
    published_after: str | None,
    published_before: str | None,
    fixed_params: dict[str, Any],
    request_hash: str,
    page: int,
    response: dict[str, Any],
    item_count: int,
) -> YouTubeSearchRun:
    return _create_collection_run(
        session,
        collection_method="channel_uploads",
        query=None,
        channel_id=channel_id,
        playlist_id=playlist_id,
        video_license=None,
        published_after=published_after,
        published_before=published_before,
        fixed_params=fixed_params,
        request_hash=request_hash,
        page=page,
        response=response,
        item_count=item_count,
    )


def _create_collection_run(
    session: Session,
    *,
    collection_method: str,
    query: str | None,
    channel_id: str | None,
    playlist_id: str | None,
    video_license: str | None,
    published_after: str | None,
    published_before: str | None,
    fixed_params: dict[str, Any],
    request_hash: str,
    page: int,
    response: dict[str, Any],
    item_count: int,
) -> YouTubeSearchRun:
    page_info = response.get("pageInfo", {})
    run = YouTubeSearchRun(
        request_hash=request_hash,
        collection_method=collection_method,
        query=query,
        channel_id=channel_id,
        playlist_id=playlist_id,
        published_after=published_after,
        published_before=published_before,
        part=fixed_params["part"],
        search_type=fixed_params["type"],
        max_results=int(fixed_params["maxResults"]),
        region_code=fixed_params.get("regionCode"),
        safe_search=fixed_params.get("safeSearch"),
        video_license=video_license,
        response_kind=response.get("kind"),
        response_etag=response.get("etag"),
        next_page_token=response.get("nextPageToken"),
        total_results=page_info.get("totalResults"),
        results_per_page=page_info.get("resultsPerPage"),
        item_count=item_count,
        page=page,
    )
    session.add(run)
    session.flush()

    return run

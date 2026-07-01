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
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    published_after: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_before: Mapped[str | None] = mapped_column(String(32), nullable=True)
    part: Mapped[str] = mapped_column(String(32), nullable=False)
    search_type: Mapped[str] = mapped_column(String(32), nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    region_code: Mapped[str] = mapped_column(String(8), nullable=False)
    safe_search: Mapped[str] = mapped_column(String(16), nullable=False)
    video_license: Mapped[str] = mapped_column(String(32), nullable=False)
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


def create_search_run(
    session: Session,
    *,
    query: str,
    published_after: str | None,
    published_before: str | None,
    fixed_params: dict[str, Any],
    request_hash: str,
    page: int,
    response: dict[str, Any],
) -> YouTubeSearchRun:
    items = response.get("items", [])
    page_info = response.get("pageInfo", {})
    run = YouTubeSearchRun(
        request_hash=request_hash,
        query=query,
        published_after=published_after,
        published_before=published_before,
        part=fixed_params["part"],
        search_type=fixed_params["type"],
        max_results=int(fixed_params["maxResults"]),
        region_code=fixed_params["regionCode"],
        safe_search=fixed_params["safeSearch"],
        video_license=fixed_params["videoLicense"],
        response_kind=response.get("kind"),
        response_etag=response.get("etag"),
        next_page_token=response.get("nextPageToken"),
        total_results=page_info.get("totalResults"),
        results_per_page=page_info.get("resultsPerPage"),
        item_count=len(items),
        page=page,
    )
    session.add(run)
    session.flush()
    
    return run

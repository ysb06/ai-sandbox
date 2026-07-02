from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ReviewStatus = Literal["pending", "accepted", "rejected", "needs_review"]


class VideoSummary(BaseModel):
    id: int
    video_id: str | None
    title: str | None
    publishTime: str | None
    has_path: bool
    review_status: ReviewStatus | None = None
    reviewed: bool = False


class VideoListResponse(BaseModel):
    items: list[VideoSummary]
    start_id: int
    rows: int
    next_start_id: int | None
    has_more: bool


class VideoInfo(BaseModel):
    id: int
    search_id: int
    kind: str | None
    etag: str | None
    video_id: str | None
    title: str | None
    description: str | None
    publishTime: str | None
    path: str | None


class VideoDetailInfo(BaseModel):
    duration: str | None
    resolution: str | None
    has_caption: bool | None
    tags: list[str]
    language: str | None
    audio_language: str | None
    license: str | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    recording_date: str | None
    location: dict | None
    rating: list[str]
    other_info: list[str]
    is_synthetic_marked: bool | None


class SearchRunInfo(BaseModel):
    id: int
    query: str


class DownloadAttemptInfo(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    file_size_bytes: int | None
    downloader: str
    format_selector: str
    error_type: str | None
    error_message: str | None


class MediaInfo(BaseModel):
    available: bool
    url: str | None


class VideoDetailResponse(BaseModel):
    video: VideoInfo
    search_run: SearchRunInfo | None
    detail: VideoDetailInfo | None
    latest_download_attempt: DownloadAttemptInfo | None
    media: MediaInfo


class ReviewResponse(BaseModel):
    username: str
    video_ref_id: int
    status: ReviewStatus
    note: str | None
    persisted: bool


class ReviewUpdateRequest(BaseModel):
    status: ReviewStatus
    note: str | None = None

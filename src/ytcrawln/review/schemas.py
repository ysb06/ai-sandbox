from typing import Any

from pydantic import BaseModel, Field


class ClipItem(BaseModel):
    video_ref_id: int
    frame: int
    time_sec: float
    title: str | None


class ClipListResponse(BaseModel):
    items: list[ClipItem]


class VideoInfo(BaseModel):
    id: int
    video_id: str | None
    title: str | None
    description: str | None
    publishTime: str | None
    kind: str | None
    etag: str | None


class VideoMetadata(BaseModel):
    duration: str | None
    resolution: str | None
    has_caption: bool | None
    language: str | None
    audio_language: str | None
    license: str | None

    view_count: int | None
    like_count: int | None
    comment_count: int | None

    recording_date: str | None
    location: dict[str, Any] | None
    is_synthetic_marked: bool | None

    tags: list[str] = Field(default_factory=list)
    rating: list[str] = Field(default_factory=list)
    other_info: list[str] = Field(default_factory=list)


class MediaInfo(BaseModel):
    available: bool
    url: str | None


class VideoDetailResponse(BaseModel):
    video: VideoInfo
    detail: VideoMetadata | None
    media: MediaInfo

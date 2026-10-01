from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ReviewStatus = Literal["accepted", "rejected", "needs_review"]
ReviewUsername = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class ReviewSaveRequest(BaseModel):
    """A reviewer's complete decision and optional note for one candidate."""

    username: ReviewUsername
    status: ReviewStatus
    note: str | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ReviewItem(BaseModel):
    """One reviewer's latest saved review, with timestamps expressed in UTC."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(ge=1)
    clip_candidate_id: int = Field(ge=1)
    username: ReviewUsername
    status: ReviewStatus
    note: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        # SQLite can return stored UTC timestamps without timezone information.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ReviewListResponse(BaseModel):
    clip_candidate_id: int = Field(ge=1)
    items: list[ReviewItem]


class ClipItem(BaseModel):
    id: int
    video_ref_id: int
    frame: int
    time_sec: float
    title: str | None
    my_review: ReviewItem | None = None


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

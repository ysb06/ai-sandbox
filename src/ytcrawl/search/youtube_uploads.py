from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ytcrawl.search.youtube import create_youtube_client

COLLECTION_METHOD = "channel_uploads"
UPLOADS_PLAYLIST_PART = "snippet,contentDetails"
FIXED_UPLOADS_PARAMS: dict[str, Any] = {
    "part": UPLOADS_PLAYLIST_PART,
    "type": "video",
    "maxResults": 50,
}


@dataclass(frozen=True)
class SkippedPlaylistItem:
    index: int
    reason: str


@dataclass(frozen=True)
class PlaylistItemSelection:
    items: tuple[dict[str, Any], ...]
    skipped: tuple[SkippedPlaylistItem, ...]


def create_uploads_client(api_key: str) -> Any:
    return create_youtube_client(api_key)


def fetch_uploads_playlist_id(youtube: Any, *, channel_id: str) -> str:
    response = (
        youtube.channels()
        .list(part="contentDetails", id=channel_id)
        .execute(num_retries=0)
    )
    items = response.get("items", [])
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ValueError(f"YouTube channel not found: {channel_id}")

    content_details = _dict_value(items[0].get("contentDetails"))
    related_playlists = _dict_value(content_details.get("relatedPlaylists"))
    playlist_id = related_playlists.get("uploads")
    if not isinstance(playlist_id, str) or not playlist_id:
        raise ValueError(f"Uploads playlist not found for channel: {channel_id}")
    return playlist_id


def fetch_uploads_page(
    youtube: Any,
    *,
    playlist_id: str,
    page_token: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "part": UPLOADS_PLAYLIST_PART,
        "playlistId": playlist_id,
        "maxResults": FIXED_UPLOADS_PARAMS["maxResults"],
    }
    if page_token:
        params["pageToken"] = page_token
    return youtube.playlistItems().list(**params).execute(num_retries=0)


def build_request_hash(
    *,
    channel_id: str,
    published_after: str | None,
    published_before: str | None,
) -> str:
    payload = {
        "collection_method": COLLECTION_METHOD,
        "channel_id": channel_id,
        "published_after": published_after,
        "published_before": published_before,
        "fixed_params": FIXED_UPLOADS_PARAMS,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_published_range(
    published_after: str | None,
    published_before: str | None,
) -> tuple[datetime | None, datetime | None]:
    lower_bound = (
        _parse_rfc3339(published_after, field_name="published_after")
        if published_after
        else None
    )
    upper_bound = (
        _parse_rfc3339(published_before, field_name="published_before")
        if published_before
        else None
    )
    if lower_bound is not None and upper_bound is not None:
        if lower_bound > upper_bound:
            raise ValueError("published_after must be earlier than published_before")
    return lower_bound, upper_bound


def select_playlist_items(
    response: dict[str, Any],
    *,
    published_after: datetime | None,
    published_before: datetime | None,
) -> PlaylistItemSelection:
    selected: list[dict[str, Any]] = []
    skipped: list[SkippedPlaylistItem] = []
    raw_items = response.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("playlistItems.list response items must be a list")

    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            skipped.append(SkippedPlaylistItem(index, "item is not an object"))
            continue

        video_id = extract_video_id(item)
        if not video_id:
            skipped.append(SkippedPlaylistItem(index, "missing video_id"))
            continue

        published_at_value = extract_video_published_at(item)
        if not published_at_value:
            skipped.append(SkippedPlaylistItem(index, "missing videoPublishedAt"))
            continue
        try:
            published_at = _parse_rfc3339(
                published_at_value,
                field_name="videoPublishedAt",
            )
        except ValueError as exc:
            skipped.append(SkippedPlaylistItem(index, str(exc)))
            continue

        if published_after is not None and published_at < published_after:
            continue
        if published_before is not None and published_at > published_before:
            continue
        selected.append(item)

    return PlaylistItemSelection(tuple(selected), tuple(skipped))


def extract_video_id(item: dict[str, Any]) -> str | None:
    content_details = _dict_value(item.get("contentDetails"))
    video_id = content_details.get("videoId")
    if isinstance(video_id, str) and video_id:
        return video_id

    snippet = _dict_value(item.get("snippet"))
    resource_id = _dict_value(snippet.get("resourceId"))
    fallback = resource_id.get("videoId")
    if isinstance(fallback, str) and fallback:
        return fallback
    return None


def extract_video_published_at(item: dict[str, Any]) -> str | None:
    content_details = _dict_value(item.get("contentDetails"))
    value = content_details.get("videoPublishedAt")
    if isinstance(value, str) and value:
        return value
    return None


def _parse_rfc3339(value: str, *, field_name: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any

from ytcrawl.db import core, videos, videos_detail
from ytcrawl.search import youtube_detail

EXCLUDED_LIVE_BROADCAST_CONTENT = frozenset({"live", "upcoming"})


@dataclass(frozen=True)
class DetailCrawlResult:
    record_ids_by_video_id: dict[str, list[int]]
    detail_items_by_video_id: dict[str, dict[str, Any]]
    saved: int = 0
    failures: int = 0


@dataclass(frozen=True)
class LiveVideoPartition:
    downloadable: tuple[videos.VideoRecord, ...]
    excluded: tuple[videos.VideoRecord, ...]
    status_by_video_id: dict[str, str]


def get_detail_live_broadcast_content(
    item: dict[str, Any] | None,
) -> str | None:
    if not isinstance(item, dict):
        return None
    snippet = item.get("snippet")
    if not isinstance(snippet, dict):
        return None
    value = snippet.get("liveBroadcastContent")
    return value.lower() if isinstance(value, str) else None


def partition_live_video_records(
    video_records: tuple[videos.VideoRecord, ...],
    detail_result: DetailCrawlResult,
) -> LiveVideoPartition:
    status_by_video_id = {
        video_id: live_status
        for video_id, item in detail_result.detail_items_by_video_id.items()
        if (
            live_status := get_detail_live_broadcast_content(item)
        ) in EXCLUDED_LIVE_BROADCAST_CONTENT
    }
    downloadable: list[videos.VideoRecord] = []
    excluded: list[videos.VideoRecord] = []
    for record in video_records:
        target = (
            excluded
            if record.video_id in status_by_video_id
            else downloadable
        )
        target.append(record)
    return LiveVideoPartition(
        downloadable=tuple(downloadable),
        excluded=tuple(excluded),
        status_by_video_id=status_by_video_id,
    )


def group_video_record_ids_by_video_id(
    video_records: tuple[videos.VideoRecord, ...],
) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for record in video_records:
        if record.video_id:
            grouped.setdefault(record.video_id, []).append(record.id)
    return grouped


def map_video_detail_items_by_video_id(
    responses: list[dict],
) -> dict[str, dict[str, Any]]:
    detail_items: dict[str, dict[str, Any]] = {}
    for response in responses:
        for item in response.get("items", []):
            video_id = item.get("id")
            if video_id:
                detail_items[video_id] = item
    return detail_items


def crawl_youtube_details(
    api_key: str,
    video_records: tuple[videos.VideoRecord, ...],
) -> DetailCrawlResult:
    detail_saved = 0
    detail_failures = 0
    record_ids_by_video_id = group_video_record_ids_by_video_id(video_records)
    if not record_ids_by_video_id:
        return DetailCrawlResult(
            record_ids_by_video_id=record_ids_by_video_id,
            detail_items_by_video_id={},
            saved=detail_saved,
            failures=detail_failures,
        )

    try:
        detail_responses = youtube_detail.fetch_video_detail_responses(
            list(record_ids_by_video_id),
            api_key,
        )
    except Exception as exc:  # noqa: BLE001 - keep crawler failures summarized.
        detail_failures += sum(
            len(record_ids) for record_ids in record_ids_by_video_id.values()
        )
        print(f"Failed to fetch video details: {exc}", file=sys.stderr)
        return DetailCrawlResult(
            record_ids_by_video_id=record_ids_by_video_id,
            detail_items_by_video_id={},
            saved=detail_saved,
            failures=detail_failures,
        )

    detail_items_by_video_id = map_video_detail_items_by_video_id(detail_responses)
    for video_id, record_ids in record_ids_by_video_id.items():
        detail_item = detail_items_by_video_id.get(video_id)
        if detail_item is None:
            detail_failures += len(record_ids)
            print(
                f"Missing video detail for video_id {video_id}.",
                file=sys.stderr,
            )

    saved, failures = save_youtube_detail_items(
        record_ids_by_video_id=record_ids_by_video_id,
        detail_items_by_video_id=detail_items_by_video_id,
    )
    detail_saved += saved
    detail_failures += failures

    return DetailCrawlResult(
        record_ids_by_video_id=record_ids_by_video_id,
        detail_items_by_video_id=detail_items_by_video_id,
        saved=detail_saved,
        failures=detail_failures,
    )


def save_youtube_detail_items(
    *,
    record_ids_by_video_id: dict[str, list[int]],
    detail_items_by_video_id: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    detail_successes = 0
    detail_failures = 0

    for video_id, record_ids in record_ids_by_video_id.items():
        detail_item = detail_items_by_video_id.get(video_id)
        if detail_item is None:
            continue

        for video_pk in record_ids:
            try:
                with core.session_scope() as session:
                    videos_detail.create_or_update_video_detail(
                        session,
                        video_ref_id=video_pk,
                        item=detail_item,
                    )
                detail_successes += 1
            except Exception as exc:  # noqa: BLE001 - keep processing rows.
                detail_failures += 1
                print(
                    f"Failed to save video detail for video row {video_pk}: {exc}",
                    file=sys.stderr,
                )

    return detail_successes, detail_failures


def save_youtube_embed_codes(
    detail_result: DetailCrawlResult,
) -> tuple[int, int]:
    embed_successes = 0
    embed_failures = 0

    for video_id, record_ids in detail_result.record_ids_by_video_id.items():
        detail_item = detail_result.detail_items_by_video_id.get(video_id)
        if detail_item is None:
            continue

        embed_code = videos.extract_embed_code(detail_item)
        for video_pk in record_ids:
            try:
                with core.session_scope() as session:
                    videos.update_video_embed_code(
                        session,
                        id=video_pk,
                        embed_code=embed_code,
                    )
                embed_successes += 1
            except Exception as exc:  # noqa: BLE001 - keep processing rows.
                embed_failures += 1
                print(
                    f"Failed to save video embed code for video row {video_pk}: {exc}",
                    file=sys.stderr,
                )

    return embed_successes, embed_failures

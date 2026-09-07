from __future__ import annotations

import argparse
import random
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ytcrawl.config import ConfigError, get_config
from ytcrawl.crawl.details import group_video_record_ids_by_video_id
from ytcrawl.db import core, video_download_attempts, videos
from ytcrawl.download.errors import (
    LIVE_VIDEO_EXCLUDED_ERROR_TYPE,
    LiveVideoExcludedError,
)
from ytcrawl.download.youtube import (
    DOWNLOADER_LABEL,
    DOWNLOAD_FORMAT,
    YouTubeDownloadError,
    download as download_youtube,
)

DOWNLOAD_SLEEP_SECONDS_RANGE = (60.0, 180.0)
DOWNLOAD_BATCH_SIZE = 50
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
BOT_CHECK_ERROR_TYPE = "bot_check_required"
HTTP_403_ERROR_TYPE = "http_403_forbidden"
HTTP_429_ERROR_TYPE = "http_429_too_many_requests"
DEFERRED_AFTER_SAFETY_STOP_ERROR_TYPE = "deferred_after_safety_stop"
USER_DECLINED_CONTINUATION_ERROR_TYPE = "user_declined_continuation"
SAFETY_STOP_ERROR_TYPES = frozenset(
    {BOT_CHECK_ERROR_TYPE, HTTP_403_ERROR_TYPE, HTTP_429_ERROR_TYPE}
)
TERMINAL_ERROR_TYPES = SAFETY_STOP_ERROR_TYPES

ContinuationPrompt = Callable[[int, int], bool]


@dataclass(frozen=True)
class DownloadCrawlResult:
    successes: int = 0
    failures: int = 0
    live_skipped: int = 0
    user_declined: int = 0
    deferred: int = 0
    attempted_unique_videos: int = 0
    remaining_unique_videos: int = 0
    halt_error_type: str | None = None
    batch_limit_reached: bool = False

    @property
    def total_failures(self) -> int:
        return self.failures + self.user_declined


def clean_download_error_message(exc: Exception) -> str:
    return ANSI_ESCAPE_PATTERN.sub("", str(exc))


def _coerce_http_status(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        status = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


def _exception_http_status(exc: Exception) -> int | None:
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))

        for attribute in ("http_status", "status", "status_code", "code"):
            status = _coerce_http_status(getattr(current, attribute, None))
            if status is not None:
                return status

        response = getattr(current, "response", None)
        if response is not None:
            for attribute in ("status", "status_code"):
                status = _coerce_http_status(getattr(response, attribute, None))
                if status is not None:
                    return status

        for nested in (
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
            getattr(current, "cause", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return None


def classify_download_error(exc: Exception) -> str:
    if isinstance(exc, LiveVideoExcludedError):
        return LIVE_VIDEO_EXCLUDED_ERROR_TYPE

    http_status = _exception_http_status(exc)
    if http_status == 403:
        return HTTP_403_ERROR_TYPE
    if http_status == 429:
        return HTTP_429_ERROR_TYPE

    message = clean_download_error_message(exc).lower()
    if (
        "sign in to confirm" in message
        and "not a bot" in message
    ) or "--cookies-from-browser" in message or "--cookies" in message:
        return BOT_CHECK_ERROR_TYPE
    if (
        "http error 403" in message
        or "403: forbidden" in message
        or re.search(r"\bhttp(?: status)?\s+403\b", message)
    ):
        return HTTP_403_ERROR_TYPE
    if (
        "http error 429" in message
        or "429: too many requests" in message
        or re.search(r"\bhttp(?: status)?\s+429\b", message)
        or re.search(r"\bstatus code:?\s*429\b", message)
    ):
        return HTTP_429_ERROR_TYPE
    if ("bytes read" in message and "more expected" in message) or (
        "content too short" in message
    ):
        return "incomplete_read"
    if isinstance(exc, YouTubeDownloadError):
        return "download_error"
    return "unexpected_error"


def sleep_before_next_download() -> None:
    sleep_time = random.uniform(*DOWNLOAD_SLEEP_SECONDS_RANGE)
    print(f"Sleeping for {sleep_time:.2f} seconds before next download...")
    time.sleep(sleep_time)


def create_download_attempts(
    session,
    record_ids: list[int],
) -> list[int]:
    attempt_ids: list[int] = []
    for video_pk in record_ids:
        attempt = video_download_attempts.create_download_attempt(
            session,
            video_ref_id=video_pk,
            downloader=DOWNLOADER_LABEL,
            format_selector=DOWNLOAD_FORMAT,
        )
        attempt_ids.append(attempt.id)
    return attempt_ids


def mark_download_attempts_failed(
    session,
    attempt_ids: list[int],
    *,
    error_type: str,
    error_message: str,
) -> None:
    for attempt_id in attempt_ids:
        video_download_attempts.mark_download_attempt_failed(
            session,
            id=attempt_id,
            error_type=error_type,
            error_message=error_message,
        )


def record_unrequested_downloads(
    video_items: Sequence[tuple[str, list[int]]],
    *,
    error_type: str,
    error_message: str,
) -> int:
    recorded = 0
    if not video_items:
        return recorded

    with core.session_scope() as session:
        for _, record_ids in video_items:
            attempt_ids = create_download_attempts(session, record_ids)
            mark_download_attempts_failed(
                session,
                attempt_ids,
                error_type=error_type,
                error_message=error_message,
            )
            recorded += len(record_ids)
    return recorded


def record_live_video_exclusions(
    video_records: tuple[videos.VideoRecord, ...],
    *,
    status_by_video_id: dict[str, str],
) -> int:
    if not video_records:
        return 0
    record_ids_by_video_id = group_video_record_ids_by_video_id(video_records)
    recorded = 0
    with core.session_scope() as session:
        for video_id, record_ids in record_ids_by_video_id.items():
            live_status = status_by_video_id.get(video_id, "live")
            error_message = (
                f"YouTube Data API reported liveBroadcastContent={live_status} "
                f"for {video_id}; no media download request was made."
            )
            attempt_ids = create_download_attempts(session, record_ids)
            mark_download_attempts_failed(
                session,
                attempt_ids,
                error_type=LIVE_VIDEO_EXCLUDED_ERROR_TYPE,
                error_message=error_message,
            )
            recorded += len(record_ids)
            print(
                f"Skipping live video {video_id}: "
                f"liveBroadcastContent={live_status}; "
                "no media download request was made.",
                file=sys.stderr,
            )
    return recorded


def prompt_for_next_batch(
    processed_unique_videos: int,
    remaining_unique_videos: int,
) -> bool:
    print(
        f"Processed {processed_unique_videos} unique videos; "
        f"{remaining_unique_videos} remain."
    )
    try:
        response = input("Continue downloading the next batch? [y/N] ")
    except EOFError:
        print(
            "No input was available; continuation was declined. "
            "No network request will be made for the remaining videos.",
            file=sys.stderr,
        )
        return False
    return response.strip().lower() in {"y", "yes"}


def crawl_youtube_videos(
    output_dir: str | Path,
    video_records: tuple[videos.VideoRecord, ...],
    *,
    batch_size: int = DOWNLOAD_BATCH_SIZE,
    continuation_prompt: ContinuationPrompt | None = None,
) -> DownloadCrawlResult:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    output_root = Path(output_dir).expanduser().resolve()
    record_ids_by_video_id = group_video_record_ids_by_video_id(video_records)
    missing_video_id_count = len(video_records) - sum(
        len(record_ids) for record_ids in record_ids_by_video_id.values()
    )
    for record in video_records:
        if not record.video_id:
            print(
                f"Skipping video row {record.id}: missing video_id.",
                file=sys.stderr,
            )

    failures = missing_video_id_count
    successes = 0
    live_skipped = 0
    user_declined = 0
    deferred = 0
    attempted_unique_videos = 0
    remaining_unique_videos = 0
    halt_error_type: str | None = None
    batch_limit_reached = False
    video_items = list(record_ids_by_video_id.items())
    for item_index, (video_id, record_ids) in enumerate(video_items):
        if item_index > 0 and item_index % batch_size == 0:
            batch_limit_reached = True
            remaining_items = video_items[item_index:]
            remaining_unique_videos = len(remaining_items)
            if continuation_prompt is None:
                break
            if not continuation_prompt(
                attempted_unique_videos,
                remaining_unique_videos,
            ):
                declined_message = (
                    "No network request was made because the user declined "
                    f"continuation after {attempted_unique_videos} unique videos."
                )
                user_declined = record_unrequested_downloads(
                    remaining_items,
                    error_type=USER_DECLINED_CONTINUATION_ERROR_TYPE,
                    error_message=declined_message,
                )
                print(
                    f"Continuation declined; {remaining_unique_videos} unique "
                    "videos were not requested from the network.",
                    file=sys.stderr,
                )
                break
            remaining_unique_videos = 0

        if item_index > 0:
            sleep_before_next_download()

        with core.session_scope() as session:
            attempt_ids = create_download_attempts(session, record_ids)
        attempted_unique_videos += 1

        try:
            downloaded_path = Path(
                download_youtube(video_id, output_root, overwrite=False)
            ).resolve()
            stored_path = downloaded_path.relative_to(output_root).as_posix()
        except Exception as exc:  # noqa: BLE001 - classify before deciding whether to stop.
            error_type = classify_download_error(exc)
            error_message = clean_download_error_message(exc)
            with core.session_scope() as session:
                mark_download_attempts_failed(
                    session,
                    attempt_ids,
                    error_type=error_type,
                    error_message=error_message,
                )
            if error_type == LIVE_VIDEO_EXCLUDED_ERROR_TYPE:
                live_skipped += len(record_ids)
                print(
                    f"Skipping live video download for {video_id}: {exc}",
                    file=sys.stderr,
                )
                continue

            failures += len(record_ids)
            print(f"Failed to download {video_id}: {exc}", file=sys.stderr)
            if error_type in SAFETY_STOP_ERROR_TYPES:
                remaining_items = video_items[item_index + 1 :]
                remaining_unique_videos = len(remaining_items)
                halt_error_type = error_type
                deferred_message = (
                    "No network request was made because downloads were deferred "
                    f"after {error_type} occurred on {video_id}: {error_message}"
                )
                deferred = record_unrequested_downloads(
                    remaining_items,
                    error_type=DEFERRED_AFTER_SAFETY_STOP_ERROR_TYPE,
                    error_message=deferred_message,
                )
                print(
                    f"Safety stop activated by {error_type}; "
                    f"{remaining_unique_videos} unique videos were deferred "
                    "without network requests.",
                    file=sys.stderr,
                )
                break
            continue

        file_size_bytes = (
            downloaded_path.stat().st_size if downloaded_path.is_file() else None
        )
        with core.session_scope() as session:
            for attempt_id in attempt_ids:
                video_download_attempts.mark_download_attempt_succeeded(
                    session,
                    id=attempt_id,
                    file_size_bytes=file_size_bytes,
                )
            for video_pk in record_ids:
                videos.update_video_path(
                    session, id=video_pk, path=stored_path
                )
        successes += len(record_ids)

    return DownloadCrawlResult(
        successes=successes,
        failures=failures,
        live_skipped=live_skipped,
        user_declined=user_declined,
        deferred=deferred,
        attempted_unique_videos=attempted_unique_videos,
        remaining_unique_videos=remaining_unique_videos,
        halt_error_type=halt_error_type,
        batch_limit_reached=batch_limit_reached,
    )


def crawl_missing_youtube_videos(
    output_dir: str | Path,
    *,
    continuation_prompt: ContinuationPrompt | None = None,
) -> DownloadCrawlResult:
    with core.session_scope() as session:
        video_records = videos.find_video_records_needing_download(session)
    return crawl_youtube_videos(
        output_dir,
        video_records,
        continuation_prompt=continuation_prompt,
    )
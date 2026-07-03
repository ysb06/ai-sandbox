from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path

from ytcrawl.crawl.details import group_video_record_ids_by_video_id
from ytcrawl.db import core, video_download_attempts, videos
from ytcrawl.download.youtube import (
    DOWNLOADER_LABEL,
    DOWNLOAD_FORMAT,
    YouTubeDownloadError,
    download as download_youtube,
)

DOWNLOAD_SLEEP_SECONDS_RANGE = (10.0, 15.0)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
BOT_CHECK_ERROR_TYPE = "bot_check_required"


def clean_download_error_message(exc: Exception) -> str:
    return ANSI_ESCAPE_PATTERN.sub("", str(exc))


def classify_download_error(exc: Exception) -> str:
    message = clean_download_error_message(exc).lower()
    if (
        "sign in to confirm" in message
        and "not a bot" in message
    ) or "--cookies-from-browser" in message or "--cookies" in message:
        return BOT_CHECK_ERROR_TYPE
    if "http error 403" in message or "403: forbidden" in message:
        return "http_403_forbidden"
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


def crawl_youtube_videos(
    output_dir: str,
    video_records: tuple[videos.VideoRecord, ...],
) -> tuple[int, int]:
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
    video_items = list(record_ids_by_video_id.items())
    for item_index, (video_id, record_ids) in enumerate(video_items):
        if item_index > 0:
            sleep_before_next_download()

        with core.session_scope() as session:
            attempt_ids = create_download_attempts(session, record_ids)

        try:
            downloaded_path = Path(
                download_youtube(video_id, output_dir, overwrite=False)
            ).resolve()
        except Exception as exc:  # noqa: BLE001 - keep crawling remaining videos.
            error_type = classify_download_error(exc)
            error_message = clean_download_error_message(exc)
            with core.session_scope() as session:
                mark_download_attempts_failed(
                    session,
                    attempt_ids,
                    error_type=error_type,
                    error_message=error_message,
                )
            failures += len(record_ids)
            print(f"Failed to download {video_id}: {exc}", file=sys.stderr)
            if error_type == BOT_CHECK_ERROR_TYPE:
                remaining_items = video_items[item_index + 1 :]
                skipped_message = (
                    f"Skipped after bot check on {video_id}: {error_message}"
                )
                with core.session_scope() as session:
                    for _, skipped_record_ids in remaining_items:
                        skipped_attempt_ids = create_download_attempts(
                            session,
                            skipped_record_ids,
                        )
                        mark_download_attempts_failed(
                            session,
                            skipped_attempt_ids,
                            error_type=BOT_CHECK_ERROR_TYPE,
                            error_message=skipped_message,
                        )
                        failures += len(skipped_record_ids)
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
                    session, id=video_pk, path=str(downloaded_path)
                )
        successes += len(record_ids)

    return (successes, failures)

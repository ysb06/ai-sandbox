from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ytcrawl.config import ConfigError, get_config
from ytcrawl.crawl.download import crawl_youtube_videos
from ytcrawl.db import core, video_download_attempts, videos


def find_failed_video_records(
    session: Session,
) -> tuple[videos.VideoRecord, ...]:
    """Return videos whose latest download attempt is a completed failure."""
    attempt = video_download_attempts.VideoDownloadAttempt
    video = videos.Video
    latest_attempts = (
        select(
            attempt.video_ref_id.label("video_ref_id"),
            func.max(attempt.id).label("attempt_id"),
        )
        .group_by(attempt.video_ref_id)
        .subquery()
    )

    rows = session.execute(
        select(video.id, video.video_id)
        .join(latest_attempts, latest_attempts.c.video_ref_id == video.id)
        .join(attempt, attempt.id == latest_attempts.c.attempt_id)
        .where(
            or_(video.path.is_(None), video.path == ""),
            attempt.finished_at.is_not(None),
            attempt.error_type.is_not(None),
            attempt.error_type != "",
        )
        .order_by(video.id)
    ).all()
    return tuple(
        videos.VideoRecord(id=video_ref_id, video_id=youtube_video_id)
        for video_ref_id, youtube_video_id in rows
    )


def main() -> int:
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not config.db_path.is_file():
        print(f"Database file not found: {config.db_path}", file=sys.stderr)
        return 2

    try:
        core.configure(config.db_url)
        with core.session_scope() as session:
            video_records = find_failed_video_records(session)

        if not video_records:
            print("No failed downloads to recover.")
            return 0

        successes, failures = crawl_youtube_videos(config.media_root, video_records)
    except Exception as exc:  # noqa: BLE001 - report CLI recovery failures.
        print(f"Recovery error: {exc}", file=sys.stderr)
        return 1

    print(f"Recovered {successes} videos, failed {failures}.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

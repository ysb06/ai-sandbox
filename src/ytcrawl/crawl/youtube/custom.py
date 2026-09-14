"""Download a preselected JSON list without creating search records."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import select

from ytcrawl.config import AppConfig, ConfigError, get_config
from ytcrawl.crawl import download
from ytcrawl.db import core, video_download_attempts, videos, videos_detail
from ytcrawl.db.custom_migration import ensure_optional_search_id
from ytcrawl.download.cookies import add_browser_cookie_argument
from ytcrawl.download.youtube import VIDEO_ID_PATTERN


def load_candidates(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array.")
    unique: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(data, 1):
        prefix = f"JSON item {index}"
        if not isinstance(item, dict):
            raise ValueError(f"{prefix}: expected an object.")
        video_id = item.get("video_id")
        if not isinstance(video_id, str) or not VIDEO_ID_PATTERN.fullmatch(video_id):
            raise ValueError(f"{prefix}: invalid video_id {video_id!r}.")
        for field in ("title", "description", "published_at", "default_audio_language", "license"):
            value = item.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{prefix}: {field} must be a string or null.")
        duration = item.get("duration_seconds")
        if duration is not None and (
            isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0
        ):
            raise ValueError(f"{prefix}: duration_seconds must be finite and nonnegative.")
        marker = item.get("contains_synthetic_media")
        if marker is not None and not isinstance(marker, bool):
            raise ValueError(f"{prefix}: contains_synthetic_media must be boolean or null.")
        # Also reject non-standard JSON NaN/Infinity in metadata retained verbatim.
        try:
            json.dumps(item, allow_nan=False)
        except ValueError as exc:
            raise ValueError(f"{prefix}: {exc}") from exc
        unique.setdefault(video_id, item)
    return list(unique.values())


def _merge_metadata(session, video: videos.Video, item: dict[str, Any], source: Path) -> None:
    for field, key in (("title", "title"), ("description", "description"), ("publishTime", "published_at")):
        if getattr(video, field) in (None, ""):
            setattr(video, field, item.get(key))
    detail = videos_detail.find_video_detail_for_video(session, video_ref_id=video.id)
    if detail is None:
        detail = videos_detail.VideoDetail(
            video_ref_id=video.id, tags=[], rating=[], other_info=[], raw={}
        )
        session.add(detail)
    duration = item.get("duration_seconds")
    values = {
        "duration": f"PT{format(Decimal(str(duration)).normalize(), 'f')}S" if duration is not None else None,
        "audio_language": item.get("default_audio_language"),
        "license": item.get("license"),
        "is_synthetic_marked": item.get("contains_synthetic_media"),
    }
    for field, value in values.items():
        if getattr(detail, field) in (None, ""):
            setattr(detail, field, value)
    raw = dict(detail.raw or {})
    raw["custom_import"] = {"source_file": str(source), "candidate": item}
    detail.raw = raw


def register_candidates(items: list[dict[str, Any]], source: Path) -> tuple[videos.VideoRecord, ...]:
    records = []
    with core.session_scope() as session:
        for item in items:
            rows = list(session.scalars(
                select(videos.Video).where(videos.Video.video_id == item["video_id"])
                .order_by(videos.Video.id)
            ))
            if not rows:
                video = videos.Video(video_id=item["video_id"], search_id=None)
                session.add(video)
                session.flush()
                rows = [video]
            for video in rows:
                _merge_metadata(session, video, item, source)
                records.append(videos.VideoRecord(video.id, video.video_id))
    return tuple(records)


def _completed_file(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Media path outside configured media root: {path}")
    return (
        resolved.is_file() and resolved.stat().st_size > 0
        and resolved.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".flv", ".avi"}
        and not any(part in resolved.name for part in (".part", ".temp"))
        and not re.search(r"\.f\d+\.", resolved.name)
    )


def select_downloads(records: tuple[videos.VideoRecord, ...], root: Path) -> tuple[tuple[videos.VideoRecord, ...], int]:
    root = root.expanduser().resolve()
    grouped: dict[str, list[videos.VideoRecord]] = {}
    for record in records:
        grouped.setdefault(record.video_id, []).append(record)
    pending = []
    reused = 0
    with core.session_scope() as session:
        for video_id, group in grouped.items():
            rows = [session.get(videos.Video, record.id) for record in group]
            candidates = [root / row.path for row in rows if row.path]
            # Recover completed output after an interruption between file and DB writes.
            candidates.extend(root / f"vid_{video_id}{ext}" for ext in (".mp4", ".mkv", ".webm", ".mov", ".flv", ".avi"))
            existing = next((path.resolve() for path in candidates if _completed_file(path, root)), None)
            if existing is None:
                pending.extend(group)
                continue
            reused += 1
            for row in rows:
                row.path = existing.relative_to(root).as_posix()
                previous = video_download_attempts.find_latest_attempt_for_video(session, video_ref_id=row.id)
                if previous is None or previous.finished_at is None or previous.error_type or previous.file_size_bytes != existing.stat().st_size:
                    attempt = video_download_attempts.create_download_attempt(
                        session, video_ref_id=row.id, downloader="local-file-reuse",
                        format_selector=download.DOWNLOAD_FORMAT,
                    )
                    video_download_attempts.mark_download_attempt_succeeded(
                        session, id=attempt.id, file_size_bytes=existing.stat().st_size,
                    )
    return tuple(pending), reused


def run_custom(items: list[dict[str, Any]], source: Path, config: AppConfig, *, cookies_from_browser: str | None = None) -> int:
    if not items:
        print("Total 0 unique videos; nothing to download.")
        return 0
    backup = ensure_optional_search_id(config.db_path, config.backup_root)
    if backup:
        print(f"Migrated optional search_id; database backup: {backup}")
    core.configure(config.db_url)
    core.create_all()
    records = register_candidates(items, source)
    pending, reused = select_downloads(records, config.media_root)
    result = download.crawl_youtube_videos(
        config.media_root, pending,
        batch_size=max(1, len({record.video_id for record in pending})),
        cookies_from_browser=cookies_from_browser,
    ) if pending else download.DownloadCrawlResult()
    print(
        f"Total {len(items)} unique videos; reused {reused}, "
        f"downloaded {result.successful_unique_videos}, failed {result.failed_unique_videos}, "
        f"live excluded {result.live_skipped_unique_videos}, "
        f"remaining {result.remaining_unique_videos}."
    )
    return int(bool(result.total_failures or result.remaining_unique_videos or result.halt_error_type))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, type=Path, dest="json_path")
    add_browser_cookie_argument(parser)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = args.json_path.expanduser().resolve()
        items = load_candidates(source)
        config = get_config()
    except (OSError, ValueError, ConfigError) as exc:
        print(f"Input/configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        return run_custom(items, source, config, cookies_from_browser=args.cookies_from_browser)
    except KeyboardInterrupt:
        print("Interrupted; rerun the same command to resume missing files.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Custom download failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
수집된 video 테이블에 나타난 영상들의 세부 정보를 수집하는 모듈
"""

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from googleapiclient.errors import HttpError
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ytcrawln.api.youtube import videos as youtube_videos
from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.db import core
from ytcrawln.db.tables.videos import Video
from ytcrawln.db.tables.videos_detail import VideoDetail

VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")


class DetailImportValidationError(ValueError):
    """Configuration or database prerequisites are missing."""


@dataclass
class DetailImportResult:
    missing_rows: int = 0
    unique_video_ids: int = 0
    inserted_rows: int = 0
    skipped_rows: int = 0
    invalid_id_rows: int = 0
    unreturned_rows: int = 0
    failed_rows: int = 0
    unprocessed_rows: int = 0

    @property
    def exit_code(self) -> int:
        return int(
            bool(
                self.invalid_id_rows
                or self.unreturned_rows
                or self.failed_rows
                or self.unprocessed_rows
            )
        )


def find_videos_without_details(session: Session) -> list[tuple[int, str | None]]:
    has_detail = (
        select(VideoDetail.id).where(VideoDetail.video_ref_id == Video.id).exists()
    )
    rows = session.execute(
        select(Video.id, Video.video_id).where(~has_detail).order_by(Video.id)
    )
    return [(row.id, row.video_id) for row in rows]


def _object(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {key} object.")
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    # Counts must fit SQLite's signed 64-bit INTEGER representation.
    return parsed if 0 <= parsed <= 2**63 - 1 else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _strings(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def extract_video_detail_values(item: dict[str, Any]) -> dict[str, Any]:
    """Convert an actual API item; absent optional properties stay unknown."""
    if not isinstance(item.get("snippet"), dict) or not isinstance(
        item.get("contentDetails"), dict
    ):
        raise ValueError("Video detail requires snippet and contentDetails objects.")
    snippet = item["snippet"]
    content = item["contentDetails"]
    status = _object(item, "status")
    statistics = _object(item, "statistics")
    recording = _object(item, "recordingDetails")
    topics = _object(item, "topicDetails")
    player = _object(item, "player")
    ratings = _object(content, "contentRating")
    rating = [
        f"{str(key).removesuffix('Rating').lower()}-{str(value).lower()}"
        for key, value in ratings.items()
        if value not in (None, "")
    ]
    if status.get("madeForKids") is True:
        rating.append("youtube-kids")
    synthetic = status.get("containsSyntheticMedia")
    location = recording.get("location")
    return {
        "duration": _text(content.get("duration")),
        "resolution": _text(content.get("definition")),
        "has_caption": _boolean(content.get("caption")),
        "tags": _strings(snippet.get("tags")),
        "language": _text(snippet.get("defaultLanguage")),
        "audio_language": _text(snippet.get("defaultAudioLanguage")),
        "license": _text(status.get("license")),
        "view_count": _integer(statistics.get("viewCount")),
        "like_count": _integer(statistics.get("likeCount")),
        "comment_count": _integer(statistics.get("commentCount")),
        "recording_date": _text(recording.get("recordingDate")),
        "location": location if isinstance(location, dict) else None,
        "rating": rating,
        "other_info": _strings(topics.get("topicCategories")),
        "is_synthetic_marked": synthetic if isinstance(synthetic, bool) else None,
        "embed_code": _text(player.get("embedHtml")),
        "raw": item,
    }


def _map_response(response: Any, video_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise ValueError("Expected a video list response with an items array.")
    expected = set(video_ids)
    items = {}
    for item in response["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Invalid video item ID.")
        video_id = item["id"]
        if video_id not in expected or video_id in items:
            raise ValueError("Unexpected or duplicate video item ID.")
        items[video_id] = item
    return items


def _save_details(
    session: Session,
    grouped: dict[str, list[int]],
    values: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    inserted = skipped = failed = 0
    # Keep SQL IN clauses bounded even when one YouTube ID has many local rows.
    for video_id, detail_values in values.items():
        refs = grouped[video_id]
        for start in range(0, len(refs), 500):
            batch = refs[start : start + 500]
            current = dict(
                session.execute(
                    select(Video.id, Video.video_id).where(Video.id.in_(batch))
                ).all()
            )
            existing = set(
                session.scalars(
                    select(VideoDetail.video_ref_id).where(
                        VideoDetail.video_ref_id.in_(batch)
                    )
                )
            )
            for ref_id in batch:
                if current.get(ref_id) != video_id:
                    failed += 1
                    print(
                        f"Video row {ref_id} was removed or changed; skipped.",
                        file=sys.stderr,
                    )
                elif ref_id in existing:
                    skipped += 1
                else:
                    session.add(VideoDetail(video_ref_id=ref_id, **detail_values))
                    inserted += 1
    session.flush()
    return inserted, skipped, failed


def fill_missing_video_details(
    api_key: str | None,
    *,
    session_factory: sessionmaker[Session],
) -> DetailImportResult:
    """Fill absent detail rows; each batch commits before the next API request."""
    with session_factory() as session:
        rows = find_videos_without_details(session)
    result = DetailImportResult(missing_rows=len(rows))
    grouped: dict[str, list[int]] = {}
    for ref_id, video_id in rows:
        if (
            not isinstance(video_id, str)
            or VIDEO_ID_PATTERN.fullmatch(video_id) is None
        ):
            result.invalid_id_rows += 1
            print(
                f"Video row {ref_id} has an invalid or empty YouTube ID.",
                file=sys.stderr,
            )
        else:
            grouped.setdefault(video_id, []).append(ref_id)
    result.unique_video_ids = len(grouped)
    result.unprocessed_rows = sum(map(len, grouped.values()))
    if not grouped:
        return result
    if not api_key or not api_key.strip():
        raise DetailImportValidationError("YOUTUBE_API_KEY is required.")

    youtube = youtube_videos.create_youtube_client(api_key)

    video_ids = list(grouped)
    size = youtube_videos.MAX_VIDEO_IDS_PER_REQUEST
    try:
        for start in range(0, len(video_ids), size):
            batch_ids = video_ids[start : start + size]
            batch_rows = sum(len(grouped[video_id]) for video_id in batch_ids)
            response = youtube_videos.fetch_video_detail_response(
                batch_ids, youtube_client=youtube
            )
            
            try:
                items = _map_response(response, batch_ids)
            except ValueError as exc:
                print(f"Invalid API response: {exc}", file=sys.stderr)
                result.failed_rows += batch_rows
                result.unprocessed_rows -= batch_rows
                break

            values = {}
            unreturned = invalid_response = 0
            for video_id in batch_ids:
                if video_id not in items:
                    unreturned += len(grouped[video_id])
                    print(f"No API item returned for {video_id}.", file=sys.stderr)
                    continue
                try:
                    values[video_id] = extract_video_detail_values(items[video_id])
                except ValueError as exc:
                    invalid_response += len(grouped[video_id])
                    print(f"Invalid detail for {video_id}: {exc}", file=sys.stderr)

            try:
                with session_factory.begin() as session:
                    inserted, skipped, failed = _save_details(session, grouped, values)
            except SQLAlchemyError as exc:
                print(
                    f"Database batch error: {type(exc).__name__}; batch rolled back.",
                    file=sys.stderr,
                )
                result.failed_rows += batch_rows
                result.unprocessed_rows -= batch_rows
                break
            result.inserted_rows += inserted
            result.skipped_rows += skipped
            result.unreturned_rows += unreturned
            result.failed_rows += failed + invalid_response
            result.unprocessed_rows -= batch_rows
    finally:
        try:
            youtube.close()
        except Exception:
            pass  # Client cleanup must not hide committed results or the original error.
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Import missing YouTube video details, preserving existing details."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Configuration file (default: project config_ytcrawln.yaml).",
    )
    args = parser.parse_args(argv)

    try:
        config = get_config(args.config)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not config.db_path.is_file():
        print(f"Database file not found: {config.db_path}", file=sys.stderr)
        return 2

    if env is None:
        load_dotenv()
        env = os.environ

    try:
        core.configure(config.db_url)
        factory = sessionmaker(bind=core.get_engine(), expire_on_commit=False)
        result = fill_missing_video_details(
            env.get("YOUTUBE_API_KEY"), session_factory=factory
        )
    except DetailImportValidationError as exc:
        print(f"Import validation error: {exc}", file=sys.stderr)
        return 2
    except (OSError, SQLAlchemyError, RuntimeError) as exc:
        print(f"Detail import error: {type(exc).__name__}", file=sys.stderr)
        return 1

    for label, value in (
        ("Missing detail rows", result.missing_rows),
        ("Unique YouTube video IDs", result.unique_video_ids),
        ("Inserted detail rows", result.inserted_rows),
        ("Skipped existing rows", result.skipped_rows),
        ("Invalid video ID rows", result.invalid_id_rows),
        ("Unreturned video ID rows", result.unreturned_rows),
        ("Failed rows", result.failed_rows),
        ("Unprocessed rows", result.unprocessed_rows),
    ):
        print(f"{label}: {value}")

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

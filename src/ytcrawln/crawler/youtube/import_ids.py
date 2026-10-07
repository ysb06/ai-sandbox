"""Register new YouTube videos and their details from an ID list."""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ytcrawln.api.youtube import videos as youtube_videos
from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.crawler.youtube.details import (
    extract_video_detail_values,
    map_video_detail_response,
)
from ytcrawln.db.tables.videos import Videos
from ytcrawln.db.tables.videos_detail import VideoDetail
from ytcrawln.video_ids import VIDEO_ID_PATTERN as VIDEO_ID_PATTERN
from ytcrawln.video_ids import VideoIDValidationError
from ytcrawln.video_ids import read_video_ids as _read_video_ids
from ytcrawln.video_ids import unique_video_ids

DB_LOOKUP_BATCH_SIZE = 500


class VideoImportValidationError(ValueError):
    """Input or database prerequisites prevent registration."""


class ExistingVideoIDsError(VideoImportValidationError):
    def __init__(self, video_ids: Sequence[str]) -> None:
        self.video_ids = tuple(video_ids)
        super().__init__("Video IDs already exist in the database: " + ", ".join(video_ids))


@dataclass
class VideoImportResult:
    """Counts use input IDs, except the two committed database row counts."""

    input_ids: int = 0
    unique_ids: int = 0
    duplicate_ids: int = 0
    inserted_videos: int = 0
    inserted_details: int = 0
    unreturned_ids: int = 0
    failed_ids: int = 0
    unprocessed_ids: int = 0
    conflicting_ids: tuple[str, ...] = ()
    interrupted: bool = False

    @property
    def exit_code(self) -> int:
        if self.interrupted:
            return 130
        if self.conflicting_ids:
            return 2
        return int(bool(self.unreturned_ids or self.failed_ids or self.unprocessed_ids))


def read_video_ids(path: str | Path) -> list[str]:
    """Read and validate every nonblank line, retaining duplicates for counting."""
    try:
        return _read_video_ids(path)
    except VideoIDValidationError as exc:
        raise VideoImportValidationError(str(exc)) from exc


def _prepare_ids(video_ids: Sequence[str]) -> tuple[list[str], VideoImportResult]:
    try:
        unique = unique_video_ids(video_ids)
    except VideoIDValidationError as exc:
        raise VideoImportValidationError(str(exc)) from exc
    return unique, VideoImportResult(
        input_ids=len(video_ids),
        unique_ids=len(unique),
        duplicate_ids=len(video_ids) - len(unique),
        unprocessed_ids=len(unique),
    )


def _check_schema(session: Session) -> None:
    inspector = inspect(session.connection())
    for model in (Videos, VideoDetail):
        table = model.__table__
        if not inspector.has_table(table.name):
            raise VideoImportValidationError(f"Database has no {table.name} table.")
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        missing = set(table.columns.keys()) - columns
        if missing:
            raise VideoImportValidationError(
                f"Database {table.name} table is missing columns: "
                + ", ".join(sorted(missing))
            )


def _reject_existing_ids(session: Session, video_ids: Sequence[str]) -> None:
    existing: set[str] = set()
    for start in range(0, len(video_ids), DB_LOOKUP_BATCH_SIZE):
        existing.update(
            session.scalars(
                select(Videos.video_id).where(
                    Videos.video_id.in_(video_ids[start : start + DB_LOOKUP_BATCH_SIZE])
                )
            )
        )
    if existing:
        raise ExistingVideoIDsError([value for value in video_ids if value in existing])


def _video_values(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item["snippet"]  # Validated by extract_video_detail_values first.
    values = {
        "kind": item.get("kind"),
        "etag": item.get("etag"),
        "video_id": item["id"],
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "publishTime": snippet.get("publishedAt"),
        "path": f"vid_{item['id']}.mp4",
    }
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Invalid {key}: expected text.")
    return values


def _report_error(stage: str, exc: Exception) -> None:
    # API exception strings may include request URLs containing the API key.
    status = getattr(getattr(exc, "resp", None), "status", None)
    suffix = f" (HTTP {status})" if isinstance(status, int) else ""
    print(f"{stage}: {type(exc).__name__}{suffix}.", file=sys.stderr)


def import_video_ids(
    video_ids: Sequence[str],
    api_key: str | None,
    *,
    session_factory: sessionmaker[Session],
) -> VideoImportResult:
    """Insert only new video/detail pairs; reject any pre-existing input ID.

    Requires prepared tables and a single registration writer. Completed batches
    survive later failures; rerunning their IDs is an error, not a resume mode.
    """
    ids, result = _prepare_ids(video_ids)
    try:
        with session_factory() as session:
            _check_schema(session)
            _reject_existing_ids(session, ids)
    except SQLAlchemyError as exc:
        raise VideoImportValidationError(
            f"Unable to inspect the database: {type(exc).__name__}."
        ) from exc
    if not api_key or not api_key.strip():
        raise VideoImportValidationError("YOUTUBE_API_KEY is required.")

    youtube = None
    try:
        try:
            youtube = youtube_videos.create_youtube_client(api_key)
        except Exception as exc:
            _report_error("API client initialization failed", exc)
            return result

        size = youtube_videos.MAX_VIDEO_IDS_PER_REQUEST
        for start in range(0, len(ids), size):
            batch = ids[start : start + size]
            try:
                response = youtube_videos.fetch_video_detail_response(
                    batch, youtube_client=youtube
                )
            except Exception as exc:
                _report_error("API request failed; import stopped", exc)
                result.failed_ids += len(batch)
                result.unprocessed_ids -= len(batch)
                break
            try:
                items = map_video_detail_response(response, batch)
            except ValueError as exc:
                print(f"Invalid API response: {exc}", file=sys.stderr)
                result.failed_ids += len(batch)
                result.unprocessed_ids -= len(batch)
                break

            prepared = []
            unreturned = invalid = 0
            for video_id in batch:
                item = items.get(video_id)
                if item is None:
                    unreturned += 1
                    print(f"No API item returned for {video_id}.", file=sys.stderr)
                    continue
                try:
                    detail_values = extract_video_detail_values(item)
                    prepared.append((_video_values(item), detail_values))
                except ValueError as exc:
                    invalid += 1
                    print(f"Invalid video data for {video_id}: {exc}", file=sys.stderr)

            try:
                with session_factory.begin() as session:
                    _reject_existing_ids(session, batch)
                    for video_values, detail_values in prepared:
                        video = Videos(**video_values)
                        session.add(video)
                        session.flush()
                        session.add(VideoDetail(video_ref_id=video.id, **detail_values))
                    session.flush()
            except ExistingVideoIDsError as exc:
                print(f"Import conflict; batch rolled back: {exc}", file=sys.stderr)
                result.conflicting_ids = exc.video_ids
                result.failed_ids += len(batch)
                result.unprocessed_ids -= len(batch)
                break
            except SQLAlchemyError as exc:
                _report_error("Database batch failed; batch rolled back", exc)
                result.failed_ids += len(batch)
                result.unprocessed_ids -= len(batch)
                break

            result.inserted_videos += len(prepared)
            result.inserted_details += len(prepared)
            result.unreturned_ids += unreturned
            result.failed_ids += invalid
            result.unprocessed_ids -= len(batch)
    except KeyboardInterrupt:
        result.interrupted = True
        print("Import interrupted; uncommitted inserts rolled back.", file=sys.stderr)
    finally:
        if youtube is not None:
            try:
                youtube.close()
            except Exception:
                pass  # Cleanup must not hide committed results or an import error.
    return result


def _print_result(result: VideoImportResult) -> None:
    for label, value in (
        ("Input IDs", result.input_ids),
        ("Unique IDs", result.unique_ids),
        ("Duplicate input IDs removed", result.duplicate_ids),
        ("Inserted videos", result.inserted_videos),
        ("Inserted details", result.inserted_details),
        ("Unreturned IDs", result.unreturned_ids),
        ("Failed IDs", result.failed_ids),
        ("Unprocessed IDs", result.unprocessed_ids),
    ):
        print(f"{label}: {value}")


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Register new YouTube videos and details from an ID text file.",
        epilog="Existing input IDs are errors. No downloads or schema changes are performed.",
    )
    parser.add_argument("--ids-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)

    engine = None
    try:
        try:
            ids = read_video_ids(args.ids_file)
            config = get_config(args.config)
            database_path = config.db_path.expanduser().resolve()
            if not database_path.is_file():
                raise VideoImportValidationError(f"Database file not found: {database_path}")
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
            print(f"Import validation error: {exc}", file=sys.stderr)
            return 2

        if env is None:
            load_dotenv()
            env = os.environ

        # mode=rw prevents creating an empty DB even if it disappears after preflight.
        engine = create_engine(
            URL.create(
                "sqlite",
                database=database_path.as_uri(),
                query={"mode": "rw", "uri": "true"},
            )
        )
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        result = import_video_ids(ids, env.get("YOUTUBE_API_KEY"), session_factory=factory)
        _print_result(result)
        return result.exit_code
    except VideoImportValidationError as exc:
        print(f"Import validation error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Import interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        _report_error("Video import error", exc)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

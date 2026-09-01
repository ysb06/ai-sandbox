import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ytcrawl.config import ConfigError, get_config
from ytcrawl.statistics._common import (
    collect_media_aggregate,
    format_decimal as _format_decimal,
    format_duration as _format_duration,
    format_size,
)


@dataclass(frozen=True, slots=True)
class DatasetStatistics:
    """Aggregate statistics for completed media referenced by the database."""

    completed_video_files: int
    total_size_bytes: int
    total_duration_seconds: Decimal
    duration_files: int
    duplicate_db_references_ignored: int
    missing_referenced_files: int
    missing_durations: int
    invalid_durations: int
    conflicting_durations: int


def collect_statistics(
    db_path: str | Path,
    media_root: str | Path,
) -> DatasetStatistics:
    """Read dataset statistics without modifying the SQLite database."""
    selected_db_path = Path(db_path).expanduser().resolve()
    rows = _read_video_references(selected_db_path)
    aggregate = collect_media_aggregate(media_root, rows)

    return DatasetStatistics(
        completed_video_files=aggregate.existing_files,
        total_size_bytes=aggregate.total_size_bytes,
        total_duration_seconds=aggregate.total_duration_seconds,
        duration_files=aggregate.duration_files,
        duplicate_db_references_ignored=aggregate.duplicate_references_ignored,
        missing_referenced_files=aggregate.missing_files,
        missing_durations=aggregate.missing_durations,
        invalid_durations=aggregate.invalid_durations,
        conflicting_durations=aggregate.conflicting_durations,
    )


def format_statistics(statistics: DatasetStatistics) -> str:
    """Format dataset statistics for the human-readable CLI output."""
    return "\n".join(
        (
            f"Completed video files: {statistics.completed_video_files:,}",
            f"Total size: {format_size(statistics.total_size_bytes)}",
            "Total duration: "
            f"{_format_duration(statistics.total_duration_seconds)} "
            f"({_format_decimal(statistics.total_duration_seconds)} seconds)",
            "Duration coverage: "
            f"{statistics.duration_files:,}/"
            f"{statistics.completed_video_files:,} files",
            "Duplicate DB references ignored: "
            f"{statistics.duplicate_db_references_ignored:,}",
            f"Missing referenced files: {statistics.missing_referenced_files:,}",
            f"Missing durations: {statistics.missing_durations:,}",
            f"Invalid durations: {statistics.invalid_durations:,}",
            f"Conflicting durations: {statistics.conflicting_durations:,}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report aggregate size and duration for completed videos."
    )
    parser.parse_args(argv)

    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not config.db_path.is_file():
        print(f"Database file not found: {config.db_path}", file=sys.stderr)
        return 2
    if not config.media_root.is_dir():
        print(f"Media directory not found: {config.media_root}", file=sys.stderr)
        return 2

    try:
        statistics = collect_statistics(config.db_path, config.media_root)
    except Exception as exc:  # noqa: BLE001 - report unexpected CLI failures.
        print(f"Statistics error: {exc}", file=sys.stderr)
        return 1

    print(format_statistics(statistics))
    return 0


def _read_video_references(
    db_path: Path,
) -> tuple[tuple[str, object], ...]:
    database_uri = f"{db_path.as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT videos.path, videos_detail.duration
            FROM videos
            LEFT JOIN videos_detail
                ON videos_detail.video_ref_id = videos.id
            WHERE videos.path IS NOT NULL
                AND TRIM(videos.path) != ''
            ORDER BY videos.id
            """
        ).fetchall()
    return tuple((str(path), duration) for path, duration in rows)


if __name__ == "__main__":
    raise SystemExit(main())

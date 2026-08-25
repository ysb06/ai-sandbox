from __future__ import annotations

import argparse
import re
import sqlite3
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ytcrawl.config import ConfigError, get_config

_TEMPORARY_MEDIA_SUFFIXES = frozenset({".part", ".ytdl", ".tmp", ".temp"})
_ISO_8601_DURATION_PATTERN = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?"
    r"$"
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


def parse_iso8601_duration(value: str) -> Decimal:
    """Return the number of seconds in a YouTube ISO-8601 duration."""
    match = _ISO_8601_DURATION_PATTERN.fullmatch(value)
    if match is None or not any(match.groupdict().values()):
        raise ValueError(f"Invalid ISO-8601 duration: {value!r}")
    if "T" in value and not any(
        match.group(name) for name in ("hours", "minutes", "seconds")
    ):
        raise ValueError(f"Invalid ISO-8601 duration: {value!r}")

    try:
        days = Decimal(match.group("days") or "0")
        hours = Decimal(match.group("hours") or "0")
        minutes = Decimal(match.group("minutes") or "0")
        seconds = Decimal(match.group("seconds") or "0")
    except InvalidOperation as exc:
        raise ValueError(f"Invalid ISO-8601 duration: {value!r}") from exc

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def collect_statistics(
    db_path: str | Path,
    media_root: str | Path,
) -> DatasetStatistics:
    """Read dataset statistics without modifying the SQLite database."""
    selected_db_path = Path(db_path).expanduser().resolve()
    selected_media_root = Path(media_root).expanduser().resolve()

    rows = _read_video_references(selected_db_path)
    references: dict[Path, list[object]] = {}
    duplicate_references = 0

    for stored_path, duration in rows:
        stored_media_path = Path(stored_path)
        candidate = (selected_media_root / stored_media_path).resolve()
        if (
            stored_media_path.suffix.lower() in _TEMPORARY_MEDIA_SUFFIXES
            or candidate.suffix.lower() in _TEMPORARY_MEDIA_SUFFIXES
        ):
            continue
        if candidate in references:
            duplicate_references += 1
        references.setdefault(candidate, []).append(duration)

    total_size = 0
    existing_files: dict[Path, list[object]] = {}
    missing_files = 0
    for path, durations in references.items():
        try:
            file_stat = path.stat()
        except FileNotFoundError:
            missing_files += 1
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            missing_files += 1
            continue

        total_size += file_stat.st_size
        existing_files[path] = durations

    total_duration = Decimal(0)
    duration_files = 0
    missing_durations = 0
    invalid_durations = 0
    conflicting_durations = 0

    for durations in existing_files.values():
        valid_values: set[Decimal] = set()
        invalid_value_found = False
        for raw_duration in durations:
            if raw_duration is None:
                continue
            if not isinstance(raw_duration, str):
                invalid_value_found = True
                continue
            value = raw_duration.strip()
            if not value:
                continue
            try:
                valid_values.add(parse_iso8601_duration(value))
            except ValueError:
                invalid_value_found = True

        if len(valid_values) > 1:
            conflicting_durations += 1
        elif invalid_value_found:
            invalid_durations += 1
        elif not valid_values:
            missing_durations += 1
        else:
            total_duration += next(iter(valid_values))
            duration_files += 1

    return DatasetStatistics(
        completed_video_files=len(existing_files),
        total_size_bytes=total_size,
        total_duration_seconds=total_duration,
        duration_files=duration_files,
        duplicate_db_references_ignored=duplicate_references,
        missing_referenced_files=missing_files,
        missing_durations=missing_durations,
        invalid_durations=invalid_durations,
        conflicting_durations=conflicting_durations,
    )


def format_statistics(statistics: DatasetStatistics) -> str:
    """Format dataset statistics for the human-readable CLI output."""
    size_gb = statistics.total_size_bytes / 1_000_000_000
    size_gib = statistics.total_size_bytes / (1024**3)
    return "\n".join(
        (
            f"Completed video files: {statistics.completed_video_files:,}",
            "Total size: "
            f"{size_gb:.2f} GB / {size_gib:.2f} GiB "
            f"({statistics.total_size_bytes:,} bytes)",
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


def _format_duration(total_seconds: Decimal) -> str:
    hours, remainder = divmod(total_seconds, Decimal(3600))
    minutes, seconds = divmod(remainder, Decimal(60))
    second_text = _format_decimal(seconds)
    if seconds < 10:
        second_text = f"0{second_text}"
    return f"{int(hours)}:{int(minutes):02d}:{second_text}"


def _format_decimal(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    whole, separator, fraction = normalized.partition(".")
    formatted = f"{int(whole):,}"
    return f"{formatted}.{fraction}" if separator else formatted


if __name__ == "__main__":
    raise SystemExit(main())

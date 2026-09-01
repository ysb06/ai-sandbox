from __future__ import annotations

import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

TEMPORARY_MEDIA_SUFFIXES = frozenset({".part", ".ytdl", ".tmp", ".temp"})
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
class MediaAggregate:
    existing_files: int
    total_size_bytes: int
    total_duration_seconds: Decimal
    duration_files: int
    duplicate_references_ignored: int
    missing_files: int
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


def resolve_media_path(media_root: Path, stored_path: str) -> Path:
    """Resolve a stored media path using the same semantics as Review."""
    return (media_root / Path(stored_path)).resolve()


def collect_media_aggregate(
    media_root: str | Path,
    rows: Iterable[tuple[str, object]],
) -> MediaAggregate:
    """Aggregate unique, existing physical files and their DB durations."""
    selected_media_root = Path(media_root).expanduser().resolve()
    references: dict[Path, list[object]] = {}
    duplicate_references = 0

    for stored_path, duration in rows:
        stored_media_path = Path(stored_path)
        candidate = resolve_media_path(selected_media_root, stored_path)
        if (
            stored_media_path.suffix.lower() in TEMPORARY_MEDIA_SUFFIXES
            or candidate.suffix.lower() in TEMPORARY_MEDIA_SUFFIXES
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

    return MediaAggregate(
        existing_files=len(existing_files),
        total_size_bytes=total_size,
        total_duration_seconds=total_duration,
        duration_files=duration_files,
        duplicate_references_ignored=duplicate_references,
        missing_files=missing_files,
        missing_durations=missing_durations,
        invalid_durations=invalid_durations,
        conflicting_durations=conflicting_durations,
    )


def format_duration(total_seconds: Decimal) -> str:
    hours, remainder = divmod(total_seconds, Decimal(3600))
    minutes, seconds = divmod(remainder, Decimal(60))
    second_text = format_decimal(seconds)
    if seconds < 10:
        second_text = f"0{second_text}"
    return f"{int(hours)}:{int(minutes):02d}:{second_text}"


def format_decimal(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    whole, separator, fraction = normalized.partition(".")
    formatted = f"{int(whole):,}"
    return f"{formatted}.{fraction}" if separator else formatted


def format_size(total_size_bytes: int) -> str:
    size_gb = total_size_bytes / 1_000_000_000
    size_gib = total_size_bytes / (1024**3)
    return (
        f"{size_gb:.2f} GB / {size_gib:.2f} GiB "
        f"({total_size_bytes:,} bytes)"
    )

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ytcrawl.config import ConfigError, get_config
from ytcrawl.statistics._common import (
    collect_media_aggregate,
    format_decimal,
    format_duration,
    format_size,
    resolve_media_path,
)

_DECISIVE_STATUSES = frozenset({"accepted", "rejected"})


@dataclass(frozen=True, slots=True)
class AcceptanceStatistics:
    """Aggregate statistics for videos accepted by reviewer consensus."""

    accept_ratio_threshold: Decimal
    decisive_reviewed_videos: int
    accepted_logical_videos: int
    accepted_physical_video_files: int
    total_size_bytes: int
    total_duration_seconds: Decimal
    duration_files: int
    duplicate_reviewer_decisions_collapsed: int
    duplicate_file_references_ignored: int
    missing_files: int
    missing_durations: int
    invalid_durations: int
    conflicting_durations: int


@dataclass(frozen=True, slots=True)
class _VideoRow:
    video_ref_id: int
    logical_key: tuple[str, object]
    stored_path: str | None
    duration: object


def collect_acceptance_statistics(
    db_path: str | Path,
    media_root: str | Path,
    accept_ratio: Decimal,
) -> AcceptanceStatistics:
    """Read consensus statistics without modifying the SQLite database."""
    threshold = _validate_accept_ratio(accept_ratio)
    selected_db_path = Path(db_path).expanduser().resolve()
    selected_media_root = Path(media_root).expanduser().resolve()

    raw_video_rows, review_rows = _read_acceptance_data(selected_db_path)
    video_rows = tuple(
        _to_video_row(row, selected_media_root) for row in raw_video_rows
    )
    logical_key_by_video_ref = {
        row.video_ref_id: row.logical_key for row in video_rows
    }

    latest_decisions: dict[tuple[tuple[str, object], str], str] = {}
    duplicate_decisions = 0
    for _review_id, video_ref_id, username, status, _updated_at in review_rows:
        logical_key = logical_key_by_video_ref.get(video_ref_id)
        if logical_key is None:
            continue
        decision_key = (logical_key, username)
        if decision_key in latest_decisions:
            duplicate_decisions += 1
        latest_decisions[decision_key] = status

    decision_counts: dict[tuple[str, object], list[int]] = {}
    for (logical_key, _username), status in latest_decisions.items():
        if status not in _DECISIVE_STATUSES:
            continue
        counts = decision_counts.setdefault(logical_key, [0, 0])
        if status == "accepted":
            counts[0] += 1
        else:
            counts[1] += 1

    accepted_keys = {
        logical_key
        for logical_key, (accepted, rejected) in decision_counts.items()
        if Decimal(accepted) / Decimal(accepted + rejected) >= threshold
    }
    accepted_references = (
        (row.stored_path, row.duration)
        for row in video_rows
        if row.logical_key in accepted_keys and row.stored_path is not None
    )
    aggregate = collect_media_aggregate(
        selected_media_root,
        accepted_references,
    )

    return AcceptanceStatistics(
        accept_ratio_threshold=threshold,
        decisive_reviewed_videos=len(decision_counts),
        accepted_logical_videos=len(accepted_keys),
        accepted_physical_video_files=aggregate.existing_files,
        total_size_bytes=aggregate.total_size_bytes,
        total_duration_seconds=aggregate.total_duration_seconds,
        duration_files=aggregate.duration_files,
        duplicate_reviewer_decisions_collapsed=duplicate_decisions,
        duplicate_file_references_ignored=aggregate.duplicate_references_ignored,
        missing_files=aggregate.missing_files,
        missing_durations=aggregate.missing_durations,
        invalid_durations=aggregate.invalid_durations,
        conflicting_durations=aggregate.conflicting_durations,
    )


def format_acceptance_statistics(statistics: AcceptanceStatistics) -> str:
    """Format acceptance statistics for the human-readable CLI output."""
    return "\n".join(
        (
            "Accept ratio threshold: "
            f"{format_decimal(statistics.accept_ratio_threshold)}",
            f"Decisive reviewed videos: {statistics.decisive_reviewed_videos:,}",
            f"Accepted logical videos: {statistics.accepted_logical_videos:,}",
            "Accepted physical video files: "
            f"{statistics.accepted_physical_video_files:,}",
            f"Total size: {format_size(statistics.total_size_bytes)}",
            "Total duration: "
            f"{format_duration(statistics.total_duration_seconds)} "
            f"({format_decimal(statistics.total_duration_seconds)} seconds)",
            "Duration coverage: "
            f"{statistics.duration_files:,}/"
            f"{statistics.accepted_physical_video_files:,} files",
            "Duplicate reviewer decisions collapsed: "
            f"{statistics.duplicate_reviewer_decisions_collapsed:,}",
            "Duplicate file references ignored: "
            f"{statistics.duplicate_file_references_ignored:,}",
            f"Missing files: {statistics.missing_files:,}",
            f"Missing durations: {statistics.missing_durations:,}",
            f"Invalid durations: {statistics.invalid_durations:,}",
            f"Conflicting durations: {statistics.conflicting_durations:,}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report size and duration for videos accepted by reviewer consensus."
        )
    )
    parser.add_argument(
        "--accept-ratio",
        required=True,
        type=_parse_accept_ratio,
        help="Minimum accepted / (accepted + rejected) ratio, from 0.0 to 1.0.",
    )
    args = parser.parse_args(argv)

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
        statistics = collect_acceptance_statistics(
            config.db_path,
            config.media_root,
            args.accept_ratio,
        )
    except Exception as exc:  # noqa: BLE001 - report unexpected CLI failures.
        print(f"Acceptance statistics error: {exc}", file=sys.stderr)
        return 1

    print(format_acceptance_statistics(statistics))
    return 0


def _validate_accept_ratio(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid accept ratio: {value!r}") from exc
    if not value.is_finite() or value < 0 or value > 1:
        raise ValueError("Accept ratio must be between 0.0 and 1.0.")
    return value


def _parse_accept_ratio(value: str) -> Decimal:
    try:
        return _validate_accept_ratio(Decimal(value))
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "accept ratio must be a decimal between 0.0 and 1.0"
        ) from exc


def _to_video_row(
    row: tuple[int, object, object, object],
    media_root: Path,
) -> _VideoRow:
    video_ref_id, raw_video_id, raw_stored_path, duration = row
    video_id = (
        raw_video_id
        if isinstance(raw_video_id, str) and raw_video_id.strip()
        else ""
    )
    stored_path = (
        raw_stored_path
        if isinstance(raw_stored_path, str) and raw_stored_path.strip()
        else None
    )
    if video_id:
        logical_key: tuple[str, object] = ("video_id", video_id)
    elif stored_path is not None:
        logical_key = (
            "path",
            resolve_media_path(media_root, stored_path),
        )
    else:
        logical_key = ("video_ref_id", video_ref_id)
    return _VideoRow(
        video_ref_id=video_ref_id,
        logical_key=logical_key,
        stored_path=stored_path,
        duration=duration,
    )


def _read_acceptance_data(
    db_path: Path,
) -> tuple[
    tuple[tuple[int, object, object, object], ...],
    tuple[tuple[int, int, str, str, object], ...],
]:
    database_uri = f"{db_path.as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.execute("BEGIN")
        video_rows = connection.execute(
            """
            SELECT
                videos.id,
                videos.video_id,
                videos.path,
                videos_detail.duration
            FROM videos
            LEFT JOIN videos_detail
                ON videos_detail.video_ref_id = videos.id
            ORDER BY videos.id
            """
        ).fetchall()
        review_rows = connection.execute(
            """
            SELECT id, video_ref_id, username, status, updated_at
            FROM video_reviews
            ORDER BY updated_at, id
            """
        ).fetchall()

    return (
        tuple((int(row[0]), row[1], row[2], row[3]) for row in video_rows),
        tuple(
            (int(row[0]), int(row[1]), str(row[2]), str(row[3]), row[4])
            for row in review_rows
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())

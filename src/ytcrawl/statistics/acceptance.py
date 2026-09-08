from __future__ import annotations

import argparse
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
)
from ytcrawl.statistics._consensus import collect_review_consensus


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


def collect_acceptance_statistics(
    db_path: str | Path,
    media_root: str | Path,
    accept_ratio: Decimal,
) -> AcceptanceStatistics:
    """Read consensus statistics without modifying the SQLite database."""
    threshold = _validate_accept_ratio(accept_ratio)
    selected_media_root = Path(media_root).expanduser().resolve()
    consensus = collect_review_consensus(db_path, selected_media_root)

    accepted_keys = {
        logical_key
        for logical_key, counts in consensus.decision_counts.items()
        if Decimal(counts.accepted) / Decimal(counts.decisive) >= threshold
    }
    accepted_references = (
        (row.stored_path, row.duration)
        for row in consensus.video_rows
        if row.logical_key in accepted_keys and row.stored_path is not None
    )
    aggregate = collect_media_aggregate(
        selected_media_root,
        accepted_references,
    )

    return AcceptanceStatistics(
        accept_ratio_threshold=threshold,
        decisive_reviewed_videos=len(consensus.decision_counts),
        accepted_logical_videos=len(accepted_keys),
        accepted_physical_video_files=aggregate.existing_files,
        total_size_bytes=aggregate.total_size_bytes,
        total_duration_seconds=aggregate.total_duration_seconds,
        duration_files=aggregate.duration_files,
        duplicate_reviewer_decisions_collapsed=(
            consensus.duplicate_reviewer_decisions_collapsed
        ),
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


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from ytcrawl.config import ConfigError, get_config
from ytcrawl.statistics._common import format_decimal, format_size
from ytcrawl.statistics._consensus import collect_review_consensus

CleanupStatus = Literal[
    "deleted",
    "would_delete",
    "already_missing",
    "would_be_already_missing",
    "path_unset",
    "failed",
]


@dataclass(frozen=True, slots=True)
class RejectCleanupCandidate:
    video_ref_id: int
    video_id: str | None
    stored_path: str | None
    accepted: int
    rejected: int
    reject_ratio: Decimal


@dataclass(frozen=True, slots=True)
class RejectCleanupPlan:
    reject_ratio_threshold: Decimal
    eligible_logical_videos: int
    duplicate_reviewer_decisions_collapsed: int
    candidates: tuple[RejectCleanupCandidate, ...]


@dataclass(frozen=True, slots=True)
class CleanupFileOutcome:
    candidate: RejectCleanupCandidate
    status: CleanupStatus
    resolved_path: str | None
    size_bytes: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RejectCleanupResult:
    plan: RejectCleanupPlan
    dry_run: bool
    outcomes: tuple[CleanupFileOutcome, ...]

    @property
    def selected_rows(self) -> int:
        return len(self.plan.candidates)

    @property
    def deleted_files(self) -> int:
        return sum(outcome.status == "deleted" for outcome in self.outcomes)

    @property
    def would_delete_files(self) -> int:
        return sum(
            outcome.status == "would_delete" for outcome in self.outcomes
        )

    @property
    def already_missing(self) -> int:
        return sum(
            outcome.status
            in {"already_missing", "would_be_already_missing"}
            for outcome in self.outcomes
        )

    @property
    def path_unset(self) -> int:
        return sum(outcome.status == "path_unset" for outcome in self.outcomes)

    @property
    def failures(self) -> int:
        return sum(outcome.status == "failed" for outcome in self.outcomes)

    @property
    def affected_size_bytes(self) -> int:
        return sum(
            outcome.size_bytes
            for outcome in self.outcomes
            if outcome.status in {"deleted", "would_delete"}
        )


def collect_reject_cleanup_plan(
    db_path: str | Path,
    media_root: str | Path,
    reject_ratio: Decimal,
) -> RejectCleanupPlan:
    """Select DB rows whose logical video meets the Reject ratio policy."""
    threshold = _validate_reject_ratio(reject_ratio)
    consensus = collect_review_consensus(db_path, media_root)
    eligible_counts = {
        logical_key: counts
        for logical_key, counts in consensus.decision_counts.items()
        if counts.rejected > 0
        and Decimal(counts.rejected) / Decimal(counts.decisive) >= threshold
    }

    candidates: list[RejectCleanupCandidate] = []
    for row in consensus.video_rows:
        counts = eligible_counts.get(row.logical_key)
        if counts is None:
            continue
        candidates.append(
            RejectCleanupCandidate(
                video_ref_id=row.video_ref_id,
                video_id=row.video_id,
                stored_path=row.stored_path,
                accepted=counts.accepted,
                rejected=counts.rejected,
                reject_ratio=(
                    Decimal(counts.rejected) / Decimal(counts.decisive)
                ),
            )
        )

    return RejectCleanupPlan(
        reject_ratio_threshold=threshold,
        eligible_logical_videos=len(eligible_counts),
        duplicate_reviewer_decisions_collapsed=(
            consensus.duplicate_reviewer_decisions_collapsed
        ),
        candidates=tuple(candidates),
    )


def cleanup_rejected_media(
    db_path: str | Path,
    media_root: str | Path,
    reject_ratio: Decimal,
    *,
    dry_run: bool = False,
) -> RejectCleanupResult:
    """Delete only local files; preserve every database row and stored path."""
    root = Path(media_root).expanduser().resolve()
    plan = collect_reject_cleanup_plan(db_path, root, reject_ratio)
    planned_removed_paths: set[Path] = set()
    outcomes: list[CleanupFileOutcome] = []

    for candidate in plan.candidates:
        if candidate.stored_path is None:
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="path_unset",
                    resolved_path=None,
                )
            )
            continue

        raw_path = root / Path(candidate.stored_path)
        try:
            resolved_path = raw_path.resolve()
            resolved_path.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="failed",
                    resolved_path=None,
                    error=f"invalid media path: {exc}",
                )
            )
            continue

        try:
            file_stat = raw_path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="already_missing",
                    resolved_path=resolved_path.as_posix(),
                )
            )
            continue
        except OSError as exc:
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="failed",
                    resolved_path=resolved_path.as_posix(),
                    error=f"could not inspect file: {exc}",
                )
            )
            continue

        if stat.S_ISLNK(file_stat.st_mode):
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="failed",
                    resolved_path=resolved_path.as_posix(),
                    error="stored path is a symbolic link",
                )
            )
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="failed",
                    resolved_path=resolved_path.as_posix(),
                    error="stored path is not a regular file",
                )
            )
            continue

        # Inspect every stored path before simulating an earlier duplicate
        # deletion. This keeps dry-run safety failures aligned with a real run.
        if dry_run and resolved_path in planned_removed_paths:
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="would_be_already_missing",
                    resolved_path=resolved_path.as_posix(),
                )
            )
            continue

        if dry_run:
            planned_removed_paths.add(resolved_path)
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="would_delete",
                    resolved_path=resolved_path.as_posix(),
                    size_bytes=file_stat.st_size,
                )
            )
            continue

        try:
            _unlink_media_file(raw_path)
        except (FileNotFoundError, NotADirectoryError):
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="already_missing",
                    resolved_path=resolved_path.as_posix(),
                )
            )
        except OSError as exc:
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="failed",
                    resolved_path=resolved_path.as_posix(),
                    error=f"could not delete file: {exc}",
                )
            )
        else:
            outcomes.append(
                CleanupFileOutcome(
                    candidate=candidate,
                    status="deleted",
                    resolved_path=resolved_path.as_posix(),
                    size_bytes=file_stat.st_size,
                )
            )

    return RejectCleanupResult(
        plan=plan,
        dry_run=dry_run,
        outcomes=tuple(outcomes),
    )


def _unlink_media_file(path: Path) -> None:
    path.unlink()


def _validate_reject_ratio(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid reject ratio: {value!r}") from exc
    if not value.is_finite() or value < 0 or value > 1:
        raise ValueError("Reject ratio must be between 0.0 and 1.0.")
    return value


def _parse_reject_ratio(value: str) -> Decimal:
    try:
        return _validate_reject_ratio(Decimal(value))
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "reject ratio must be a decimal between 0.0 and 1.0"
        ) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.cleanup",
        description=(
            "Delete local media files whose logical video meets a Reject ratio. "
            "Database rows and stored paths are never changed."
        ),
    )
    parser.add_argument(
        "--reject-ratio",
        required=True,
        type=_parse_reject_ratio,
        help=(
            "Minimum rejected / (accepted + rejected) ratio, from 0.0 to 1.0. "
            "At least one Reject decision is always required."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report matching local files without deleting them.",
    )
    return parser.parse_args(argv)


def _format_outcome(outcome: CleanupFileOutcome, *, dry_run: bool) -> str:
    candidate = outcome.candidate
    identity = (
        f"video_ref_id={candidate.video_ref_id}, "
        f"video_id={candidate.video_id!r}, path={candidate.stored_path!r}"
    )
    decision = (
        f"accepted={candidate.accepted}, rejected={candidate.rejected}, "
        f"reject_ratio={format_decimal(candidate.reject_ratio)}"
    )
    if outcome.status == "deleted":
        return f"Deleted {identity}, size_bytes={outcome.size_bytes}; {decision}."
    if outcome.status == "would_delete":
        return (
            f"Would delete {identity}, size_bytes={outcome.size_bytes}; "
            f"{decision}."
        )
    if outcome.status == "would_be_already_missing":
        return (
            "Would already be missing after an earlier selected row: "
            f"{identity}; {decision}."
        )
    if outcome.status == "path_unset":
        return f"Stored path is unset; no file to delete: {identity}; {decision}."
    if outcome.status == "already_missing":
        return f"File is already missing: {identity}; {decision}."
    action = "inspect" if dry_run else "delete"
    return (
        f"Failed to {action} {identity}: {outcome.error or 'unknown error'}; "
        f"{decision}."
    )


def print_cleanup_result(result: RejectCleanupResult) -> None:
    for outcome in result.outcomes:
        message = _format_outcome(outcome, dry_run=result.dry_run)
        if outcome.status in {"already_missing", "path_unset"}:
            print(f"Warning: {message}", file=sys.stderr)
        elif outcome.status == "failed":
            print(message, file=sys.stderr)
        else:
            print(message)

    print("Reject cleanup summary:")
    print(
        "  Reject ratio threshold: "
        f"{format_decimal(result.plan.reject_ratio_threshold)}"
    )
    print(f"  Mode: {'dry-run' if result.dry_run else 'delete'}")
    print(
        f"  Eligible logical videos: {result.plan.eligible_logical_videos:,}"
    )
    print(f"  Selected video rows: {result.selected_rows:,}")
    print(
        "  Duplicate reviewer decisions collapsed: "
        f"{result.plan.duplicate_reviewer_decisions_collapsed:,}"
    )
    if result.dry_run:
        print(f"  Files that would be deleted: {result.would_delete_files:,}")
        print(
            "  Selected file-size total: "
            f"{format_size(result.affected_size_bytes)}"
        )
        print("  No files were deleted.")
    else:
        print(f"  Deleted files: {result.deleted_files:,}")
        print(
            "  Deleted file-size total (not guaranteed freed disk space): "
            f"{format_size(result.affected_size_bytes)}"
        )
    print(f"  Already missing: {result.already_missing:,}")
    print(f"  Stored path unset: {result.path_unset:,}")
    print(f"  Failed: {result.failures:,}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
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
        result = cleanup_rejected_media(
            config.db_path,
            config.media_root,
            args.reject_ratio,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - normalize CLI failures.
        print(f"Reject cleanup error: {exc}", file=sys.stderr)
        return 1

    print_cleanup_result(result)
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

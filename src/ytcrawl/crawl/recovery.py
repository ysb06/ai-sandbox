from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ytcrawl.config import ConfigError, get_config
from ytcrawl.crawl.download import (
    DOWNLOAD_BATCH_SIZE,
    DownloadCrawlResult,
    crawl_youtube_videos,
)
from ytcrawl.db import (
    core,
    video_download_attempts,
    video_review_segments,
    video_reviews,
    videos,
    videos_detail,
)

RECOVERY_BATCH_SIZE = DOWNLOAD_BATCH_SIZE


class RecoveryError(RuntimeError):
    """Raised when a recovery action cannot be completed safely."""


class RecoveryPreflightError(RecoveryError):
    """Raised before any recovery mutation or media request is made."""


class DuplicateRemovalConflictError(RecoveryPreflightError):
    """Raised when at least one duplicate group cannot be merged losslessly."""

    def __init__(self, conflicts: tuple[DuplicateConflict, ...]) -> None:
        self.conflicts = conflicts
        details = "; ".join(
            f"{conflict.video_id}: {conflict.reason}" for conflict in conflicts
        )
        super().__init__(
            "Duplicate removal aborted because conflicts were found; "
            f"no backup or database changes were made. {details}"
        )


@dataclass(frozen=True, slots=True)
class DuplicateVideoGroup:
    video_id: str
    representative_id: int
    duplicate_ids: tuple[int, ...]

    @property
    def video_ref_ids(self) -> tuple[int, ...]:
        return (self.representative_id, *self.duplicate_ids)


@dataclass(frozen=True, slots=True)
class DuplicateConflict:
    video_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReviewMergePlan:
    username: str
    review_ids: tuple[int, ...]
    canonical_review_id: int
    status: str
    note: str | None
    segments: tuple[tuple[int, int], ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DuplicateMergePlan:
    group: DuplicateVideoGroup
    path: str | None
    embed_code: str | None
    detail_donor_id: int | None
    detail_delete_ids: tuple[int, ...]
    review_merges: tuple[ReviewMergePlan, ...]


@dataclass(frozen=True, slots=True)
class DuplicateRemovalPlan:
    groups: tuple[DuplicateMergePlan, ...]
    conflicts: tuple[DuplicateConflict, ...]


@dataclass(frozen=True, slots=True)
class DuplicateRemovalResult:
    groups_removed: int
    rows_removed: int
    backup_path: Path | None


@dataclass(frozen=True, slots=True)
class AuditPathFinding:
    video_ref_id: int
    video_id: str | None
    stored_path: str | None
    resolved_path: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class AuditIncompleteAttempt:
    attempt_id: int
    video_ref_id: int
    video_id: str | None
    started_at: datetime


@dataclass(frozen=True, slots=True)
class AuditOrphanFile:
    relative_path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class RecoveryAuditResult:
    duplicate_groups: tuple[DuplicateVideoGroup, ...]
    missing_paths: tuple[AuditPathFinding, ...]
    zero_byte_files: tuple[AuditPathFinding, ...]
    nonregular_paths: tuple[AuditPathFinding, ...]
    invalid_paths: tuple[AuditPathFinding, ...]
    unfinished_attempts: tuple[AuditIncompleteAttempt, ...]
    unreferenced_media_files: tuple[AuditOrphanFile, ...]

    @property
    def duplicate_extra_rows(self) -> int:
        return sum(len(group.duplicate_ids) for group in self.duplicate_groups)

    @property
    def has_findings(self) -> bool:
        return bool(
            self.duplicate_groups
            or self.missing_paths
            or self.zero_byte_files
            or self.nonregular_paths
            or self.invalid_paths
            or self.unfinished_attempts
            or self.unreferenced_media_files
        )


# Compatibility aliases keep the result vocabulary explicit for callers while
# retaining the concise Audit* names used by the recovery module.
RecoveryPathFinding = AuditPathFinding
UnfinishedAttemptFinding = AuditIncompleteAttempt
UnreferencedMediaFile = AuditOrphanFile
RecoveryAuditReport = RecoveryAuditResult


def _nonempty_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def find_duplicate_video_groups(
    session: Session,
) -> tuple[DuplicateVideoGroup, ...]:
    duplicate_video_ids = tuple(
        session.scalars(
            select(videos.Video.video_id)
            .where(
                videos.Video.video_id.is_not(None),
                func.trim(videos.Video.video_id) != "",
            )
            .group_by(videos.Video.video_id)
            .having(func.count(videos.Video.id) > 1)
        )
    )
    if not duplicate_video_ids:
        return ()

    rows = session.execute(
        select(videos.Video.video_id, videos.Video.id)
        .where(videos.Video.video_id.in_(duplicate_video_ids))
        .order_by(videos.Video.id)
    ).all()
    ids_by_video_id: dict[str, list[int]] = defaultdict(list)
    for video_id, video_ref_id in rows:
        if video_id is not None:
            ids_by_video_id[video_id].append(video_ref_id)

    groups = tuple(
        DuplicateVideoGroup(
            video_id=video_id,
            representative_id=record_ids[0],
            duplicate_ids=tuple(record_ids[1:]),
        )
        for video_id, record_ids in ids_by_video_id.items()
    )
    return tuple(sorted(groups, key=lambda group: group.representative_id))


def _path_inside_media_root(
    media_root: Path,
    stored_path: str,
) -> tuple[Path, str]:
    try:
        candidate = (media_root / Path(stored_path)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RecoveryPreflightError(
            f"could not resolve stored path {stored_path!r}: {exc}"
        ) from exc
    try:
        relative_path = candidate.relative_to(media_root)
    except ValueError as exc:
        raise RecoveryPreflightError(
            f"stored path escapes the media root: {stored_path!r}"
        ) from exc
    return candidate, relative_path.as_posix()


def _choose_group_path(
    video_rows: tuple[videos.Video, ...],
    media_root: Path,
) -> tuple[str | None, tuple[str, ...]]:
    candidates: dict[Path, tuple[str, bool]] = {}
    problems: list[str] = []
    for video in video_rows:
        stored_path = _nonempty_text(video.path)
        if stored_path is None:
            continue
        try:
            candidate, normalized_path = _path_inside_media_root(
                media_root,
                stored_path,
            )
            try:
                file_stat = candidate.stat()
            except (FileNotFoundError, NotADirectoryError):
                is_valid = False
            except OSError as exc:
                problems.append(
                    f"could not inspect path for video row {video.id}: {exc}"
                )
                continue
            else:
                is_valid = stat.S_ISREG(file_stat.st_mode) and file_stat.st_size > 0
            candidates[candidate] = (normalized_path, is_valid)
        except RecoveryPreflightError as exc:
            problems.append(f"video row {video.id} has {exc}")

    if problems:
        return None, tuple(problems)

    valid_candidates = tuple(
        (path, normalized)
        for path, (normalized, is_valid) in candidates.items()
        if is_valid
    )
    if len(valid_candidates) > 1:
        return None, (
            "multiple different valid local media files are referenced: "
            + ", ".join(str(path) for path, _ in valid_candidates),
        )
    if valid_candidates:
        return valid_candidates[0][1], ()

    if len(candidates) > 1:
        return None, (
            "multiple different non-working stored paths are referenced: "
            + ", ".join(str(path) for path in candidates),
        )
    if candidates:
        return next(iter(candidates.values()))[0], ()
    return None, ()


def _choose_group_embed_code(
    video_rows: tuple[videos.Video, ...],
) -> tuple[str | None, str | None]:
    values = tuple(
        dict.fromkeys(
            value
            for value in (_nonempty_text(video.embed_code) for video in video_rows)
            if value is not None
        )
    )
    if len(values) > 1:
        return None, "multiple different non-empty embed codes are present"
    return (values[0] if values else None), None


def _review_merge_plans(
    session: Session,
    group: DuplicateVideoGroup,
) -> tuple[tuple[ReviewMergePlan, ...], tuple[str, ...]]:
    review_rows = tuple(
        session.scalars(
            select(video_reviews.VideoReview)
            .where(
                video_reviews.VideoReview.video_ref_id.in_(group.video_ref_ids)
            )
            .order_by(
                video_reviews.VideoReview.username,
                video_reviews.VideoReview.id,
            )
        )
    )
    if not review_rows:
        return (), ()

    review_ids = tuple(review.id for review in review_rows)
    segment_rows = tuple(
        session.scalars(
            select(video_review_segments.VideoReviewSegment)
            .where(
                video_review_segments.VideoReviewSegment.review_id.in_(review_ids)
            )
            .order_by(
                video_review_segments.VideoReviewSegment.review_id,
                video_review_segments.VideoReviewSegment.start_ms,
                video_review_segments.VideoReviewSegment.end_ms,
                video_review_segments.VideoReviewSegment.id,
            )
        )
    )
    segments_by_review_id: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for segment in segment_rows:
        segments_by_review_id[segment.review_id].append(
            (segment.start_ms, segment.end_ms)
        )

    reviews_by_username: dict[str, list[video_reviews.VideoReview]] = defaultdict(list)
    for review in review_rows:
        reviews_by_username[review.username].append(review)

    plans: list[ReviewMergePlan] = []
    conflicts: list[str] = []
    for username, username_reviews in reviews_by_username.items():
        unexpected_statuses = {
            review.status
            for review in username_reviews
            if review.status not in video_reviews.REVIEW_STATUSES
        }
        if unexpected_statuses:
            conflicts.append(
                f"reviewer {username!r} has invalid statuses "
                f"{sorted(unexpected_statuses)!r}"
            )
            continue

        statuses = {review.status for review in username_reviews}
        if len(statuses) > 1:
            conflicts.append(
                f"reviewer {username!r} has conflicting decisions "
                f"{sorted(statuses)!r}"
            )
            continue
        selected_status = next(iter(statuses))

        notes = tuple(
            dict.fromkeys(
                note
                for note in (_nonempty_text(review.note) for review in username_reviews)
                if note is not None
            )
        )
        if len(notes) > 1:
            conflicts.append(
                f"reviewer {username!r} has conflicting non-empty notes"
            )
            continue
        selected_note = notes[0] if notes else None

        nonempty_segment_sets = tuple(
            dict.fromkeys(
                tuple(sorted(segments_by_review_id.get(review.id, ())))
                for review in username_reviews
                if segments_by_review_id.get(review.id)
            )
        )
        if len(nonempty_segment_sets) > 1:
            conflicts.append(
                f"reviewer {username!r} has conflicting review segments"
            )
            continue
        selected_segments = (
            nonempty_segment_sets[0] if nonempty_segment_sets else ()
        )

        representative_review = next(
            (
                review
                for review in username_reviews
                if review.video_ref_id == group.representative_id
            ),
            None,
        )
        canonical_review = representative_review or min(
            username_reviews,
            key=lambda review: review.id,
        )
        plans.append(
            ReviewMergePlan(
                username=username,
                review_ids=tuple(review.id for review in username_reviews),
                canonical_review_id=canonical_review.id,
                status=selected_status,
                note=selected_note,
                segments=selected_segments,
                created_at=min(review.created_at for review in username_reviews),
                updated_at=max(review.updated_at for review in username_reviews),
            )
        )

    return tuple(plans), tuple(conflicts)


def _preflight_duplicate_group(
    session: Session,
    group: DuplicateVideoGroup,
    media_root: Path,
) -> tuple[DuplicateMergePlan | None, tuple[DuplicateConflict, ...]]:
    video_rows = tuple(
        session.scalars(
            select(videos.Video)
            .where(videos.Video.id.in_(group.video_ref_ids))
            .order_by(videos.Video.id)
        )
    )
    if tuple(video.id for video in video_rows) != group.video_ref_ids:
        return None, (
            DuplicateConflict(group.video_id, "video rows changed during preflight"),
        )

    problems: list[str] = []
    selected_path, path_problems = _choose_group_path(video_rows, media_root)
    problems.extend(path_problems)
    selected_embed_code, embed_problem = _choose_group_embed_code(video_rows)
    if embed_problem is not None:
        problems.append(embed_problem)

    detail_rows = tuple(
        session.scalars(
            select(videos_detail.VideoDetail)
            .where(
                videos_detail.VideoDetail.video_ref_id.in_(group.video_ref_ids)
            )
            .order_by(
                videos_detail.VideoDetail.video_ref_id,
                videos_detail.VideoDetail.id,
            )
        )
    )
    detail_counts: dict[int, int] = defaultdict(int)
    for detail in detail_rows:
        detail_counts[detail.video_ref_id] += 1
    invalid_detail_ids = tuple(
        video_ref_id
        for video_ref_id, count in detail_counts.items()
        if count > 1
    )
    if invalid_detail_ids:
        problems.append(
            "more than one detail row exists for video rows "
            f"{invalid_detail_ids!r}"
        )

    representative_detail = next(
        (
            detail
            for detail in detail_rows
            if detail.video_ref_id == group.representative_id
        ),
        None,
    )
    detail_donor = None
    if representative_detail is None and detail_rows:
        detail_donor = min(
            detail_rows,
            key=lambda detail: (detail.video_ref_id, detail.id),
        )
    detail_delete_ids = tuple(
        detail.id
        for detail in detail_rows
        if detail is not representative_detail and detail is not detail_donor
    )

    review_merges, review_problems = _review_merge_plans(session, group)
    problems.extend(review_problems)
    if problems:
        return None, tuple(
            DuplicateConflict(group.video_id, problem) for problem in problems
        )

    return (
        DuplicateMergePlan(
            group=group,
            path=selected_path,
            embed_code=selected_embed_code,
            detail_donor_id=detail_donor.id if detail_donor is not None else None,
            detail_delete_ids=detail_delete_ids,
            review_merges=review_merges,
        ),
        (),
    )


def _foreign_key_violations(session: Session) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in session.execute(text("PRAGMA foreign_key_check")))


def preflight_duplicate_removal(
    session: Session,
    media_root: str | Path,
) -> DuplicateRemovalPlan:
    violations = _foreign_key_violations(session)
    if violations:
        raise RecoveryPreflightError(
            "The database already contains foreign-key violations: "
            f"{violations!r}"
        )

    root = Path(media_root).expanduser().resolve()
    merge_plans: list[DuplicateMergePlan] = []
    conflicts: list[DuplicateConflict] = []
    for group in find_duplicate_video_groups(session):
        merge_plan, group_conflicts = _preflight_duplicate_group(
            session,
            group,
            root,
        )
        conflicts.extend(group_conflicts)
        if merge_plan is not None:
            merge_plans.append(merge_plan)
    return DuplicateRemovalPlan(
        groups=tuple(merge_plans),
        conflicts=tuple(conflicts),
    )


def _create_database_backup(db_path: Path, backup_root: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_root / f"ytcrawl-recovery-{timestamp}.sqlite3"
    backup_root.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise RecoveryError(f"Backup destination already exists: {backup_path}")

    source_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(source_uri, uri=True, timeout=30.0)
        source_check_rows = source.execute("PRAGMA quick_check").fetchall()
        if source_check_rows != [("ok",)]:
            raise sqlite3.DatabaseError(
                f"Source database quick_check failed: {source_check_rows!r}"
            )
        destination = sqlite3.connect(backup_path, timeout=30.0)
        source.backup(destination)
        destination.commit()
        mode_row = destination.execute("PRAGMA journal_mode=DELETE").fetchone()
        if mode_row is None or str(mode_row[0]).lower() != "delete":
            raise sqlite3.DatabaseError(
                "Could not normalize backup journal_mode to DELETE."
            )
        check_rows = destination.execute("PRAGMA quick_check").fetchall()
        if check_rows != [("ok",)]:
            raise sqlite3.DatabaseError(
                f"Backup quick_check failed: {check_rows!r}"
            )
        destination.commit()
    except Exception as exc:  # noqa: BLE001 - clean every partial backup artifact.
        if destination is not None:
            destination.close()
            destination = None
        cleanup_errors = _remove_backup_artifacts(backup_path)
        cleanup_detail = (
            f"; cleanup also failed: {'; '.join(cleanup_errors)}"
            if cleanup_errors
            else ""
        )
        raise RecoveryError(
            f"Failed to create SQLite backup: {exc}{cleanup_detail}"
        ) from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()

    try:
        os.chmod(backup_path, stat.S_IMODE(db_path.stat().st_mode))
    except OSError as exc:
        cleanup_errors = _remove_backup_artifacts(backup_path)
        cleanup_detail = (
            f"; cleanup also failed: {'; '.join(cleanup_errors)}"
            if cleanup_errors
            else ""
        )
        raise RecoveryError(
            f"Failed to finalize SQLite backup: {exc}{cleanup_detail}"
        ) from exc
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            Path(f"{backup_path}{suffix}").unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors = _remove_backup_artifacts(backup_path)
            cleanup_detail = (
                f"; cleanup also failed: {'; '.join(cleanup_errors)}"
                if cleanup_errors
                else ""
            )
            raise RecoveryError(
                f"Failed to finalize SQLite backup sidecars: {exc}"
                f"{cleanup_detail}"
            ) from exc
    return backup_path


def _remove_backup_artifacts(backup_path: Path) -> tuple[str, ...]:
    errors: list[str] = []
    for candidate in (
        backup_path,
        Path(f"{backup_path}-journal"),
        Path(f"{backup_path}-wal"),
        Path(f"{backup_path}-shm"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    return tuple(errors)


def _apply_review_merge(
    session: Session,
    representative_id: int,
    plan: ReviewMergePlan,
) -> None:
    canonical = session.get(
        video_reviews.VideoReview,
        plan.canonical_review_id,
    )
    if canonical is None:
        raise RecoveryError(
            f"Review row disappeared during merge: {plan.canonical_review_id}"
        )

    if len(plan.review_ids) == 1:
        canonical.video_ref_id = representative_id
        return

    session.execute(
        delete(video_review_segments.VideoReviewSegment).where(
            video_review_segments.VideoReviewSegment.review_id.in_(plan.review_ids)
        )
    )
    noncanonical_ids = tuple(
        review_id
        for review_id in plan.review_ids
        if review_id != plan.canonical_review_id
    )
    session.execute(
        delete(video_reviews.VideoReview).where(
            video_reviews.VideoReview.id.in_(noncanonical_ids)
        )
    )
    session.flush()

    canonical.video_ref_id = representative_id
    canonical.status = plan.status
    canonical.note = plan.note
    canonical.created_at = plan.created_at
    canonical.updated_at = plan.updated_at
    session.flush()
    session.add_all(
        video_review_segments.VideoReviewSegment(
            review_id=canonical.id,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for start_ms, end_ms in plan.segments
    )


def _apply_duplicate_merge(
    session: Session,
    plan: DuplicateMergePlan,
) -> None:
    group = plan.group
    representative = session.get(videos.Video, group.representative_id)
    if representative is None:
        raise RecoveryError(
            f"Representative video row disappeared: {group.representative_id}"
        )
    representative.path = plan.path
    representative.embed_code = plan.embed_code

    if plan.detail_delete_ids:
        session.execute(
            delete(videos_detail.VideoDetail).where(
                videos_detail.VideoDetail.id.in_(plan.detail_delete_ids)
            )
        )
    if plan.detail_donor_id is not None:
        donor = session.get(videos_detail.VideoDetail, plan.detail_donor_id)
        if donor is None:
            raise RecoveryError(
                f"Detail donor disappeared during merge: {plan.detail_donor_id}"
            )
        donor.video_ref_id = group.representative_id
    session.flush()

    session.execute(
        update(video_download_attempts.VideoDownloadAttempt)
        .where(
            video_download_attempts.VideoDownloadAttempt.video_ref_id.in_(
                group.duplicate_ids
            )
        )
        .values(video_ref_id=group.representative_id)
    )
    for review_plan in plan.review_merges:
        _apply_review_merge(session, group.representative_id, review_plan)
    session.flush()

    delete_result = session.execute(
        delete(videos.Video).where(videos.Video.id.in_(group.duplicate_ids))
    )
    if delete_result.rowcount != len(group.duplicate_ids):
        raise RecoveryError(
            f"Expected to delete {len(group.duplicate_ids)} video rows for "
            f"{group.video_id}, deleted {delete_result.rowcount}."
        )
    session.flush()


def _table_counts(session: Session) -> dict[str, int]:
    models = {
        "videos": videos.Video,
        "videos_detail": videos_detail.VideoDetail,
        "video_download_attempts": video_download_attempts.VideoDownloadAttempt,
        "video_reviews": video_reviews.VideoReview,
        "video_review_segments": video_review_segments.VideoReviewSegment,
    }
    return {
        name: int(
            session.scalar(select(func.count()).select_from(model)) or 0
        )
        for name, model in models.items()
    }


def _expected_counts_after_merge(
    session: Session,
    before: dict[str, int],
    plan: DuplicateRemovalPlan,
) -> dict[str, int]:
    expected = dict(before)
    expected["videos"] -= sum(
        len(merge.group.duplicate_ids) for merge in plan.groups
    )
    expected["videos_detail"] -= sum(
        len(merge.detail_delete_ids) for merge in plan.groups
    )

    removed_review_rows = 0
    removed_segment_rows = 0
    recreated_segment_rows = 0
    for merge in plan.groups:
        for review in merge.review_merges:
            if len(review.review_ids) <= 1:
                continue
            removed_review_rows += len(review.review_ids) - 1
            removed_segment_rows += int(
                session.scalar(
                    select(func.count())
                    .select_from(video_review_segments.VideoReviewSegment)
                    .where(
                        video_review_segments.VideoReviewSegment.review_id.in_(
                            review.review_ids
                        )
                    )
                )
                or 0
            )
            recreated_segment_rows += len(review.segments)
    expected["video_reviews"] -= removed_review_rows
    expected["video_review_segments"] += (
        recreated_segment_rows - removed_segment_rows
    )
    return expected


def _apply_duplicate_removal_plan(
    plan: DuplicateRemovalPlan,
    media_root: Path,
) -> None:
    engine = core.get_engine()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if enabled != 1:
            raise RecoveryError(
                "Could not enable SQLite foreign-key enforcement for recovery."
            )
        connection.commit()

        session = Session(bind=connection, expire_on_commit=False)
        try:
            with session.begin():
                current_plan = preflight_duplicate_removal(session, media_root)
                if current_plan != plan:
                    raise RecoveryError(
                        "Duplicate data changed after preflight; recovery was aborted."
                    )
                before_counts = _table_counts(session)
                expected_counts = _expected_counts_after_merge(
                    session,
                    before_counts,
                    plan,
                )
                for merge_plan in plan.groups:
                    _apply_duplicate_merge(session, merge_plan)
                session.flush()

                after_counts = _table_counts(session)
                if after_counts != expected_counts:
                    raise RecoveryError(
                        "Recovery row-count validation failed; expected "
                        f"{expected_counts!r}, found {after_counts!r}."
                    )

                remaining = find_duplicate_video_groups(session)
                if remaining:
                    raise RecoveryError(
                        "Duplicate rows remained after the merge: "
                        f"{tuple(group.video_id for group in remaining)!r}"
                    )
                violations = _foreign_key_violations(session)
                if violations:
                    raise RecoveryError(
                        "Foreign-key validation failed after duplicate removal: "
                        f"{violations!r}"
                    )
        finally:
            session.close()


def remove_duplicate_videos(
    db_path: str | Path,
    backup_root: str | Path,
    media_root: str | Path,
) -> DuplicateRemovalResult:
    selected_db_path = Path(db_path).expanduser().resolve()
    selected_backup_root = Path(backup_root).expanduser().resolve()
    selected_media_root = Path(media_root).expanduser().resolve()
    if not selected_db_path.is_file():
        raise RecoveryPreflightError(
            f"Database file not found: {selected_db_path}"
        )

    core.configure(f"sqlite:///{selected_db_path.as_posix()}")
    with core.session_scope() as session:
        plan = preflight_duplicate_removal(session, selected_media_root)
    if plan.conflicts:
        raise DuplicateRemovalConflictError(plan.conflicts)
    if not plan.groups:
        return DuplicateRemovalResult(
            groups_removed=0,
            rows_removed=0,
            backup_path=None,
        )

    backup_path = _create_database_backup(
        selected_db_path,
        selected_backup_root,
    )
    try:
        _apply_duplicate_removal_plan(plan, selected_media_root)
    except Exception as exc:
        raise RecoveryError(
            "Duplicate removal failed and its transaction was rolled back. "
            f"The pre-change backup remains at {backup_path}: {exc}"
        ) from exc

    return DuplicateRemovalResult(
        groups_removed=len(plan.groups),
        rows_removed=sum(len(plan.group.duplicate_ids) for plan in plan.groups),
        backup_path=backup_path,
    )


def find_missing_file_video_records(
    session: Session,
    media_root: str | Path,
) -> tuple[videos.VideoRecord, ...]:
    root = Path(media_root).expanduser().resolve()
    video_rows = tuple(
        session.scalars(
            select(videos.Video).order_by(videos.Video.id)
        )
    )

    missing: list[videos.VideoRecord] = []
    problems: list[str] = []
    for video in video_rows:
        record = videos.VideoRecord(id=video.id, video_id=video.video_id)
        stored_path = _nonempty_text(video.path)
        if stored_path is None:
            missing.append(record)
            continue

        try:
            candidate, _ = _path_inside_media_root(root, stored_path)
        except RecoveryPreflightError as exc:
            problems.append(f"video row {video.id}: {exc}")
            continue
        try:
            file_stat = candidate.stat()
        except (FileNotFoundError, NotADirectoryError):
            missing.append(record)
            continue
        except OSError as exc:
            problems.append(
                f"video row {video.id}: could not inspect {candidate}: {exc}"
            )
            continue

        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size == 0:
            missing.append(record)

    if problems:
        raise RecoveryPreflightError(
            "Missing-file preflight failed; no media download request was made. "
            + "; ".join(problems)
        )
    return tuple(missing)


def download_missing_videos(
    media_root: str | Path,
) -> DownloadCrawlResult:
    with core.session_scope() as session:
        video_records = find_missing_file_video_records(session, media_root)
    if not video_records:
        return DownloadCrawlResult()
    return crawl_youtube_videos(
        media_root,
        video_records,
        batch_size=RECOVERY_BATCH_SIZE,
        continuation_prompt=None,
    )


@dataclass(frozen=True, slots=True)
class _AuditPathScan:
    missing_paths: tuple[AuditPathFinding, ...]
    zero_byte_files: tuple[AuditPathFinding, ...]
    nonregular_paths: tuple[AuditPathFinding, ...]
    invalid_paths: tuple[AuditPathFinding, ...]
    referenced_paths: frozenset[Path]


def _audit_stored_paths(
    session: Session,
    media_root: Path,
) -> _AuditPathScan:
    video_rows = tuple(
        session.scalars(
            select(videos.Video).order_by(videos.Video.id)
        )
    )
    missing_paths: list[AuditPathFinding] = []
    zero_byte_files: list[AuditPathFinding] = []
    nonregular_paths: list[AuditPathFinding] = []
    invalid_paths: list[AuditPathFinding] = []
    referenced_paths: set[Path] = set()

    for video in video_rows:
        stored_path = video.path
        if stored_path is None:
            unset_reason = "path is NULL"
        elif stored_path == "":
            unset_reason = "path is empty"
        elif not stored_path.strip():
            unset_reason = "path is whitespace-only"
        else:
            unset_reason = None

        if unset_reason is not None:
            missing_paths.append(
                AuditPathFinding(
                    video_ref_id=video.id,
                    video_id=video.video_id,
                    stored_path=stored_path,
                    resolved_path=None,
                    reason=unset_reason,
                )
            )
            continue

        assert stored_path is not None
        raw_candidate = media_root / Path(stored_path)
        try:
            candidate = raw_candidate.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            invalid_paths.append(
                AuditPathFinding(
                    video_ref_id=video.id,
                    video_id=video.video_id,
                    stored_path=stored_path,
                    resolved_path=None,
                    reason=f"resolve error: {exc}",
                )
            )
            continue

        try:
            candidate.relative_to(media_root)
        except ValueError:
            invalid_paths.append(
                AuditPathFinding(
                    video_ref_id=video.id,
                    video_id=video.video_id,
                    stored_path=stored_path,
                    resolved_path=candidate.as_posix(),
                    reason="resolved path is outside the media root",
                )
            )
            continue

        # A contained DB path is a reference even when its target is absent,
        # empty, non-regular, or cannot be inspected.
        referenced_paths.add(candidate)
        try:
            raw_stat = raw_candidate.lstat()
        except (FileNotFoundError, NotADirectoryError):
            is_final_symlink = False
        except OSError as exc:
            invalid_paths.append(
                AuditPathFinding(
                    video_ref_id=video.id,
                    video_id=video.video_id,
                    stored_path=stored_path,
                    resolved_path=candidate.as_posix(),
                    reason=f"lstat error: {exc}",
                )
            )
            continue
        else:
            is_final_symlink = stat.S_ISLNK(raw_stat.st_mode)

        try:
            file_stat = candidate.stat()
        except (FileNotFoundError, NotADirectoryError):
            missing_paths.append(
                AuditPathFinding(
                    video_ref_id=video.id,
                    video_id=video.video_id,
                    stored_path=stored_path,
                    resolved_path=candidate.as_posix(),
                    reason=(
                        "broken internal symlink"
                        if is_final_symlink
                        else "path does not exist"
                    ),
                )
            )
            continue
        except OSError as exc:
            invalid_paths.append(
                AuditPathFinding(
                    video_ref_id=video.id,
                    video_id=video.video_id,
                    stored_path=stored_path,
                    resolved_path=candidate.as_posix(),
                    reason=f"stat error: {exc}",
                )
            )
            continue

        if stat.S_ISREG(file_stat.st_mode):
            if file_stat.st_size == 0:
                zero_byte_files.append(
                    AuditPathFinding(
                        video_ref_id=video.id,
                        video_id=video.video_id,
                        stored_path=stored_path,
                        resolved_path=candidate.as_posix(),
                        reason="regular file is zero bytes",
                    )
                )
            continue

        nonregular_paths.append(
            AuditPathFinding(
                video_ref_id=video.id,
                video_id=video.video_id,
                stored_path=stored_path,
                resolved_path=candidate.as_posix(),
                reason=(
                    "path is a directory"
                    if stat.S_ISDIR(file_stat.st_mode)
                    else "path is not a regular file"
                ),
            )
        )

    return _AuditPathScan(
        missing_paths=tuple(missing_paths),
        zero_byte_files=tuple(zero_byte_files),
        nonregular_paths=tuple(nonregular_paths),
        invalid_paths=tuple(invalid_paths),
        referenced_paths=frozenset(referenced_paths),
    )


def _audit_unfinished_attempts(
    session: Session,
) -> tuple[AuditIncompleteAttempt, ...]:
    rows = session.execute(
        select(
            video_download_attempts.VideoDownloadAttempt.id,
            video_download_attempts.VideoDownloadAttempt.video_ref_id,
            videos.Video.video_id,
            video_download_attempts.VideoDownloadAttempt.started_at,
        )
        .outerjoin(
            videos.Video,
            videos.Video.id
            == video_download_attempts.VideoDownloadAttempt.video_ref_id,
        )
        .where(video_download_attempts.VideoDownloadAttempt.finished_at.is_(None))
        .order_by(video_download_attempts.VideoDownloadAttempt.id)
    ).all()
    return tuple(
        AuditIncompleteAttempt(
            attempt_id=attempt_id,
            video_ref_id=video_ref_id,
            video_id=video_id,
            started_at=started_at,
        )
        for attempt_id, video_ref_id, video_id, started_at in rows
    )


def _audit_unreferenced_media_files(
    media_root: Path,
    referenced_paths: frozenset[Path],
) -> tuple[AuditOrphanFile, ...]:
    try:
        root_stat = media_root.stat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise RecoveryError(
            f"Could not inspect media root {media_root}: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise RecoveryError(f"Media root is not a directory: {media_root}")

    walk_errors: list[str] = []

    def record_walk_error(exc: OSError) -> None:
        walk_errors.append(str(exc))

    orphan_files: list[AuditOrphanFile] = []
    for current_root, directory_names, filenames in os.walk(
        media_root,
        topdown=True,
        onerror=record_walk_error,
        followlinks=False,
    ):
        directory_names.sort()
        filenames.sort()
        current_path = Path(current_root)
        for filename in filenames:
            path = current_path / filename
            try:
                file_stat = path.lstat()
            except OSError as exc:
                raise RecoveryError(
                    f"Could not inspect media entry {path}: {exc}"
                ) from exc
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            try:
                canonical_path = path.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise RecoveryError(
                    f"Could not resolve media file {path}: {exc}"
                ) from exc
            if canonical_path in referenced_paths:
                continue
            orphan_files.append(
                AuditOrphanFile(
                    relative_path=path.relative_to(media_root).as_posix(),
                    size_bytes=file_stat.st_size,
                )
            )

    if walk_errors:
        raise RecoveryError(
            "Media tree scan failed: " + "; ".join(walk_errors)
        )
    return tuple(
        sorted(orphan_files, key=lambda finding: finding.relative_path)
    )


def collect_recovery_audit(
    session: Session,
    media_root: str | Path,
) -> RecoveryAuditResult:
    try:
        root = Path(media_root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RecoveryError(f"Could not resolve media root: {exc}") from exc

    path_scan = _audit_stored_paths(session, root)
    return RecoveryAuditResult(
        duplicate_groups=find_duplicate_video_groups(session),
        missing_paths=path_scan.missing_paths,
        zero_byte_files=path_scan.zero_byte_files,
        nonregular_paths=path_scan.nonregular_paths,
        invalid_paths=path_scan.invalid_paths,
        unfinished_attempts=_audit_unfinished_attempts(session),
        unreferenced_media_files=_audit_unreferenced_media_files(
            root,
            path_scan.referenced_paths,
        ),
    )


def audit_recovery_state(
    db_path: str | Path,
    media_root: str | Path,
) -> RecoveryAuditResult:
    selected_db_path = Path(db_path).expanduser().resolve()
    if not selected_db_path.is_file():
        raise RecoveryPreflightError(
            f"Database file not found: {selected_db_path}"
        )

    database_uri = f"{selected_db_path.as_uri()}?mode=ro"

    def connect_read_only() -> sqlite3.Connection:
        return sqlite3.connect(database_uri, uri=True, timeout=30.0)

    engine = sqlalchemy_create_engine(
        "sqlite+pysqlite://",
        creator=connect_read_only,
        poolclass=NullPool,
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            query_only = connection.exec_driver_sql(
                "PRAGMA query_only"
            ).scalar_one()
            if query_only != 1:
                raise RecoveryError(
                    "Could not enforce SQLite query-only mode for recovery audit."
                )
            # SQLite's legacy transaction mode does not begin a transaction for
            # SELECT statements. End SQLAlchemy's implicit PRAGMA transaction,
            # then issue BEGIN explicitly so every audit query sees one snapshot.
            connection.commit()
            connection.exec_driver_sql("BEGIN")
            try:
                quick_check = tuple(
                    row[0]
                    for row in connection.exec_driver_sql("PRAGMA quick_check")
                )
                if quick_check != ("ok",):
                    raise RecoveryError(
                        "SQLite quick_check failed before audit: "
                        f"{quick_check!r}"
                    )

                session = Session(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                try:
                    return collect_recovery_audit(session, media_root)
                finally:
                    session.close()
            finally:
                connection.rollback()
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError(f"Recovery audit failed: {exc}") from exc
    finally:
        engine.dispose()


def _print_audit_path_section(
    title: str,
    findings: tuple[AuditPathFinding, ...],
) -> None:
    print(f"{title} ({len(findings)}):")
    if not findings:
        print("  (none)")
        return
    for finding in findings:
        resolved = (
            f", resolved={finding.resolved_path!r}"
            if finding.resolved_path is not None
            else ""
        )
        print(
            f"  - video_ref_id={finding.video_ref_id}, "
            f"video_id={finding.video_id!r}, path={finding.stored_path!r}"
            f"{resolved}, reason={finding.reason}"
        )


def print_audit_report(report: RecoveryAuditResult) -> None:
    print("Recovery audit summary:")
    print(
        f"  Duplicate video IDs: {len(report.duplicate_groups)} groups, "
        f"{report.duplicate_extra_rows} extra rows"
    )
    print(f"  Duplicate groups: {len(report.duplicate_groups)}")
    print(f"  Duplicate extra rows: {report.duplicate_extra_rows}")
    print(f"  Missing paths: {len(report.missing_paths)}")
    print(f"  Zero-byte files: {len(report.zero_byte_files)}")
    print(f"  Non-regular paths: {len(report.nonregular_paths)}")
    print(f"  Invalid paths: {len(report.invalid_paths)}")
    print(f"  Unfinished download attempts: {len(report.unfinished_attempts)}")
    print(
        "  Unreferenced media files: "
        f"{len(report.unreferenced_media_files)}"
    )
    print("Recovery audit details:")
    print(f"Duplicate video IDs ({len(report.duplicate_groups)}):")
    if not report.duplicate_groups:
        print("  (none)")
    else:
        for group in report.duplicate_groups:
            print(
                f"  - video_id={group.video_id!r}, "
                f"representative_id={group.representative_id}, "
                f"duplicate_ids={group.duplicate_ids!r}"
            )
    _print_audit_path_section("Missing paths", report.missing_paths)
    _print_audit_path_section("Zero-byte files", report.zero_byte_files)
    _print_audit_path_section("Non-regular paths", report.nonregular_paths)
    _print_audit_path_section("Invalid paths", report.invalid_paths)

    print(f"Unfinished download attempts ({len(report.unfinished_attempts)}):")
    if not report.unfinished_attempts:
        print("  (none)")
    else:
        for finding in report.unfinished_attempts:
            print(
                f"  - attempt_id={finding.attempt_id}, "
                f"video_ref_id={finding.video_ref_id}, "
                f"video_id={finding.video_id!r}, "
                f"started_at={finding.started_at.isoformat()}"
            )

    print(
        f"Unreferenced media files ({len(report.unreferenced_media_files)}):"
    )
    if not report.unreferenced_media_files:
        print("  (none)")
    else:
        for finding in report.unreferenced_media_files:
            print(
                f"  - path={finding.relative_path!r}, "
                f"size_bytes={finding.size_bytes}"
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.crawl.recovery",
        description=(
            "Remove duplicate stored YouTube rows and/or download media files "
            "that are missing from local storage."
        ),
    )
    parser.add_argument(
        "--remove-duplicate",
        "--remove-duplicates",
        dest="remove_duplicate",
        action="store_true",
        help="Safely merge duplicate non-empty YouTube video IDs.",
    )
    parser.add_argument(
        "--download-missing",
        action="store_true",
        help=(
            "Download rows whose stored path is NULL, empty, whitespace-only, "
            "missing, a directory, or a zero-byte file."
        ),
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Report duplicate, path, attempt, and unreferenced-media findings.",
    )
    return parser.parse_args(argv)


def _print_download_summary(result: DownloadCrawlResult) -> None:
    if (
        result.successes == 0
        and result.failures == 0
        and result.live_skipped == 0
        and result.deferred == 0
        and result.attempted_unique_videos == 0
        and result.remaining_unique_videos == 0
    ):
        print("No missing local video files to download.")
        return
    print(
        f"Downloaded {result.successes} missing video rows, "
        f"failed {result.failures}, live skipped {result.live_skipped}, "
        f"deferred {result.deferred}; "
        f"attempted {result.attempted_unique_videos} unique videos, "
        f"remaining {result.remaining_unique_videos} unique videos."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.audit and (args.remove_duplicate or args.download_missing):
        print(
            "Recovery argument error: --audit cannot be combined with "
            "--remove-duplicate/--remove-duplicates or --download-missing.",
            file=sys.stderr,
        )
        return 2
    if not args.remove_duplicate and not args.download_missing and not args.audit:
        print("No recovery action selected.")
        return 0

    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if not config.db_path.is_file():
        print(f"Database file not found: {config.db_path}", file=sys.stderr)
        return 2

    if args.audit:
        try:
            audit_result = audit_recovery_state(
                config.db_path,
                config.media_root,
            )
            print_audit_report(audit_result)
        except Exception as exc:  # noqa: BLE001 - normalize read/scan failures.
            print(f"Recovery audit error: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        core.configure(config.db_url)
    except Exception as exc:  # noqa: BLE001 - normalize CLI setup failures.
        print(f"Recovery error: {exc}", file=sys.stderr)
        return 1
    if args.remove_duplicate:
        try:
            result = remove_duplicate_videos(
                config.db_path,
                config.backup_root,
                config.media_root,
            )
        except DuplicateRemovalConflictError as exc:
            conflict_group_count = len(
                {conflict.video_id for conflict in exc.conflicts}
            )
            print(
                "Recovery conflict: "
                f"{conflict_group_count} duplicate groups, "
                f"{len(exc.conflicts)} conflicts. {exc}",
                file=sys.stderr,
            )
            return 1
        except RecoveryError as exc:
            print(f"Recovery error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - CLI reports unexpected DB failures.
            print(f"Recovery error: {exc}", file=sys.stderr)
            return 1

        if result.groups_removed == 0:
            print("No duplicate videos found.")
        else:
            print(
                f"Removed {result.rows_removed} duplicate video rows from "
                f"{result.groups_removed} groups. Backup: {result.backup_path}"
            )

    if args.download_missing:
        try:
            download_result = download_missing_videos(config.media_root)
        except RecoveryError as exc:
            print(f"Recovery error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - CLI reports unexpected failures.
            print(f"Recovery error: {exc}", file=sys.stderr)
            return 1
        _print_download_summary(download_result)
        if download_result.total_failures or download_result.halt_error_type:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

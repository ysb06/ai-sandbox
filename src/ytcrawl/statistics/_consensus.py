from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from ytcrawl.statistics._common import resolve_media_path

DECISIVE_REVIEW_STATUSES = frozenset({"accepted", "rejected"})

LogicalVideoKey: TypeAlias = tuple[str, str | int | Path]


@dataclass(frozen=True, slots=True)
class ConsensusVideoRow:
    video_ref_id: int
    logical_key: LogicalVideoKey
    video_id: str | None
    stored_path: str | None
    duration: object


@dataclass(frozen=True, slots=True)
class LogicalDecisionCounts:
    accepted: int
    rejected: int

    @property
    def decisive(self) -> int:
        return self.accepted + self.rejected


@dataclass(frozen=True, slots=True)
class ReviewConsensusSnapshot:
    video_rows: tuple[ConsensusVideoRow, ...]
    decision_counts: dict[LogicalVideoKey, LogicalDecisionCounts]
    duplicate_reviewer_decisions_collapsed: int


def collect_review_consensus(
    db_path: str | Path,
    media_root: str | Path,
) -> ReviewConsensusSnapshot:
    """Read videos and each logical video's latest reviewer decisions."""
    selected_db_path = Path(db_path).expanduser().resolve()
    selected_media_root = Path(media_root).expanduser().resolve()
    raw_video_rows, review_rows = _read_review_data(selected_db_path)
    video_rows = tuple(
        _to_consensus_video_row(row, selected_media_root)
        for row in raw_video_rows
    )
    logical_key_by_video_ref = {
        row.video_ref_id: row.logical_key for row in video_rows
    }

    latest_decisions: dict[tuple[LogicalVideoKey, str], str] = {}
    duplicate_decisions = 0
    for _review_id, video_ref_id, username, status, _updated_at in review_rows:
        logical_key = logical_key_by_video_ref.get(video_ref_id)
        if logical_key is None:
            continue
        decision_key = (logical_key, username)
        if decision_key in latest_decisions:
            duplicate_decisions += 1
        latest_decisions[decision_key] = status

    mutable_counts: dict[LogicalVideoKey, list[int]] = {}
    for (logical_key, _username), status in latest_decisions.items():
        if status not in DECISIVE_REVIEW_STATUSES:
            continue
        counts = mutable_counts.setdefault(logical_key, [0, 0])
        if status == "accepted":
            counts[0] += 1
        else:
            counts[1] += 1

    decision_counts = {
        logical_key: LogicalDecisionCounts(
            accepted=counts[0],
            rejected=counts[1],
        )
        for logical_key, counts in mutable_counts.items()
    }
    return ReviewConsensusSnapshot(
        video_rows=video_rows,
        decision_counts=decision_counts,
        duplicate_reviewer_decisions_collapsed=duplicate_decisions,
    )


def _to_consensus_video_row(
    row: tuple[int, object, object, object],
    media_root: Path,
) -> ConsensusVideoRow:
    video_ref_id, raw_video_id, raw_stored_path, duration = row
    video_id = raw_video_id if isinstance(raw_video_id, str) else None
    logical_video_id = video_id if video_id is not None and video_id.strip() else ""
    stored_path = (
        raw_stored_path
        if isinstance(raw_stored_path, str) and raw_stored_path.strip()
        else None
    )
    if logical_video_id:
        logical_key: LogicalVideoKey = ("video_id", logical_video_id)
    elif stored_path is not None:
        logical_key = (
            "path",
            resolve_media_path(media_root, stored_path),
        )
    else:
        logical_key = ("video_ref_id", video_ref_id)
    return ConsensusVideoRow(
        video_ref_id=video_ref_id,
        logical_key=logical_key,
        video_id=video_id,
        stored_path=stored_path,
        duration=duration,
    )


def _read_review_data(
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

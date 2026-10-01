"""Manually register new clip candidates without changing existing candidates."""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import func, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.db import core
from ytcrawln.db.tables.clip_candidates import ClipCandidate
from ytcrawln.db.tables.face_in_video import FaceInVideo
from ytcrawln.db.tables.videos import Videos


class RegistrationValidationError(ValueError):
    """The configured database is not ready for candidate registration."""


@dataclass(frozen=True)
class RegistrationResult:
    source_candidates: int
    inserted_candidates: int

    @property
    def existing_candidates(self) -> int:
        return self.source_candidates - self.inserted_candidates


def register_clip_candidates(session: Session) -> RegistrationResult:
    """Insert new video/frame pairs into an already prepared candidate table.

    Read the source once so registration and counts use the same candidate set.
    The caller owns commit/rollback; existing IDs and timestamps never change.
    """
    statement = (
        select(
            FaceInVideo.video_ref_id,
            FaceInVideo.frame,
            func.min(FaceInVideo.time_sec).label("time_sec"),
        )
        .group_by(FaceInVideo.video_ref_id, FaceInVideo.frame)
        .order_by(FaceInVideo.video_ref_id, FaceInVideo.frame)
    )
    candidates = [dict(row) for row in session.execute(statement).mappings()]
    if not candidates:
        return RegistrationResult(source_candidates=0, inserted_candidates=0)

    # Core executemany avoids one SQL statement with parameters for every row.
    # Only the candidate's unique key may be ignored, never other constraints.
    result = session.connection().execute(
        insert(ClipCandidate.__table__).on_conflict_do_nothing(
            index_elements=["video_ref_id", "frame"]
        ),
        candidates,
        execution_options={"preserve_rowcount": True},
    )
    return RegistrationResult(
        source_candidates=len(candidates), inserted_candidates=result.rowcount
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Register clip candidates from all stored face detections. "
            "Existing candidate IDs and timestamps are preserved."
        ),
        epilog=(
            "Run from the project root after face analysis has finished. "
            "This command does not extract video files or save reviews."
        ),
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help="Configuration file (default: project config_ytcrawln.yaml).",
    )
    args = parser.parse_args(argv)

    try:
        config = get_config(args.config)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        database_path = config.db_path.expanduser().resolve()
        if not database_path.is_file():
            raise RegistrationValidationError(
                f"Database file not found: {database_path}"
            )

        core.configure(config.db_url)
        engine = core.get_engine()
        inspector = inspect(engine)
        for table_name in (Videos.__tablename__, FaceInVideo.__tablename__):
            if not inspector.has_table(table_name):
                raise RegistrationValidationError(
                    f"Database has no {table_name} table."
                )

        # Prepare only this table; a failed import can leave an empty table.
        ClipCandidate.__table__.create(engine, checkfirst=True)
        with core.session_scope() as session:
            result = register_clip_candidates(session)
    except RegistrationValidationError as exc:
        print(f"Registration validation error: {exc}", file=sys.stderr)
        return 2
    except (OSError, SQLAlchemyError, RuntimeError) as exc:
        print(f"Candidate registration error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Candidate registration interrupted; uncommitted inserts rolled back.",
            file=sys.stderr,
        )
        return 130

    # Report success only after the insertion transaction has committed.
    print(f"Database: {database_path}")
    print(f"Source candidates: {result.source_candidates}")
    print(f"Inserted candidates: {result.inserted_candidates}")
    print(f"Existing candidates: {result.existing_candidates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

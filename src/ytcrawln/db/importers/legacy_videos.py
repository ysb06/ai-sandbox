"""Append legacy videos with new IDs; repeated imports append the rows again."""

import argparse
import sqlite3
import sys
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.db import core
from ytcrawln.db.tables.videos import Video

COPY_COLUMNS = (
    "kind",
    "etag",
    "video_id",
    "title",
    "description",
    "publishTime",
    "path",
)
REQUIRED_COLUMNS = ("id", *COPY_COLUMNS)
BATCH_SIZE = 500


class ImportValidationError(ValueError):
    """An input database does not meet the import requirements."""


@dataclass(frozen=True)
class ImportResult:
    source_rows: int
    inserted_rows: int
    id_mapping: dict[int, int]


def _check_distinct_files(source: Path, destination: Path) -> None:
    destination = destination.expanduser().resolve()
    if source == destination or (
        destination.exists() and source.samefile(destination)
    ):
        raise ImportValidationError("Source and destination must be different files.")


def _check_columns(columns: set[str], *, label: str) -> None:
    missing = set(REQUIRED_COLUMNS) - columns
    if missing:
        raise ImportValidationError(
            f"{label} videos table is missing columns: {', '.join(sorted(missing))}"
        )


@contextmanager
def _open_source(source: Path) -> Generator[sqlite3.Connection, None, None]:
    if not source.is_file():
        raise ImportValidationError(f"Source database file not found: {source}")

    connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'videos'"
        ).fetchone()
        if table is None:
            raise ImportValidationError("Source database has no videos table.")
        _check_columns(
            {row["name"] for row in connection.execute('PRAGMA table_info("videos")')},
            label="Source",
        )
        yield connection
    finally:
        connection.close()


def _import_rows(source: sqlite3.Connection, session: Session) -> ImportResult:
    inspector = inspect(session.connection())
    if not inspector.has_table("videos"):
        raise ImportValidationError("Destination database has no videos table.")
    _check_columns(
        {column["name"] for column in inspector.get_columns("videos")},
        label="Destination",
    )

    columns = ", ".join(f'"{column}"' for column in REQUIRED_COLUMNS)
    cursor = source.execute(f'SELECT {columns} FROM "videos" ORDER BY "id"')
    source_rows = 0
    id_mapping: dict[int, int] = {}
    while rows := cursor.fetchmany(BATCH_SIZE):
        batch: list[tuple[int, Video]] = []
        batch_ids: set[int] = set()
        for row in rows:
            source_id = row["id"]
            if (
                not isinstance(source_id, int)
                or source_id in id_mapping
                or source_id in batch_ids
            ):
                raise ImportValidationError(
                    f"Source videos.id must contain unique integers; found {source_id!r}."
                )
            batch_ids.add(source_id)
            # Never assign the legacy primary key to the new model.
            video = Video(**{column: row[column] for column in COPY_COLUMNS})
            batch.append((source_id, video))

        session.add_all([video for _, video in batch])
        session.flush()
        id_mapping.update({source_id: video.id for source_id, video in batch})
        source_rows += len(rows)

    return ImportResult(
        source_rows=source_rows,
        inserted_rows=len(id_mapping),
        id_mapping=id_mapping,
    )


def import_legacy_videos(source_db: Path, session: Session) -> ImportResult:
    """Append all source rows and flush, leaving commit/rollback to the caller.

    The destination videos table must already exist. Existing rows are neither
    updated nor deduplicated. The returned ID mapping is valid after commit;
    callers must roll back the transaction if this function raises an error.
    """
    source = Path(source_db).expanduser().resolve()
    with _open_source(source) as connection:
        url = session.get_bind().engine.url
        if url.get_backend_name() == "sqlite" and url.database not in (
            None, "", ":memory:"
        ):
            _check_distinct_files(source, Path(url.database))
        return _import_rows(connection, session)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Append legacy videos to the configured database with new IDs. "
            "Repeated runs append duplicate videos. Media files are not copied."
        ),
    )
    parser.add_argument(
        "--source-db", type=Path, required=True, help="Legacy SQLite database to read."
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
        source = args.source_db.expanduser().resolve()
        destination = config.db_path.expanduser().resolve()
        # Validate and hold a read-only source snapshot before preparing the target.
        with _open_source(source) as connection:
            _check_distinct_files(source, destination)
            core.configure(config.db_url)
            inspector = inspect(core.get_engine())
            if inspector.has_table("videos"):
                _check_columns(
                    {column["name"] for column in inspector.get_columns("videos")},
                    label="Destination",
                )
            core.create_all()
            with core.session_scope() as session:
                result = _import_rows(connection, session)
    except ImportValidationError as exc:
        print(f"Import validation error: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, SQLAlchemyError, RuntimeError) as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        return 1

    print(f"Source DB: {source}")
    print(f"Destination DB: {destination}")
    print(f"Source rows: {result.source_rows}")
    print(f"Inserted rows: {result.inserted_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

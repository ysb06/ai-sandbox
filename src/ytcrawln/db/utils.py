"""Preview or delete videos and their related DB rows; never delete media."""

import argparse
import os
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

import yaml

from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.video_ids import VideoIDValidationError, read_video_ids, unique_video_ids

BATCH_SIZE = 500
# Children first. These are the only tables and columns this utility may delete.
DELETE_COLUMNS = {
    "clip_reviews": "clip_candidate_id",
    "clip_candidates": "video_ref_id",
    "face_in_videos": "video_ref_id",
    "videos_detail": "video_ref_id",
    "video_reviews": "video_ref_id",
    "videos": "id",
}
T = TypeVar("T")


class VideoDeletionValidationError(ValueError):
    """Input, configuration, or schema is unsuitable for safe deletion."""


class VideoDeletionError(RuntimeError):
    """Backup or deletion failed; uncommitted deletes have been rolled back."""


@dataclass(frozen=True)
class VideoDeletionResult:
    """Row counts are planned unless executed is true; no media is removed."""

    database_path: Path
    input_ids: int
    unique_ids: int
    duplicate_ids: int
    matched_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    video_ref_ids: tuple[int, ...]
    row_counts: dict[str, int]
    absent_tables: tuple[str, ...]
    executed: bool = False
    backup_path: Path | None = None


@dataclass(frozen=True)
class _DeletionTargets:
    result: VideoDeletionResult
    keys: dict[str, tuple[int, ...]]


def _chunks(values: Sequence[T]) -> Iterator[Sequence[T]]:
    for start in range(0, len(values), BATCH_SIZE):
        yield values[start : start + BATCH_SIZE]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _where_in(column: str, size: int) -> str:
    if size <= 0:
        raise ValueError("A deletion predicate must have at least one key.")
    # YouTube IDs are case-sensitive even if an older schema uses NOCASE.
    return f"{_quote_identifier(column)} COLLATE BINARY IN ({','.join('?' for _ in range(size))})"


def _database_path(db_path: str | Path) -> Path:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise VideoDeletionValidationError(f"Database file not found: {path}")
    return path


def _connect(path: Path, *, mode: str) -> sqlite3.Connection:
    # Neither preview nor deletion may create a missing source database.
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode={mode}", uri=True, isolation_level=None, timeout=5.0
    )
    connection.row_factory = sqlite3.Row
    return connection


def _check_schema(connection: sqlite3.Connection) -> set[str]:
    objects = {
        row["name"]: row["type"]
        for row in connection.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    if objects.get("videos") != "table":
        raise VideoDeletionValidationError("Database has no videos table.")

    present = set(DELETE_COLUMNS).intersection(objects)
    for name in present:
        if objects[name] != "table":
            raise VideoDeletionValidationError(f"Expected a table, not a view: {name}")
        columns = list(
            connection.execute(f"PRAGMA table_info({_quote_identifier(name)})")
        )
        required = {"id", DELETE_COLUMNS[name]}
        if name == "videos":
            required.add("video_id")
        missing = required - {column["name"] for column in columns}
        if missing:
            raise VideoDeletionValidationError(
                f"Missing columns in {name}: {', '.join(sorted(missing))}"
            )
        if [column["name"] for column in columns if column["pk"]] != ["id"]:
            raise VideoDeletionValidationError(f"Expected a single id primary key: {name}")

    if "clip_reviews" in present and "clip_candidates" not in present:
        raise VideoDeletionValidationError(
            "clip_reviews exists without clip_candidates; cannot identify related reviews."
        )

    allowed_links = {
        (name, column, "videos", "id")
        for name, column in DELETE_COLUMNS.items()
        if column == "video_ref_id"
    }
    allowed_links.add(("clip_reviews", "clip_candidate_id", "clip_candidates", "id"))
    for name, kind in objects.items():
        if kind != "table":
            continue
        for fk in connection.execute(f"PRAGMA foreign_key_list({_quote_identifier(name)})"):
            parent = fk["table"].lower()
            if parent not in present:
                continue
            link = (name, fk["from"].lower(), parent, (fk["to"] or "id").lower())
            if fk["seq"] != 0 or link not in allowed_links:
                raise VideoDeletionValidationError(
                    f"Unsupported foreign-key dependency: {name}.{fk['from']} -> {parent}. "
                    "Update the explicit deletion rules before proceeding."
                )

    # A trigger could delete unrelated rows, even when all our predicates are bounded.
    for trigger in connection.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'"
    ):
        if trigger["tbl_name"].lower() in present:
            raise VideoDeletionValidationError(
                f"Unsupported trigger on {trigger['tbl_name']}: {trigger['name']}. "
                "Review its effects before proceeding."
            )
    return present


def _count_rows(
    connection: sqlite3.Connection, name: str, keys: Sequence[int]
) -> int:
    return sum(
        connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(name)} "
            f"WHERE {_where_in(DELETE_COLUMNS[name], len(batch))}",
            batch,
        ).fetchone()[0]
        for batch in _chunks(keys)
    )


def _collect_targets(
    connection: sqlite3.Connection,
    path: Path,
    ids: Sequence[str],
    input_count: int,
) -> _DeletionTargets:
    present = _check_schema(connection)
    rows = []
    for batch in _chunks(ids):
        rows.extend(
            connection.execute(
                f"SELECT id, video_id FROM videos WHERE {_where_in('video_id', len(batch))}",
                batch,
            )
        )
    video_refs = tuple(sorted(row["id"] for row in rows))
    matched = {row["video_id"] for row in rows}
    clip_refs: list[int] = []
    if "clip_candidates" in present:
        for batch in _chunks(video_refs):
            clip_refs.extend(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM clip_candidates "
                    f"WHERE {_where_in('video_ref_id', len(batch))}",
                    batch,
                )
            )
    keys = {
        name: tuple(clip_refs) if name == "clip_reviews" else video_refs
        for name in DELETE_COLUMNS
        if name in present
    }
    counts = {name: _count_rows(connection, name, values) for name, values in keys.items()}
    return _DeletionTargets(
        result=VideoDeletionResult(
            database_path=path,
            input_ids=input_count,
            unique_ids=len(ids),
            duplicate_ids=input_count - len(ids),
            matched_ids=tuple(value for value in ids if value in matched),
            missing_ids=tuple(value for value in ids if value not in matched),
            video_ref_ids=video_refs,
            row_counts=counts,
            absent_tables=tuple(name for name in DELETE_COLUMNS if name not in present),
        ),
        keys=keys,
    )


def preview_video_deletion(
    db_path: str | Path, video_ids: Sequence[str]
) -> VideoDeletionResult:
    """Inspect a consistent read-only snapshot, without creating backups or tables."""
    ids = unique_video_ids(video_ids)
    path = _database_path(db_path)
    try:
        with closing(_connect(path, mode="ro")) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            return _collect_targets(connection, path, ids, len(video_ids)).result
    except sqlite3.DatabaseError as exc:
        raise VideoDeletionValidationError(f"Unable to inspect the database: {exc}") from exc


def _backup_database(path: Path, backup_root: Path) -> Path:
    """Create and validate a standalone pre-deletion snapshot on another connection."""
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = backup_root / f"ytcrawln-before-delete-{stamp}-{uuid4().hex}.sqlite3"
    temporary_path = backup_path.with_suffix(".sqlite3.part")
    owned = False

    def check_backup_progress(status: int, remaining: int, total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise sqlite3.OperationalError("Backup database is busy or locked.")

    try:
        descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        owned = True
        os.close(descriptor)
        with (
            closing(_connect(path, mode="ro")) as source,
            closing(_connect(temporary_path, mode="rw")) as destination,
        ):
            source.backup(destination, pages=1024, progress=check_backup_progress)
            # The backup must not depend on a separate WAL file when published.
            mode = destination.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            if mode != "delete":
                raise sqlite3.DatabaseError("Unable to make a standalone backup.")
            checks = [row[0] for row in destination.execute("PRAGMA quick_check")]
            if checks != ["ok"]:
                raise sqlite3.DatabaseError("Backup quick_check failed.")
        with temporary_path.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
        os.replace(temporary_path, backup_path)
        return backup_path
    finally:
        if owned:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def delete_videos(
    db_path: str | Path,
    video_ids: Sequence[str],
    *,
    backup_root: str | Path | None = None,
) -> VideoDeletionResult:
    """Back up and atomically remove related DB rows, without touching media.

    Review/crawler/importer coordination remains the operator's responsibility.
    The write reservation spans target selection, backup, deletes, and commit.
    Each call owns its connection; no global DB configuration is changed.
    """
    ids = unique_video_ids(video_ids)
    path = _database_path(db_path)
    # Validate before opening a writer or creating any backup directories.
    preview_video_deletion(path, ids)
    root = (
        Path(backup_root).expanduser().resolve()
        if backup_root is not None
        else path.parent / "Backup"
    )
    backup_path = None
    stage = "Opening database"
    try:
        with closing(_connect(path, mode="rw")) as connection:
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise sqlite3.DatabaseError("Foreign-key enforcement is unavailable.")
                stage = "Acquiring write transaction"
                connection.execute("BEGIN IMMEDIATE")
                stage = "Checking deletion targets"
                targets = _collect_targets(connection, path, ids, len(video_ids))
                if not targets.result.video_ref_ids:
                    connection.rollback()
                    return replace(targets.result, executed=True)

                stage = "Creating backup"
                # Do NOT call backup() on the connection holding BEGIN IMMEDIATE.
                # A separate reader sees the committed state while other writers wait.
                backup_path = _backup_database(path, root)
                for name, keys in targets.keys.items():
                    stage = f"Deleting {name}"
                    deleted = 0
                    for batch in _chunks(keys):
                        cursor = connection.execute(
                            f"DELETE FROM {_quote_identifier(name)} "
                            f"WHERE {_where_in(DELETE_COLUMNS[name], len(batch))}",
                            batch,
                        )
                        deleted += cursor.rowcount
                    if deleted != targets.result.row_counts[name]:
                        raise sqlite3.DatabaseError(f"Unexpected deleted row count in {name}.")

                stage = "Checking remaining rows"
                for name, keys in targets.keys.items():
                    if _count_rows(connection, name, keys):
                        raise sqlite3.DatabaseError(f"Related rows remain in {name}.")
                stage = "Committing deletion"
                connection.commit()
                return replace(targets.result, executed=True, backup_path=backup_path)
            finally:
                # Includes KeyboardInterrupt/SystemExit, not only ordinary errors.
                if connection.in_transaction:
                    connection.rollback()
    except VideoDeletionValidationError:
        raise
    except Exception as exc:
        backup_note = f" Backup retained: {backup_path}" if backup_path else ""
        raise VideoDeletionError(f"{stage} failed: {exc}.{backup_note}") from exc


def _print_result(result: VideoDeletionResult) -> None:
    print(f"Database: {result.database_path}")
    print(f"Mode: {'EXECUTED' if result.executed else 'PREVIEW (no changes)'}")
    print(f"Input IDs: {result.input_ids}")
    print(f"Unique IDs: {result.unique_ids}")
    print(f"Duplicate input IDs removed: {result.duplicate_ids}")
    print(f"Matched YouTube IDs: {len(result.matched_ids)}")
    print(f"Matched videos rows: {len(result.video_ref_ids)}")
    print(f"IDs not found: {len(result.missing_ids)}")
    for video_id in result.missing_ids:
        print(f"  Not found: {video_id}")
    label = "Deleted" if result.executed else "Would delete"
    for name in DELETE_COLUMNS:
        if name in result.absent_tables:
            print(f"{name}: absent (skipped)")
        else:
            print(f"{label} {name}: {result.row_counts[name]}")
    if result.backup_path is not None:
        print(f"Backup: {result.backup_path}")
    if not result.video_ref_ids:
        print("No matching videos to delete.")
    elif not result.executed:
        print("Preview only. Add --execute to back up and delete these database rows.")
    print("Media files, remote files, and Review caches are not deleted.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    delete = commands.add_parser(
        "delete-videos", help="Preview or delete videos and all supported related rows."
    )
    delete.add_argument("--ids-file", type=Path, required=True)
    delete.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    delete.add_argument(
        "--execute", action="store_true", help="Back up and delete; default is preview only."
    )
    args = parser.parse_args(argv)
    try:
        try:
            ids = read_video_ids(args.ids_file)
            config_path = args.config.expanduser().resolve()
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            # The permissive common loader would otherwise turn a missing root into '.'.
            if (
                not isinstance(raw, Mapping)
                or not isinstance(raw.get("DATA_ROOT"), str)
                or not raw["DATA_ROOT"].strip()
                or "://" in raw["DATA_ROOT"]
            ):
                raise VideoDeletionValidationError("DATA_ROOT must be a nonempty local path.")
            config = get_config(config_path)
            path = _database_path(config.db_path)
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
            raise VideoDeletionValidationError(str(exc)) from exc

        if args.execute:
            result = delete_videos(path, ids, backup_root=config.backup_root)
        else:
            result = preview_video_deletion(path, ids)
        _print_result(result)
        return 0
    except (VideoIDValidationError, VideoDeletionValidationError) as exc:
        print(f"Delete validation error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Delete interrupted. Uncommitted deletion was rolled back.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Delete error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

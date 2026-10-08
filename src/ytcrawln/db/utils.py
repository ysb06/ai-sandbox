import argparse
import os
import signal
import sqlite3
import stat
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

import yaml

from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.utils import is_sftp_source, resolve_video_source
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


class MediaCleanupValidationError(ValueError):
    """Configuration, database, or paths are unsuitable for media cleanup."""


@dataclass(frozen=True)
class MediaCleanupResult:
    """File counts only; execution never changes database rows or creates backups."""

    database_path: Path
    media_root: Path
    video_rows: int
    referenced_paths: int
    path_diagnostics: dict[str, int]
    scanned_files: int
    kept_files: int
    scanned_directories: int
    skipped_symlinks: int
    skipped_special_files: int
    candidate_paths: tuple[Path, ...]
    candidate_bytes: int
    executed: bool = False
    deleted_files: int = 0
    deleted_bytes: int = 0


class MediaCleanupError(RuntimeError):
    """Inspection/deletion failed; result records any irreversible partial deletion."""

    def __init__(
        self, message: str, *, result: MediaCleanupResult | None = None
    ) -> None:
        super().__init__(message)
        self.result = result


class MediaCleanupInterrupted(KeyboardInterrupt):
    """An interrupted deletion retains its partial result; files cannot roll back."""

    def __init__(self, message: str, result: MediaCleanupResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class _MediaCleanupTargets:
    result: MediaCleanupResult
    files: dict[Path, os.stat_result]
    directories: dict[Path, os.stat_result]


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


def _path_inside_root(path: Path, root: Path, root_info: os.stat_result) -> bool:
    if path.is_relative_to(root):
        return True
    # resolve() preserves case on macOS. Compare directory identities too, so
    # differently-cased paths on case-insensitive volumes cannot bypass guards.
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.stat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (
            root_info.st_dev,
            root_info.st_ino,
        ):
            return True
    return False


def _media_cleanup_paths(db_path: str | Path, media_root: str | Path) -> tuple[Path, Path]:
    try:
        if not isinstance(media_root, (str, Path)) or not str(media_root).strip():
            raise MediaCleanupValidationError("MEDIA_ROOT must be a nonempty local path.")
        if "://" in str(media_root):
            raise MediaCleanupValidationError(
                "MEDIA_ROOT must be local; SFTP cleanup is not supported. "
                "Run on the server with a local MEDIA_ROOT configuration."
            )
        database = _database_path(db_path)
        root = Path(media_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise MediaCleanupValidationError(f"MEDIA_ROOT is not a directory: {root}")
        root_info = root.stat()
        # Also exclude ancestors of these broad locations, not just exact matches.
        protected = (Path.home().resolve(), DEFAULT_CONFIG_PATH.parent.resolve())
        if root == Path(root.anchor) or any(
            _path_inside_root(path, root, root_info) for path in protected
        ):
            raise MediaCleanupValidationError(f"Unsafe, overly broad MEDIA_ROOT: {root}")
        if _path_inside_root(database, root, root_info):
            raise MediaCleanupValidationError("MEDIA_ROOT must not contain the database.")
        return database, root
    except (OSError, RuntimeError, ValueError) as exc:
        raise MediaCleanupValidationError(str(exc)) from exc


def _media_references(
    database: Path, root: Path
) -> tuple[int, set[Path], set[tuple[int, int]], dict[str, int]]:
    references: set[Path] = set()
    identities: set[tuple[int, int]] = set()
    diagnostics = dict.fromkeys(
        ("empty", "sftp", "outside_root", "missing", "not_regular", "duplicate"), 0
    )
    rows = 0
    try:
        root_info = root.stat()
        with closing(_connect(database, mode="ro")) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            table = connection.execute(
                "SELECT type FROM sqlite_master WHERE name = 'videos' COLLATE NOCASE"
            ).fetchone()
            if table is None or table[0] != "table":
                raise MediaCleanupValidationError("Database has no videos table.")
            columns = {
                row["name"].lower()
                for row in connection.execute('PRAGMA table_info("videos")')
            }
            if "path" not in columns:
                raise MediaCleanupValidationError("Missing videos.path column.")
            for row in connection.execute('SELECT path FROM "videos"'):
                rows += 1
                value = row[0]
                if value is None or value == "":
                    diagnostics["empty"] += 1
                    continue
                if not isinstance(value, str) or "\x00" in value:
                    raise MediaCleanupValidationError("videos.path contains an invalid path value.")
                source = resolve_video_source(root, value)
                if is_sftp_source(source):
                    diagnostics["sftp"] += 1
                    continue
                if "://" in value:
                    raise MediaCleanupValidationError("videos.path contains an unsupported URL.")
                resolved = Path(source).resolve()
                if not _path_inside_root(resolved, root, root_info):
                    diagnostics["outside_root"] += 1
                    continue
                if resolved in references:
                    diagnostics["duplicate"] += 1
                references.add(resolved)
                try:
                    info = resolved.stat()
                except FileNotFoundError:
                    diagnostics["missing"] += 1
                else:
                    if not stat.S_ISREG(info.st_mode):
                        diagnostics["not_regular"] += 1
                    else:
                        identities.add((info.st_dev, info.st_ino))
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        raise MediaCleanupValidationError(f"Unable to inspect media references: {exc}") from exc
    return rows, references, identities, diagnostics


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # atime changes on reads; ctime/nlink may change when another hardlink is removed.
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def _collect_media_cleanup(db_path: str | Path, media_root: str | Path) -> _MediaCleanupTargets:
    database, root = _media_cleanup_paths(db_path, media_root)
    rows, references, identities, diagnostics = _media_references(database, root)
    files: dict[Path, os.stat_result] = {}
    directories: dict[Path, os.stat_result] = {}
    scanned = kept = symlinks = special = 0
    stage_path = root

    def raise_walk_error(error: OSError) -> None:
        # os.walk silently skips unreadable directories without this callback.
        raise error

    try:
        for dirname, dirnames, filenames in os.walk(
            root, topdown=True, followlinks=False, onerror=raise_walk_error
        ):
            stage_path = directory = Path(dirname)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or directory.resolve(strict=True) != directory:
                raise OSError(f"Directory changed or became a symlink: {directory}")
            previous = directories.get(directory)
            if previous is not None and _directory_identity(previous) != _directory_identity(info):
                raise OSError(f"Directory changed during scan: {directory}")
            directories[directory] = info
            for name in sorted(dirnames):
                stage_path = child = directory / name
                child_info = child.lstat()
                if stat.S_ISLNK(child_info.st_mode):
                    symlinks += 1
                    dirnames.remove(name)
                elif not stat.S_ISDIR(child_info.st_mode):
                    raise OSError(f"Directory changed during scan: {child}")
                else:
                    directories[child] = child_info
            dirnames.sort()
            for name in sorted(filenames):
                stage_path = file_path = directory / name
                file_info = file_path.lstat()
                if stat.S_ISLNK(file_info.st_mode):
                    symlinks += 1
                    continue
                if stat.S_ISDIR(file_info.st_mode):
                    raise OSError(f"File became a directory during scan: {file_path}")
                if not stat.S_ISREG(file_info.st_mode):
                    special += 1
                    continue
                if file_path.resolve(strict=True) != file_path:
                    raise OSError(f"File location changed during scan: {file_path}")
                scanned += 1
                if file_path in references or (file_info.st_dev, file_info.st_ino) in identities:
                    kept += 1
                else:
                    files[file_path] = file_info
    except (OSError, RuntimeError, ValueError) as exc:
        raise MediaCleanupError(
            f"Media scan failed at {stage_path}: {exc}. No files were deleted."
        ) from exc
    return _MediaCleanupTargets(
        result=MediaCleanupResult(
            database_path=database,
            media_root=root,
            video_rows=rows,
            referenced_paths=len(references),
            path_diagnostics=diagnostics,
            scanned_files=scanned,
            kept_files=kept,
            scanned_directories=len(directories),
            skipped_symlinks=symlinks,
            skipped_special_files=special,
            candidate_paths=tuple(files),
            candidate_bytes=sum(info.st_size for info in files.values()),
        ),
        files=files,
        directories=directories,
    )


def preview_media_cleanup(db_path: str | Path, media_root: str | Path) -> MediaCleanupResult:
    """Enumerate local files without deleting media or changing database rows."""
    return _collect_media_cleanup(db_path, media_root).result


def _unlink_media_candidate(
    targets: _MediaCleanupTargets, path: Path, deleted_paths: list[Path]
) -> None:
    root = targets.result.media_root
    relative = path.relative_to(root)
    if path.resolve(strict=True) != path:
        raise OSError("File location changed after scanning.")
    # Pin every parent and refuse symlinks. unlink(dir_fd=...) cannot be redirected
    # through a replaced directory symlink between the checks and the unlink call.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = root
        descriptor = os.open(root, flags)
        stack.callback(os.close, descriptor)
        if _directory_identity(os.fstat(descriptor)) != _directory_identity(
            targets.directories[root]
        ):
            raise OSError("MEDIA_ROOT changed after scanning.")
        for name in relative.parts[:-1]:
            descriptor = os.open(name, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
            directory = directory / name
            if _directory_identity(os.fstat(descriptor)) != _directory_identity(
                targets.directories[directory]
            ):
                raise OSError(f"Parent directory changed after scanning: {directory}")
        current = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or _file_identity(current) != _file_identity(
            targets.files[path]
        ):
            raise OSError("File changed after scanning; refusing to delete it.")
        # Defer terminal Ctrl+C just across unlink + bookkeeping. Restore the
        # original thread mask even on failure; cleanup/reporting remains interruptible.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            os.unlink(relative.name, dir_fd=descriptor)
            deleted_paths.append(path)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def cleanup_media(db_path: str | Path, media_root: str | Path) -> MediaCleanupResult:
    """Recompute candidates, then permanently unlink them; never modify the DB.

    Stop writers before calling. There is no backup or rollback. Failures and
    interrupts expose partial counts via their result attribute.
    """
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(signal, "pthread_sigmask")
        or not {os.open, os.stat, os.unlink}.issubset(os.supports_dir_fd)
    ):
        raise MediaCleanupValidationError(
            "Safe cleanup requires POSIX directory-relative file operations."
        )
    targets = _collect_media_cleanup(db_path, media_root)
    result = replace(targets.result, executed=True)
    deleted_paths: list[Path] = []

    def current_result() -> MediaCleanupResult:
        return replace(
            result,
            deleted_files=len(deleted_paths),
            deleted_bytes=sum(targets.files[item].st_size for item in deleted_paths),
        )

    if result.candidate_paths and result.kept_files == 0:
        print(
            "WARNING: No regular files are preserved; ALL scanned regular files will be deleted.",
            file=sys.stderr,
            flush=True,
        )
    path = None
    try:
        for path in result.candidate_paths:
            _unlink_media_candidate(targets, path, deleted_paths)
        return current_result()
    except KeyboardInterrupt as exc:
        raise MediaCleanupInterrupted(
            f"Media cleanup interrupted at {path}. Deleted files cannot be rolled back.",
            current_result(),
        ) from exc
    except Exception as exc:
        raise MediaCleanupError(
            f"Media deletion failed at {path}: {exc}. Deleted files cannot be rolled back.",
            result=current_result(),
        ) from exc


def _print_media_result(result: MediaCleanupResult) -> None:
    print(f"Database: {result.database_path}")
    print(f"MEDIA_ROOT: {result.media_root}")
    mode = "EXECUTION (permanent deletion)" if result.executed else "PREVIEW (no changes)"
    print(f"Mode: {mode}")
    print(f"Videos rows: {result.video_rows}")
    print(f"Unique local references inside MEDIA_ROOT: {result.referenced_paths}")
    for label, count in result.path_diagnostics.items():
        print(f"Path diagnostics ({label}, rows): {count}")
    print(f"Scanned regular files: {result.scanned_files}")
    print(f"Kept files: {result.kept_files}")
    print(f"Directories preserved: {result.scanned_directories}")
    print(f"Symlinks skipped: {result.skipped_symlinks}")
    print(f"Special files skipped: {result.skipped_special_files}")
    print(f"Planned deletions: {len(result.candidate_paths)}")
    print(f"Candidate file size total: {result.candidate_bytes} bytes")
    print(f"Deleted files: {result.deleted_files}")
    print(f"Deleted file size total: {result.deleted_bytes} bytes")
    print("Size totals do not guarantee equivalent disk space will be freed.")
    if result.candidate_paths and result.kept_files == 0:
        print("WARNING: No files are preserved; ALL scanned regular files are deletion candidates.")
    if not result.executed:
        for path in result.candidate_paths:
            print(f"  Would delete: {path.relative_to(result.media_root)}")
        print("Preview only. Add --execute for permanent deletion without a backup.")
    print("Database rows and videos.path are unchanged. Directories and symlinks are preserved.")


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


def _run_media_cleanup(args: argparse.Namespace) -> int:
    result = None
    try:
        try:
            config_path = args.config.expanduser().resolve()
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            for key in ("DATA_ROOT", "MEDIA_ROOT"):
                if (
                    not isinstance(raw, Mapping)
                    or not isinstance(raw.get(key), str)
                    or not raw[key].strip()
                    or "\x00" in raw[key]
                    or "://" in raw[key]
                ):
                    raise MediaCleanupValidationError(
                        f"{key} must be a nonempty local path. SFTP cleanup is not supported; "
                        "run on the server with a local MEDIA_ROOT configuration."
                    )
            config = get_config(config_path)
        except (
            OSError, ValueError, TypeError, AttributeError, RuntimeError, yaml.YAMLError
        ) as exc:
            raise MediaCleanupValidationError(str(exc)) from exc
        operation = cleanup_media if args.execute else preview_media_cleanup
        result = operation(config.db_path, config.media_root)
        _print_media_result(result)
        return 0
    except MediaCleanupValidationError as exc:
        print(f"Cleanup validation error: {exc}", file=sys.stderr)
        return 2
    except MediaCleanupInterrupted as exc:
        _print_media_result(exc.result)
        print(str(exc), file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        if result is not None:
            print(f"Confirmed deleted files: {result.deleted_files}", file=sys.stderr)
        print("Media cleanup interrupted. Deleted files cannot be rolled back.", file=sys.stderr)
        return 130
    except MediaCleanupError as exc:
        if exc.result is not None:
            _print_media_result(exc.result)
        print(f"Cleanup error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Cleanup error: {exc}", file=sys.stderr)
        return 1


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
    cleanup = commands.add_parser(
        "cleanup-media", help="Preview or permanently delete unreferenced local media files."
    )
    cleanup.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    cleanup.add_argument(
        "--execute", action="store_true", help="Permanently delete files; default is preview only."
    )
    args = parser.parse_args(argv)
    if args.command == "cleanup-media":
        return _run_media_cleanup(args)
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

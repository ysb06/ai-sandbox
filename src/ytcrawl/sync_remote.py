from __future__ import annotations

import argparse
import errno
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from ytcrawl.config import AppConfig, ConfigError, get_config

SYNC_PROTOCOL_VERSION = 1
MEDIA_RSYNC_PARTIAL_DIRNAME = "MediaRsync"
_ARTIFACT_PREFIX = "ytcrawl-db-"
_ARTIFACT_SUFFIX = ".sqlite3"
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")

PeerRole = Literal["source", "destination"]
SyncComponent = Literal["db", "media", "all"]


class PeerSyncError(RuntimeError):
    """Raised when a synchronization peer cannot complete an operation."""


class PeerSourceMissingError(PeerSyncError):
    """Raised when a selected source component does not exist."""


def artifact_filename(artifact_id: str) -> str:
    """Return the fixed staging filename for a validated operation id."""
    if not artifact_id or len(artifact_id) > 128 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for character in artifact_id
    ):
        raise PeerSyncError(
            "Artifact id must contain only lowercase letters, digits, and '-'."
        )
    return f"{_ARTIFACT_PREFIX}{artifact_id}{_ARTIFACT_SUFFIX}"


def artifact_path(config: AppConfig, artifact_id: str) -> Path:
    """Return an artifact path contained in the configured temporary root."""
    return config.temp_root / artifact_filename(artifact_id)


def inspect_peer(
    config: AppConfig,
    *,
    expected_data_root: str | Path,
) -> dict[str, object]:
    """Describe a peer after confirming its configured DATA_ROOT identity."""
    _verify_data_root(config, expected_data_root)
    return {
        "protocol": SYNC_PROTOCOL_VERSION,
        "data_root": config.data_root.as_posix(),
        "db_path": config.db_path.as_posix(),
        "media_root": config.media_root.as_posix(),
        "db_exists": config.db_path.is_file(),
        "media_exists": config.media_root.is_dir(),
        "temp_root": config.temp_root.as_posix(),
        "backup_root": config.backup_root.as_posix(),
    }


def preflight_peer(
    config: AppConfig,
    *,
    expected_data_root: str | Path,
    role: PeerRole,
    component: SyncComponent,
) -> dict[str, object]:
    """Validate source inputs or prepare destination directories."""
    _verify_data_root(config, expected_data_root)
    if component not in ("db", "media", "all"):
        raise PeerSyncError(f"Unsupported synchronization component: {component}")
    include_db = component in ("db", "all")
    include_media = component in ("media", "all")

    if role == "source":
        if include_db and not config.db_path.is_file():
            raise PeerSourceMissingError(
                f"Source database not found: {config.db_path}"
            )
        if include_media and not config.media_root.is_dir():
            raise PeerSourceMissingError(
                f"Source media directory not found: {config.media_root}"
            )
    elif role == "destination":
        config.data_root.mkdir(parents=True, exist_ok=True)
        if include_db:
            config.temp_root.mkdir(parents=True, exist_ok=True)
            config.backup_root.mkdir(parents=True, exist_ok=True)
            _require_same_filesystem(config.data_root, config.temp_root)
        if include_media:
            config.media_root.mkdir(parents=True, exist_ok=True)
            (config.temp_root / MEDIA_RSYNC_PARTIAL_DIRNAME).mkdir(
                parents=True,
                exist_ok=True,
            )
    else:
        raise PeerSyncError(f"Unsupported peer role: {role}")

    result = inspect_peer(config, expected_data_root=expected_data_root)
    result.update({"role": role, "component": component})
    return result


def snapshot_database(
    config: AppConfig,
    *,
    expected_data_root: str | Path,
    artifact_id: str,
) -> dict[str, object]:
    """Create a consistent SQLite snapshot in the peer temporary root."""
    _verify_data_root(config, expected_data_root)
    if not config.db_path.is_file():
        raise PeerSyncError(f"Source database not found: {config.db_path}")

    config.temp_root.mkdir(parents=True, exist_ok=True)
    destination = artifact_path(config, artifact_id)
    if destination.exists():
        raise PeerSyncError(f"Staging artifact already exists: {destination}")

    _backup_database(config.db_path, destination)
    return {
        "artifact": destination.name,
        "size": destination.stat().st_size,
    }


def promote_database(
    config: AppConfig,
    *,
    expected_data_root: str | Path,
    artifact_id: str,
) -> dict[str, object]:
    """Back up the destination DB and atomically promote a staged snapshot."""
    _verify_data_root(config, expected_data_root)
    stage = artifact_path(config, artifact_id)
    if not stage.is_file():
        raise PeerSyncError(f"Staged database not found: {stage}")

    config.data_root.mkdir(parents=True, exist_ok=True)
    config.backup_root.mkdir(parents=True, exist_ok=True)
    _require_same_filesystem(config.data_root, config.temp_root)
    _quick_check(stage)

    previous_mode = 0o600
    backup: Path | None = None
    if config.db_path.is_file():
        previous_mode = stat.S_IMODE(config.db_path.stat().st_mode)
        backup = _new_backup_path(config, artifact_id)
        _backup_database(config.db_path, backup)
        os.chmod(backup, previous_mode)

    os.chmod(stage, previous_mode)
    _fsync_file(stage)
    try:
        os.replace(stage, config.db_path)
    except OSError as exc:
        raise PeerSyncError(
            f"Failed to replace destination database {config.db_path}: {exc}"
        ) from exc

    try:
        _fsync_directory(config.data_root)
    except PeerSyncError as exc:
        raise PeerSyncError(
            f"Database was replaced at {config.db_path}, but its directory "
            f"durability flush failed: {exc}"
        ) from exc
    return {
        "database": config.db_path.as_posix(),
        "backup": backup.as_posix() if backup is not None else None,
    }


def cleanup_artifact(
    config: AppConfig,
    *,
    expected_data_root: str | Path,
    artifact_id: str,
) -> dict[str, object]:
    """Remove one exact staging artifact and its SQLite sidecars."""
    _verify_data_root(config, expected_data_root)
    stage = artifact_path(config, artifact_id)
    removed = False
    for path in (stage, *(_sidecar_paths(stage))):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PeerSyncError(f"Failed to remove artifact {path}: {exc}") from exc
    return {"artifact": stage.name, "removed": removed}


def _verify_data_root(
    config: AppConfig,
    expected_data_root: str | Path,
) -> None:
    configured = PurePosixPath(config.data_root.as_posix())
    expected = PurePosixPath(str(expected_data_root))
    if configured != expected:
        raise PeerSyncError(
            "Remote DATA_ROOT identity mismatch: "
            f"expected {expected}, peer configured {configured}."
        )


def _require_same_filesystem(first: Path, second: Path) -> None:
    try:
        if first.stat().st_dev != second.stat().st_dev:
            raise PeerSyncError(
                "Database staging and DATA_ROOT must be on the same filesystem "
                f"for atomic replacement: {second}"
            )
    except OSError as exc:
        raise PeerSyncError(f"Failed to inspect synchronization paths: {exc}") from exc


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise PeerSyncError(f"Database file not found: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=30.0)
    except sqlite3.Error as exc:
        raise PeerSyncError(f"Failed to open database {path}: {exc}") from exc


def _quick_check(path: Path) -> None:
    connection = _read_only_connection(path)
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as exc:
        raise PeerSyncError(f"SQLite quick_check failed for {path}: {exc}") from exc
    finally:
        connection.close()
    if rows != [("ok",)]:
        details = "; ".join(str(row[0]) for row in rows)
        raise PeerSyncError(
            f"SQLite quick_check reported errors for {path}: {details}"
        )


def _backup_database(source: Path, destination: Path) -> None:
    if destination.exists():
        raise PeerSyncError(f"Backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_connection = _read_only_connection(source)
    destination_connection: sqlite3.Connection | None = None
    failure: OSError | sqlite3.Error | None = None
    try:
        destination_connection = sqlite3.connect(destination, timeout=30.0)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        mode_row = destination_connection.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone()
        if mode_row is None or str(mode_row[0]).lower() != "delete":
            raise sqlite3.DatabaseError(
                "Could not normalize snapshot journal_mode to DELETE."
            )
        destination_connection.commit()
    except (OSError, sqlite3.Error) as exc:
        failure = exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        source_connection.close()

    if failure is not None:
        _remove_file_and_sidecars(destination)
        raise PeerSyncError(
            f"Failed to snapshot database {source} to {destination}: {failure}"
        ) from failure

    try:
        _remove_artifact_sidecars(destination)
        _quick_check(destination)
    except Exception:
        _remove_file_and_sidecars(destination)
        raise


def _new_backup_path(config: AppConfig, artifact_id: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    filename = f"ytcrawl-{timestamp}-{artifact_id}.sqlite3"
    destination = config.backup_root / filename
    if destination.exists():
        raise PeerSyncError(f"Backup path already exists: {destination}")
    return destination


def _sidecar_paths(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES)


def _remove_file_and_sidecars(path: Path) -> None:
    for candidate in (path, *_sidecar_paths(path)):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _remove_artifact_sidecars(path: Path) -> None:
    """Remove sidecars belonging to a closed, newly created artifact only."""
    for sidecar in _sidecar_paths(path):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise PeerSyncError(
                f"Failed to remove snapshot sidecar {sidecar}: {exc}"
            ) from exc


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as file:
            os.fsync(file.fileno())
    except OSError as exc:
        raise PeerSyncError(f"Failed to flush staged database {path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        if exc.errno in (errno.EINVAL, errno.ENOTSUP):
            return
        raise PeerSyncError(
            f"Failed to flush database directory {path}: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.sync_remote",
        description="Internal SSH peer helper for ytcrawl data synchronization.",
    )
    parser.add_argument("--config", help="Peer config.yaml path.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    _add_identity_argument(inspect_parser)

    preflight_parser = subparsers.add_parser("preflight")
    _add_identity_argument(preflight_parser)
    preflight_parser.add_argument(
        "--role", choices=("source", "destination"), required=True
    )
    preflight_parser.add_argument(
        "--component", choices=("db", "media", "all"), required=True
    )

    for action in ("snapshot", "promote", "cleanup"):
        action_parser = subparsers.add_parser(action)
        _add_identity_argument(action_parser)
        action_parser.add_argument("--artifact-id", required=True)

    return parser


def _add_identity_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-data-root", required=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = get_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.action == "inspect":
            result = inspect_peer(
                config, expected_data_root=args.expected_data_root
            )
        elif args.action == "preflight":
            result = preflight_peer(
                config,
                expected_data_root=args.expected_data_root,
                role=args.role,
                component=args.component,
            )
        elif args.action == "snapshot":
            result = snapshot_database(
                config,
                expected_data_root=args.expected_data_root,
                artifact_id=args.artifact_id,
            )
        elif args.action == "promote":
            result = promote_database(
                config,
                expected_data_root=args.expected_data_root,
                artifact_id=args.artifact_id,
            )
        else:
            result = cleanup_artifact(
                config,
                expected_data_root=args.expected_data_root,
                artifact_id=args.artifact_id,
            )
    except PeerSyncError as exc:
        print(f"Remote sync error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - remote CLI error boundary.
        print(f"Remote sync error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

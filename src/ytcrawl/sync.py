from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ytcrawl import sync_remote as peer
from ytcrawl.config import AppConfig, ConfigError, get_config

Direction = Literal["push", "pull"]
SyncComponent = Literal["db", "media", "all"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_MEDIA_EXCLUDES = (
    "*.part",
    "*.ytdl",
    "*.tmp",
    "*.temp",
)
_PARTIAL_DIRECTORY = f"../Temp/{peer.MEDIA_RSYNC_PARTIAL_DIRNAME}"
_RSYNC_REMOTE_SHELL = "ssh -o BatchMode=yes"


class SyncError(RuntimeError):
    """Raised when local/remote synchronization cannot complete."""


class SyncInputError(SyncError):
    """Raised for invalid local configuration or a missing local source."""


@dataclass(frozen=True, slots=True)
class SyncResult:
    direction: Direction
    component: SyncComponent
    local_backup: str | None = None
    remote_backup: str | None = None
    warnings: tuple[str, ...] = ()


def synchronize(
    config: AppConfig,
    direction: Direction,
    component: SyncComponent,
    *,
    runner: CommandRunner | None = None,
    artifact_id_factory: Callable[[], str] | None = None,
) -> SyncResult:
    """Synchronize selected data between this peer and its configured peer."""
    _validate_selection(direction, component)
    _validate_remote_alias(config.remote_ssh_alias)
    selected_runner = runner or subprocess.run
    selected_id_factory = artifact_id_factory or (lambda: uuid.uuid4().hex)
    local_role: peer.PeerRole = (
        "source" if direction == "push" else "destination"
    )
    remote_role: peer.PeerRole = (
        "destination" if direction == "push" else "source"
    )

    if direction == "push":
        _local_preflight(config, local_role, component)
        _remote_preflight(config, remote_role, component, selected_runner)
    else:
        _remote_preflight(config, remote_role, component, selected_runner)
        _local_preflight(config, local_role, component)

    if component in ("media", "all"):
        _sync_media(config, direction, selected_runner)

    local_backup: str | None = None
    remote_backup: str | None = None
    cleanup_warnings: tuple[str, ...] = ()
    if component in ("db", "all"):
        artifact_id = selected_id_factory()
        result, cleanup_warnings = _sync_database(
            config,
            direction,
            artifact_id,
            selected_runner,
        )
        if direction == "push":
            remote_backup = _optional_string(result.get("backup"))
        else:
            local_backup = _optional_string(result.get("backup"))

    return SyncResult(
        direction=direction,
        component=component,
        local_backup=local_backup,
        remote_backup=remote_backup,
        warnings=cleanup_warnings,
    )


def build_media_rsync_command(
    config: AppConfig,
    direction: Direction,
) -> list[str]:
    """Build the additive, never-delete rsync command for media files."""
    _validate_direction(direction)
    _validate_remote_alias(config.remote_ssh_alias)
    remote_media = _remote_relative_path(
        config.remote_data_root,
        config.remote_media_root,
        directory=True,
    )
    local_media = _directory_argument(config.media_root)
    remote_spec = f"{config.remote_ssh_alias}:{remote_media}"
    source, destination = (
        (local_media, remote_spec)
        if direction == "push"
        else (remote_spec, local_media)
    )

    command = [
        "rsync",
        "--recursive",
        "--times",
        "--ignore-existing",
        f"--partial-dir={_PARTIAL_DIRECTORY}",
        "--itemize-changes",
        "-e",
        _RSYNC_REMOTE_SHELL,
    ]
    for pattern in _MEDIA_EXCLUDES:
        command.extend(("--exclude", pattern))
    command.extend(
        (
            "--rsync-path",
            _remote_rsync_path(config),
            "--",
            source,
            destination,
        )
    )
    return command


def build_database_rsync_command(
    config: AppConfig,
    direction: Direction,
    artifact_id: str,
) -> list[str]:
    """Build the rsync command for one unique SQLite staging artifact."""
    _validate_direction(direction)
    _validate_remote_alias(config.remote_ssh_alias)
    filename = peer.artifact_filename(artifact_id)
    local_stage = os.fspath(config.temp_root / filename)
    remote_stage_path = config.remote_temp_root / filename
    remote_stage = _remote_relative_path(
        config.remote_data_root,
        remote_stage_path,
        directory=False,
    )
    remote_spec = f"{config.remote_ssh_alias}:{remote_stage}"
    source, destination = (
        (local_stage, remote_spec)
        if direction == "push"
        else (remote_spec, local_stage)
    )
    return [
        "rsync",
        "--times",
        "--itemize-changes",
        "-e",
        _RSYNC_REMOTE_SHELL,
        "--rsync-path",
        _remote_rsync_path(config),
        "--",
        source,
        destination,
    ]


def build_remote_helper_command(
    config: AppConfig,
    action: str,
    *,
    options: Sequence[str] = (),
) -> list[str]:
    """Build an SSH command that invokes the peer helper without a local shell."""
    _validate_remote_alias(config.remote_ssh_alias)
    remote_python = config.remote_venv / "bin" / "python"
    remote_config = config.remote_app_dir / "config.yaml"
    remote_python_path = config.remote_app_dir / "src"
    helper_arguments = [
        "env",
        "PYTHONDONTWRITEBYTECODE=1",
        f"PYTHONPATH={remote_python_path}",
        str(remote_python),
        "-m",
        "ytcrawl.sync_remote",
        "--config",
        str(remote_config),
        action,
        *options,
    ]
    remote_command = (
        f"cd {shlex.quote(str(config.remote_app_dir))} && "
        f"{shlex.join(helper_arguments)}"
    )
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "--",
        config.remote_ssh_alias,
        remote_command,
    ]


def _sync_media(
    config: AppConfig,
    direction: Direction,
    runner: CommandRunner,
) -> None:
    command = build_media_rsync_command(config, direction)
    _run_command(command, runner=runner, description="media rsync")


def _sync_database(
    config: AppConfig,
    direction: Direction,
    artifact_id: str,
    runner: CommandRunner,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    primary_error: Exception | None = None
    result: dict[str, Any] = {}
    try:
        if direction == "push":
            peer.snapshot_database(
                config,
                artifact_id=artifact_id,
            )
        else:
            _remote_action(
                config,
                "snapshot",
                runner=runner,
                options=("--artifact-id", artifact_id),
            )

        command = build_database_rsync_command(
            config,
            direction,
            artifact_id,
        )
        _run_command(command, runner=runner, description="database rsync")

        if direction == "push":
            result = _remote_action(
                config,
                "promote",
                runner=runner,
                options=("--artifact-id", artifact_id),
            )
        else:
            result = peer.promote_database(
                config,
                artifact_id=artifact_id,
            )
    except Exception as exc:  # noqa: BLE001 - retain error through cleanup.
        primary_error = exc

    cleanup_errors: list[str] = []
    try:
        peer.cleanup_artifact(
            config,
            artifact_id=artifact_id,
        )
    except Exception as exc:  # noqa: BLE001 - report exact cleanup failure.
        cleanup_errors.append(f"local cleanup failed: {exc}")
    try:
        _remote_action(
            config,
            "cleanup",
            runner=runner,
            options=("--artifact-id", artifact_id),
        )
    except Exception as exc:  # noqa: BLE001 - report exact cleanup failure.
        cleanup_errors.append(f"remote cleanup failed: {exc}")

    if primary_error is not None:
        details = str(primary_error)
        if cleanup_errors:
            details = f"{details}; {'; '.join(cleanup_errors)}"
        raise SyncError(
            f"Database synchronization failed: {details}"
        ) from primary_error
    return result, tuple(cleanup_errors)


def _local_preflight(
    config: AppConfig,
    role: peer.PeerRole,
    component: SyncComponent,
) -> None:
    try:
        peer.preflight_peer(
            config,
            role=role,
            component=component,
        )
    except peer.PeerSourceMissingError as exc:
        raise SyncInputError(f"Local source preflight failed: {exc}") from exc
    except peer.PeerSyncError as exc:
        raise SyncError(f"Local preflight failed: {exc}") from exc


def _remote_preflight(
    config: AppConfig,
    role: peer.PeerRole,
    component: SyncComponent,
    runner: CommandRunner,
) -> None:
    _remote_action(
        config,
        "preflight",
        runner=runner,
        options=(
            "--role",
            role,
            "--component",
            component,
        ),
    )


def _remote_action(
    config: AppConfig,
    action: str,
    *,
    runner: CommandRunner,
    options: Sequence[str] = (),
) -> dict[str, Any]:
    command = build_remote_helper_command(config, action, options=options)
    completed = _run_command(
        command,
        runner=runner,
        description=f"remote {action}",
        capture_output=True,
    )
    return _parse_json_result(completed.stdout, action)


def _run_command(
    command: Sequence[str],
    *,
    runner: CommandRunner,
    description: str,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            list(command),
            check=False,
            text=True,
            shell=False,
            capture_output=capture_output,
        )
    except OSError as exc:
        raise SyncError(f"Failed to start {description}: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        suffix = f": {stderr}" if stderr else ""
        raise SyncError(
            f"{description} exited with code {completed.returncode}{suffix}"
        )
    return completed


def _parse_json_result(output: str | None, action: str) -> dict[str, Any]:
    for line in reversed((output or "").splitlines()):
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    raise SyncError(f"Remote {action} returned no valid JSON result.")


def _remote_relative_path(
    root: PurePosixPath,
    path: PurePosixPath,
    *,
    directory: bool,
) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SyncError(f"Remote path is outside REMOTE_DATA_ROOT: {path}") from exc
    if not relative.parts or ".." in relative.parts:
        raise SyncError(f"Invalid remote synchronization path: {path}")
    result = f"./{relative.as_posix()}"
    return f"{result}/" if directory else result


def _remote_rsync_path(config: AppConfig) -> str:
    return f"cd {shlex.quote(str(config.remote_data_root))} && rsync"


def _directory_argument(path: Path) -> str:
    return f"{os.fspath(path).rstrip(os.sep)}{os.sep}"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _validate_selection(direction: str, component: str) -> None:
    _validate_direction(direction)
    if component not in ("db", "media", "all"):
        raise SyncInputError(
            f"Unsupported synchronization component: {component}"
        )


def _validate_direction(direction: str) -> None:
    if direction not in ("push", "pull"):
        raise SyncInputError(
            f"Unsupported synchronization direction: {direction}"
        )


def _validate_remote_alias(alias: str) -> None:
    if alias.startswith("-") or re.fullmatch(r"[A-Za-z0-9_.@-]+", alias) is None:
        raise SyncInputError(
            "REMOTE_SSH_ALIAS must be an SSH host alias containing only "
            "letters, digits, '.', '_', '@', and '-'."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.sync",
        description=(
            "Synchronize ytcrawl DATA_ROOT with its configured SSH peer. "
            "Database synchronization replaces the destination database."
        ),
    )
    parser.add_argument("direction", choices=("push", "pull"))
    parser.add_argument("component", choices=("db", "media", "all"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        result = synchronize(config, args.direction, args.component)
    except SyncInputError as exc:
        print(f"Synchronization input error: {exc}", file=sys.stderr)
        return 2
    except SyncError as exc:
        print(f"Synchronization error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI synchronization boundary.
        print(f"Synchronization error: {exc}", file=sys.stderr)
        return 1

    print(f"Synchronization completed: {result.direction} {result.component}.")
    if result.local_backup:
        print(f"Local database backup: {result.local_backup}")
    if result.remote_backup:
        print(f"Remote database backup: {result.remote_backup}")
    for warning in result.warnings:
        print(f"Synchronization warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

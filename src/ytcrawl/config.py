from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml

CONFIG_PATH_ENV = "YTCRAWL_CONFIG"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
DB_FILENAME = "ytcrawl.sqlite3"
MEDIA_DIRNAME = "media"
TEMP_DIRNAME = "Temp"
BACKUP_DIRNAME = "Backup"

_REQUIRED_KEYS = frozenset(
    {
        "REMOTE_SSH_ALIAS",
        "REMOTE_GIT_BARE",
        "REMOTE_APP_DIR",
        "REMOTE_DATA_ROOT",
        "REMOTE_VENV",
        "DATA_ROOT",
        "REVIEW_HOST",
        "REVIEW_PORT",
    }
)
_PLACEHOLDER_PATTERN = re.compile(r"<[^>]+>")


class ConfigError(ValueError):
    """Raised when the ytcrawl configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    remote_ssh_alias: str
    remote_git_bare: PurePosixPath
    remote_app_dir: PurePosixPath
    remote_data_root: PurePosixPath
    remote_venv: PurePosixPath
    data_root: Path
    review_host: str
    review_port: int
    config_path: Path = field(repr=False, compare=False)

    @property
    def db_path(self) -> Path:
        """Return the fixed SQLite path below DATA_ROOT."""
        return self.data_root / DB_FILENAME

    @property
    def db_url(self) -> str:
        """Return the SQLAlchemy URL for the configured SQLite file."""
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def media_root(self) -> Path:
        """Return the fixed media directory below DATA_ROOT."""
        return self.data_root / MEDIA_DIRNAME

    @property
    def temp_root(self) -> Path:
        """Return the local synchronization staging directory."""
        return self.data_root / TEMP_DIRNAME

    @property
    def backup_root(self) -> Path:
        """Return the local synchronization backup directory."""
        return self.data_root / BACKUP_DIRNAME

    @property
    def remote_db_path(self) -> PurePosixPath:
        """Return the fixed remote SQLite path below REMOTE_DATA_ROOT."""
        return self.remote_data_root / DB_FILENAME

    @property
    def remote_media_root(self) -> PurePosixPath:
        """Return the fixed remote media directory below REMOTE_DATA_ROOT."""
        return self.remote_data_root / MEDIA_DIRNAME

    @property
    def remote_temp_root(self) -> PurePosixPath:
        """Return the remote synchronization staging directory."""
        return self.remote_data_root / TEMP_DIRNAME

    @property
    def remote_backup_root(self) -> PurePosixPath:
        """Return the remote synchronization backup directory."""
        return self.remote_data_root / BACKUP_DIRNAME


def resolve_config_path(
    config_path: str | Path | None = None,
) -> Path:
    """Resolve an explicit path, YTCRAWL_CONFIG, or the project default."""
    selected_path = config_path or os.environ.get(CONFIG_PATH_ENV)
    path = Path(selected_path) if selected_path else DEFAULT_CONFIG_PATH
    return path.expanduser().resolve()


def get_config(
    config_path: str | Path | None = None,
) -> AppConfig:
    """Read and cache a validated ytcrawl configuration by absolute path."""
    path = resolve_config_path(config_path)
    return _get_cached_config(path)


def clear_config_cache() -> None:
    """Clear cached settings so a changed file is read again."""
    _get_cached_config.cache_clear()


@lru_cache(maxsize=None)
def _get_cached_config(path: Path) -> AppConfig:
    return _load_config_file(path)


def _load_config_file(path: Path) -> AppConfig:
    if not path.is_file():
        raise ConfigError(
            f"Configuration file not found: {path}. "
            "Copy config.example.yaml to config.yaml and fill in its values, "
            f"or set {CONFIG_PATH_ENV}."
        )

    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(
            f"Failed to read configuration file {path}: {exc}"
        ) from exc

    if not isinstance(raw_config, Mapping):
        raise ConfigError(f"Configuration root must be a YAML mapping: {path}")

    keys = set(raw_config)
    missing_keys = sorted(_REQUIRED_KEYS - keys)
    unknown_keys = sorted(keys - _REQUIRED_KEYS, key=str)
    if missing_keys:
        raise ConfigError(
            "Missing configuration keys: " + ", ".join(missing_keys)
        )
    if unknown_keys:
        raise ConfigError(
            "Unknown configuration keys: "
            + ", ".join(str(key) for key in unknown_keys)
        )

    remote_path_keys = (
        "REMOTE_GIT_BARE",
        "REMOTE_APP_DIR",
        "REMOTE_DATA_ROOT",
        "REMOTE_VENV",
    )
    remote_paths = {
        key: _read_remote_path(raw_config, key) for key in remote_path_keys
    }

    data_root_value = _read_string(raw_config, "DATA_ROOT")
    data_root = Path(data_root_value).expanduser()
    if not data_root.is_absolute():
        raise ConfigError("DATA_ROOT must be an absolute path.")

    review_port = raw_config["REVIEW_PORT"]
    if isinstance(review_port, bool) or not isinstance(review_port, int):
        raise ConfigError("REVIEW_PORT must be an integer.")
    if not 1 <= review_port <= 65535:
        raise ConfigError("REVIEW_PORT must be between 1 and 65535.")

    return AppConfig(
        remote_ssh_alias=_read_string(raw_config, "REMOTE_SSH_ALIAS"),
        remote_git_bare=remote_paths["REMOTE_GIT_BARE"],
        remote_app_dir=remote_paths["REMOTE_APP_DIR"],
        remote_data_root=remote_paths["REMOTE_DATA_ROOT"],
        remote_venv=remote_paths["REMOTE_VENV"],
        data_root=data_root,
        review_host=_read_string(raw_config, "REVIEW_HOST"),
        review_port=review_port,
        config_path=path,
    )


def _read_string(config: Mapping[object, object], key: str) -> str:
    value = config[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string.")
    value = value.strip()
    if _PLACEHOLDER_PATTERN.search(value):
        raise ConfigError(f"{key} still contains a <...> placeholder.")
    return value


def _read_remote_path(
    config: Mapping[object, object],
    key: str,
) -> PurePosixPath:
    path = PurePosixPath(_read_string(config, key))
    if not path.is_absolute():
        raise ConfigError(f"{key} must be an absolute POSIX path.")
    return path

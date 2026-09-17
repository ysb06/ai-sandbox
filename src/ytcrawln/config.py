from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
DB_FILENAME = "ytcrawl.sqlite3"
MEDIA_DIRNAME = "media"
TEMP_DIRNAME = "Temp"
BACKUP_DIRNAME = "Backup"


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_path: Path = field(repr=False, compare=False)
    data_root: Path
    review_host: str
    review_port: int

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


def get_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> AppConfig:
    config_path = Path(config_path).expanduser().resolve()

    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    data_root_value = _read_string(raw_config, "DATA_ROOT")
    data_root = Path(data_root_value).expanduser()

    return AppConfig(
        config_path=config_path,
        data_root=data_root,
        review_host=_read_string(raw_config, "REVIEW_HOST", "127.0.0.1"),
        review_port=raw_config.get("REVIEW_PORT", 8765),
    )


def _read_string(
    config: Mapping[object, object],
    key: str,
    default: object = "",
) -> str:
    return str(config.get(key, default)).strip()

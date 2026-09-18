"""Initialize the database used by ytcrawln."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml
from sqlalchemy.exc import SQLAlchemyError

from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.db import core


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize the ytcrawln videos and videos_detail tables.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Configuration file (default: project config.yaml).",
    )
    args = parser.parse_args(argv)

    try:
        config = get_config(args.config)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        core.configure(config.db_url)
        core.create_all()
    except (OSError, SQLAlchemyError) as exc:
        print(f"Database initialization error: {exc}", file=sys.stderr)
        return 1

    print(f"Database initialized: {config.db_path.resolve()}")
    print("Tables ensured: videos, videos_detail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

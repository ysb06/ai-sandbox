from __future__ import annotations

import sys

import uvicorn

from ytcrawl.config import ConfigError, get_config


def main() -> int:
    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    uvicorn.run(
        "ytcrawl.review.app:app",
        host=config.review_host,
        port=config.review_port,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import load_dotenv

from ytcrawl.config import ConfigError, get_config
from ytcrawl.crawl.youtube.channel import crawl_youtube_channel

# CSPAN_CHANNEL_ID = "UCb--64Gl51jIEVE-GLDAVTg"
# SPBSTV_CHANNEL_ID = "UCbLhiGCUNY0ufWIATLCnBrw"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.crawl.youtube.channel.main",
        description="Collect public uploads from a YouTube channel.",
    )
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--published-after", dest="published_after")
    parser.add_argument("--published-before", dest="published_before")
    parser.add_argument(
        "--always-download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Download every video collected on this page regardless of embed availability. Existing local files are reused."
        ),
    )
    return parser.parse_args(argv)


def run_channel_crawl(
    args: argparse.Namespace,
    api_key: str,
    *,
    db_url: str,
    output_dir: str | Path,
) -> int:
    always_download = getattr(args, "always_download", False)
    return crawl_youtube_channel(
        channel_id=args.channel_id,
        api_key=api_key,
        db_url=db_url,
        output_dir=output_dir,
        published_after=args.published_after,
        published_before=args.published_before,
        always_download=always_download,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    args = parse_args(argv)

    if env is None:
        load_dotenv()
        env = os.environ

    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    api_key = env.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is required.", file=sys.stderr)
        return 2

    return run_channel_crawl(
        args,
        api_key,
        db_url=config.db_url,
        output_dir=config.media_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())

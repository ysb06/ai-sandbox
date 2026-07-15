from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

from dotenv import load_dotenv

from ytcrawl.crawl.youtube.channel import crawl_youtube_channel

CSPAN_CHANNEL_ID = "UCb--64Gl51jIEVE-GLDAVTg"
DEFAULT_DB_URL = "sqlite:///results/ytcrawl.sqlite3"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.crawl.youtube.channel.cspan",
        description="Collect public uploads from the C-SPAN YouTube channel.",
    )
    parser.add_argument("--published-after", dest="published_after")
    parser.add_argument("--published-before", dest="published_before")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for videos without an embed code or local path",
    )
    parser.add_argument(
        "--db-url",
        default=DEFAULT_DB_URL,
        help=f"Database URL (default: {DEFAULT_DB_URL})",
    )
    return parser.parse_args(argv)


def run_cspan_crawl(args: argparse.Namespace, api_key: str) -> int:
    return crawl_youtube_channel(
        channel_id=CSPAN_CHANNEL_ID,
        api_key=api_key,
        db_url=args.db_url,
        output_dir=args.output_dir,
        published_after=args.published_after,
        published_before=args.published_before,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    if env is None:
        load_dotenv()
        env = os.environ

    args = parse_args(argv)
    api_key = env.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is required.", file=sys.stderr)
        return 2

    return run_cspan_crawl(args, api_key)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

from dotenv import load_dotenv

from ytcrawl.crawl.youtube.channel import crawl_youtube_channel

# CSPAN_CHANNEL_ID = "UCb--64Gl51jIEVE-GLDAVTg"
DEFAULT_DB_URL = "sqlite:///results/ytcrawl.sqlite3"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.crawl.youtube.channel.main",
        description="Collect public uploads from a YouTube channel.",
    )
    parser.add_argument("--channel-id", required=True)
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
    parser.add_argument(
        "--always-download",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Download every video collected on this page regardless of embed "
            "availability. Existing local files are reused."
        ),
    )
    return parser.parse_args(argv)


def run_channel_crawl(args: argparse.Namespace, api_key: str) -> int:
    always_download = getattr(args, "always_download", False)
    return crawl_youtube_channel(
        channel_id=args.channel_id,
        api_key=api_key,
        db_url=args.db_url,
        output_dir=args.output_dir,
        published_after=args.published_after,
        published_before=args.published_before,
        always_download=always_download,
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

    return run_channel_crawl(args, api_key)


if __name__ == "__main__":
    raise SystemExit(main())

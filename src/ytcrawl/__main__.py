import argparse
import os
import sys
from collections.abc import Mapping, Sequence

from dotenv import load_dotenv

from ytcrawl.config import ConfigError, get_config
from ytcrawl.crawl.youtube import crawl_youtube
from ytcrawl.search.youtube import PRESET_QUERIES


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl",
        description="Search YouTube, store results, details, embed code, and videos.",
    )

    parser.add_argument("--query", help="Direct YouTube search query")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESET_QUERIES),
        help="Face-candidate query preset used when --query is omitted",
    )
    parser.add_argument("--published-after", dest="published_after")
    parser.add_argument("--published-before", dest="published_before")
    parser.add_argument(
        "--channel-id",
        dest="channel_id",
        help="YouTube channel ID used to restrict search results",
    )
    parser.add_argument(
        "--creative-common",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Restrict results to Creative Commons videos; "
            "--no-creative-common allows any license"
        ),
    )
    parser.add_argument(
        "--always-download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Download every video collected in this run regardless of embed "
            "availability. Existing local files are reused."
        ),
    )

    return parser.parse_args(argv)


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

    return crawl_youtube(
        args,
        api_key,
        config.db_url,
        output_dir=config.media_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

from dotenv import load_dotenv

from ytcrawl.search.youtube import PRESET_QUERIES, run_search_json


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.search",
        description="Save a raw YouTube search.list response as JSON.",
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
    parser.add_argument("--page-token", dest="page_token")
    parser.add_argument("--output", required=True, help="Path to the raw JSON output file")
    return parser.parse_args(argv)


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

    return run_search_json(args, api_key, args.output)


if __name__ == "__main__":
    raise SystemExit(main())

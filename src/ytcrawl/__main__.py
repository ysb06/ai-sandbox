import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from dotenv import load_dotenv

from ytcrawl.search.youtube import PRESET_QUERIES, run_search_youtube

DEFAULT_DB_URL = "sqlite:///results/ytcrawl.sqlite3"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl",
        description="Collect YouTube search result snippets into a database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search", help="Run one YouTube search")
    search.add_argument("--query", help="Direct YouTube search query")
    search.add_argument(
        "--preset",
        choices=sorted(PRESET_QUERIES),
        help="Face-candidate query preset used when --query is omitted",
    )
    search.add_argument("--published-after", dest="published_after")
    search.add_argument("--published-before", dest="published_before")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    db_url: str = DEFAULT_DB_URL,
    env: Mapping[str, str] | None = None,
    youtube_factory: Callable[[str], Any] | None = None,
) -> int:
    if env is None:
        load_dotenv()
        env = os.environ

    args = parse_args(argv)
    api_key = env.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is required.", file=sys.stderr)
        return 2

    if args.command == "search":
        if youtube_factory is None:
            return run_search_youtube(args, api_key, db_url)
        return run_search_youtube(args, api_key, db_url, youtube_factory=youtube_factory)

    print(f"Unsupported command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

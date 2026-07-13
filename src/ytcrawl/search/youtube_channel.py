from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ytcrawl.search.youtube import create_youtube_client

CHANNEL_PART = "id,snippet"
MAX_CHANNEL_RESULTS = 10


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.search.youtube_channel",
        description="Resolve a YouTube handle or search channel name candidates.",
    )
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument(
        "--handle",
        help="YouTube handle to resolve, with or without the @ prefix",
    )
    lookup.add_argument(
        "--name",
        help="Display channel name used to search candidate channels",
    )
    parser.add_argument(
        "--output",
        help="Optional path for the normalized JSON result",
    )
    return parser.parse_args(argv)


def fetch_channel_by_handle(
    handle: str,
    api_key: str,
) -> dict[str, Any]:
    youtube = create_youtube_client(api_key)
    return (
        youtube.channels()
        .list(
            part=CHANNEL_PART,
            forHandle=handle,
        )
        .execute(num_retries=0)
    )


def fetch_channel_candidates_by_name(
    name: str,
    api_key: str,
) -> dict[str, Any]:
    youtube = create_youtube_client(api_key)
    return (
        youtube.search()
        .list(
            part="snippet",
            type="channel",
            q=name,
            maxResults=MAX_CHANNEL_RESULTS,
        )
        .execute(num_retries=0)
    )


def build_channel_lookup_result(
    *,
    mode: str,
    query: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    channels = []
    for item in response.get("items", []):
        if not isinstance(item, dict):
            continue

        channel_id = _extract_channel_id(item.get("id"))
        if channel_id is None:
            continue

        snippet = item.get("snippet")
        if not isinstance(snippet, dict):
            snippet = {}
        channels.append(
            {
                "channel_id": channel_id,
                "title": snippet.get("title"),
                "description": snippet.get("description"),
            }
        )

    return {
        "mode": mode,
        "query": query,
        "channels": channels,
    }


def run_channel_lookup(
    args: argparse.Namespace,
    api_key: str,
) -> int:
    if args.handle is not None:
        mode = "handle"
        query = args.handle
        response = fetch_channel_by_handle(query, api_key)
    else:
        mode = "name"
        query = args.name
        response = fetch_channel_candidates_by_name(query, api_key)

    result = build_channel_lookup_result(
        mode=mode,
        query=query,
        response=response,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.write(rendered)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")

    if not result["channels"]:
        print("No YouTube channels found.", file=sys.stderr)
        return 1
    return 0


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

    return run_channel_lookup(args, api_key)


def _extract_channel_id(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if not isinstance(value, dict):
        return None

    channel_id = value.get("channelId")
    if isinstance(channel_id, str) and channel_id:
        return channel_id
    return None


if __name__ == "__main__":
    raise SystemExit(main())

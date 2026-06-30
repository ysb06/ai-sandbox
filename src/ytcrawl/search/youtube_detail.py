import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ytcrawl.search.youtube import create_youtube_client

DEFAULT_PART = "snippet,contentDetails,status,statistics,topicDetails,recordingDetails,localizations"
MAX_VIDEO_IDS_PER_REQUEST = 50


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.search.youtube_detail",
        description="Save raw YouTube videos.list responses as JSON.",
    )
    parser.add_argument(
        "--video-ids",
        nargs="+",
        required=True,
        help="One or more YouTube video IDs",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the raw JSON output file",
    )
    return parser.parse_args(argv)


def _chunk_video_ids(video_ids: Sequence[str]) -> list[list[str]]:
    return [
        list(video_ids[index : index + MAX_VIDEO_IDS_PER_REQUEST])
        for index in range(0, len(video_ids), MAX_VIDEO_IDS_PER_REQUEST)
    ]


def fetch_video_detail_responses(
    video_ids: Sequence[str],
    api_key: str,
) -> list[dict[str, Any]]:
    youtube = create_youtube_client(api_key)
    responses = []
    for batch in _chunk_video_ids(video_ids):
        response = (
            youtube.videos()
            .list(
                part=DEFAULT_PART,
                id=",".join(batch),
            )
            .execute(num_retries=0)
        )
        responses.append(response)
    return responses


def run_video_detail_json(
    args: argparse.Namespace,
    api_key: str,
) -> int:
    responses = fetch_video_detail_responses(args.video_ids, api_key)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(responses, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved raw YouTube video detail response to {output}")
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

    return run_video_detail_json(args, api_key)


if __name__ == "__main__":
    raise SystemExit(main())

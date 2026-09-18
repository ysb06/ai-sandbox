import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ytcrawln.api.youtube import create_youtube_client

DEFAULT_PART = "snippet,contentDetails,status,statistics,topicDetails,recordingDetails,localizations,player"
MAX_VIDEO_IDS_PER_REQUEST = 50


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawln.api.youtube.videos",
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


def fetch_video_detail_response(
    video_ids: Sequence[str],
    *,
    youtube_client: Any,
) -> dict[str, Any]:
    """Fetch one batch without managing the client or persisting the response."""
    if not 1 <= len(video_ids) <= MAX_VIDEO_IDS_PER_REQUEST:
        raise ValueError(
            f"Expected between 1 and {MAX_VIDEO_IDS_PER_REQUEST} video IDs."
        )
    return (
        youtube_client.videos()
        .list(part=DEFAULT_PART, id=",".join(video_ids))
        .execute(num_retries=0)
    )


def fetch_video_detail_responses(
    video_ids: Sequence[str],
    api_key: str,
) -> list[dict[str, Any]]:
    youtube = create_youtube_client(api_key)
    responses = []
    for batch in _chunk_video_ids(video_ids):
        response = fetch_video_detail_response(batch, youtube_client=youtube)
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
    args = parse_args(argv)
    if env is None:
        load_dotenv()
        env = os.environ

    api_key = env.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is required.", file=sys.stderr)
        return 2

    return run_video_detail_json(args, api_key)


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

FIXED_SEARCH_PARAMS: dict[str, Any] = {
    "part": "snippet",
    "type": "video",
    "maxResults": 50,
    "regionCode": "KR",
    "safeSearch": "none",
}

COLLECTION_METHOD = "search"
DEFAULT_VIDEO_LICENSE = "creativeCommon"
ANY_VIDEO_LICENSE = "any"

PRESET_QUERIES: dict[str, str] = {
    "facecam": "facecam face person",
    "interview": "interview face person",
    "selfie": "selfie face video",
    "talking-head": '"talking head" face person',
    "vlog": "vlog face person",
}


def resolve_query(args: argparse.Namespace) -> str:
    if args.query:
        return args.query
    return PRESET_QUERIES.get(args.preset, " ")


def resolve_video_license(args: argparse.Namespace) -> str:
    if getattr(args, "creative_common", True) is False:
        return ANY_VIDEO_LICENSE
    return DEFAULT_VIDEO_LICENSE


def build_search_params(
    args: argparse.Namespace,
    page_token: str | None = None,
) -> dict[str, Any]:
    params = dict(FIXED_SEARCH_PARAMS)
    params["q"] = resolve_query(args)
    params["videoLicense"] = resolve_video_license(args)
    if args.published_after:
        params["publishedAfter"] = args.published_after
    if args.published_before:
        params["publishedBefore"] = args.published_before
    channel_id = getattr(args, "channel_id", None)
    if channel_id:
        params["channelId"] = channel_id
    if page_token:
        params["pageToken"] = page_token
    return params


def build_request_hash(
    *,
    query: str,
    channel_id: str | None,
    video_license: str,
    published_after: str | None,
    published_before: str | None,
    fixed_params: dict[str, Any],
) -> str:
    payload = {
        "collection_method": COLLECTION_METHOD,
        "query": query,
        "channel_id": channel_id,
        "published_after": published_after,
        "published_before": published_before,
        "fixed_params": {
            "part": fixed_params["part"],
            "type": fixed_params["type"],
            "maxResults": int(fixed_params["maxResults"]),
            "regionCode": fixed_params["regionCode"],
            "safeSearch": fixed_params["safeSearch"],
            "videoLicense": video_license,
        },
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_youtube_client(api_key: str) -> Any:
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def fetch_search_response(
    args: argparse.Namespace,
    api_key: str,
    *,
    page_token: str | None = None,
) -> dict[str, Any]:
    youtube = create_youtube_client(api_key)
    params = build_search_params(args, page_token=page_token)

    return youtube.search().list(**params).execute(num_retries=0)


def run_search_json(
    args: argparse.Namespace,
    api_key: str,
    output_path: str | Path,
) -> int:
    response = fetch_search_response(
        args,
        api_key,
        page_token=getattr(args, "page_token", None),
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved raw YouTube search response to {output}")
    return 0

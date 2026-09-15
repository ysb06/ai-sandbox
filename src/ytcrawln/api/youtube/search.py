"""YouTube Search 모듈. Search API 호출 기능 외에 다른 기능들이 추가되면 안 됨."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from ytcrawln.api.youtube import create_youtube_client

FIXED_SEARCH_PARAMS: dict[str, Any] = {
    "collection_method": "search",
    "part": "snippet",
    "type": "video",
    "maxResults": 50,
    "regionCode": "KR",
    "safeSearch": "none",
}


def build_search_params(
    args: argparse.Namespace,
    page_token: str | None = None,
) -> dict[str, Any]:
    params = dict(FIXED_SEARCH_PARAMS)
    params["q"] = args.query
    params["videoLicense"] = args.creative_common
    params["publishedAfter"] = args.published_after
    params["publishedBefore"] = args.published_before
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
        "collection_method": fixed_params["collection_method"],
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
    response = fetch_search_response(args, api_key, page_token=args.page_token)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    search_results_json = json.dumps(response, ensure_ascii=False, indent=2)
    output.write_text(search_results_json, encoding="utf-8")
    print(f"Saved raw YouTube search response to {output}")

    return 0

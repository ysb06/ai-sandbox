import argparse
import hashlib
import json
from collections.abc import Callable
from typing import Any

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from ytcrawl import db

DEFAULT_PRESET = "talking-head"

FIXED_SEARCH_PARAMS: dict[str, Any] = {
    "part": "snippet",
    "type": "video",
    "maxResults": 50,
    "regionCode": "KR",
    "safeSearch": "none",
    "videoLicense": "creativeCommon",
}

PRESET_QUERIES: dict[str, str] = {
    "facecam": "facecam face person",
    "interview": "interview face person",
    "selfie": "selfie face video",
    "talking-head": '"talking head" face person',
    "vlog": "vlog face person",
}


def resolve_preset(args: argparse.Namespace) -> str | None:
    if args.query:
        return None
    return args.preset or DEFAULT_PRESET


def resolve_query(args: argparse.Namespace) -> str:
    if args.query:
        return args.query
    return PRESET_QUERIES[resolve_preset(args)]


def build_search_params(args: argparse.Namespace, page_token: str | None = None) -> dict[str, Any]:
    params = dict(FIXED_SEARCH_PARAMS)
    params["q"] = resolve_query(args)
    if args.published_after:
        params["publishedAfter"] = args.published_after
    if args.published_before:
        params["publishedBefore"] = args.published_before
    if page_token:
        params["pageToken"] = page_token
    return params


def build_request_hash(
    *,
    query: str,
    preset: str | None,
    published_after: str | None,
    published_before: str | None,
    fixed_params: dict[str, Any],
) -> str:
    payload = {
        "query": query,
        "preset": preset,
        "published_after": published_after,
        "published_before": published_before,
        "fixed_params": {
            "part": fixed_params["part"],
            "type": fixed_params["type"],
            "maxResults": int(fixed_params["maxResults"]),
            "regionCode": fixed_params["regionCode"],
            "safeSearch": fixed_params["safeSearch"],
            "videoLicense": fixed_params["videoLicense"],
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


def run_search_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
    *,
    youtube_factory: Callable[[str], Any] = create_youtube_client,
) -> int:
    query = resolve_query(args)
    preset = resolve_preset(args)
    request_hash = build_request_hash(
        query=query,
        preset=preset,
        published_after=args.published_after,
        published_before=args.published_before,
        fixed_params=FIXED_SEARCH_PARAMS,
    )
    page = 1
    page_token = None

    engine = db.create_engine_for_url(db_url)
    db.create_schema(engine)

    with Session(engine) as session:
        if getattr(args, "next_page", True):
            latest_run = db.find_latest_matching_search_run(
                session,
                request_hash=request_hash,
            )
            if latest_run is not None:
                if latest_run.next_page_token is None:
                    print(
                        "No next page token for latest matching search run "
                        f"{latest_run.id}; no new search performed."
                    )
                    return 0
                page_token = latest_run.next_page_token
                page = latest_run.page + 1

    youtube = youtube_factory(api_key)
    params = build_search_params(args, page_token=page_token)
    response = youtube.search().list(**params).execute(num_retries=0)

    with Session(engine) as session:
        run = db.save_search_response(
            session,
            query=query,
            preset=preset,
            published_after=args.published_after,
            published_before=args.published_before,
            fixed_params=FIXED_SEARCH_PARAMS,
            request_hash=request_hash,
            page=page,
            response=response,
        )
        run_id = run.id
        item_count = run.item_count
        session.commit()

    print(f"Saved {item_count} videos from search run {run_id} to {db_url}")
    return 0

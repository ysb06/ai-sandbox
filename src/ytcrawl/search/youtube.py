import argparse
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


def build_search_params(args: argparse.Namespace) -> dict[str, Any]:
    params = dict(FIXED_SEARCH_PARAMS)
    params["q"] = resolve_query(args)
    if args.published_after:
        params["publishedAfter"] = args.published_after
    if args.published_before:
        params["publishedBefore"] = args.published_before
    return params


def create_youtube_client(api_key: str) -> Any:
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def run_search_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
    *,
    youtube_factory: Callable[[str], Any] = create_youtube_client,
) -> int:
    youtube = youtube_factory(api_key)

    params = build_search_params(args)
    response = youtube.search().list(**params).execute(num_retries=0)
    query = resolve_query(args)

    engine = db.create_engine_for_url(db_url)
    db.create_schema(engine)
    with Session(engine) as session:
        run = db.save_search_response(
            session,
            query=query,
            preset=resolve_preset(args),
            published_after=args.published_after,
            published_before=args.published_before,
            fixed_params=FIXED_SEARCH_PARAMS,
            response=response,
        )
        run_id = run.id
        item_count = run.item_count
        session.commit()

    print(f"Saved {item_count} videos from search run {run_id} to {db_url}")
    return 0

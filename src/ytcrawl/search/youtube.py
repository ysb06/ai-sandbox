import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from sqlalchemy import select
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


@dataclass(frozen=True)
class SearchVideoRecord:
    id: int
    video_id: str | None


@dataclass(frozen=True)
class SearchSaveResult:
    run_id: int | None
    item_count: int
    video_records: tuple[SearchVideoRecord, ...] = ()
    skipped: bool = False
    skipped_run_id: int | None = None


def resolve_preset(args: argparse.Namespace) -> str | None:
    if args.query:
        return None
    return args.preset


def resolve_query(args: argparse.Namespace) -> str:
    if args.query:
        return args.query
    return PRESET_QUERIES.get(args.preset, " ")


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


def save_search_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
) -> SearchSaveResult:
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
                    return SearchSaveResult(
                        run_id=None,
                        item_count=0,
                        skipped=True,
                        skipped_run_id=latest_run.id,
                    )
                page_token = latest_run.next_page_token
                page = latest_run.page + 1

    response = fetch_search_response(args, api_key, page_token=page_token)

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
        video_records = tuple(
            SearchVideoRecord(id=video.id, video_id=video.video_id)
            for video in session.scalars(
                select(db.Video).where(db.Video.search_id == run.id).order_by(db.Video.id)
            )
        )
        session.commit()

    return SearchSaveResult(
        run_id=run_id,
        item_count=item_count,
        video_records=video_records,
    )
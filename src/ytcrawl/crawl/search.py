from __future__ import annotations

import argparse
from dataclasses import dataclass

from ytcrawl.db import core, videos, youtube_search_runs
from ytcrawl.search import youtube as youtube_search


@dataclass(frozen=True)
class SnippetCrawlResult:
    run_id: int | None
    item_count: int
    video_records: tuple[videos.VideoRecord, ...]
    skipped: bool = False
    skipped_run_id: int | None = None


def crawl_youtube_snippet(
    args: argparse.Namespace,
    api_key: str,
) -> SnippetCrawlResult:
    page = 1
    page_token = None

    query = youtube_search.resolve_query(args)
    request_hash = youtube_search.build_request_hash(
        query=query,
        published_after=args.published_after,
        published_before=args.published_before,
        fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
    )

    with core.session_scope() as session:
        latest_run = youtube_search_runs.find_latest_matching_search_run(
            session,
            request_hash=request_hash,
        )
        if latest_run is not None:
            if latest_run.next_page_token is None:
                print(
                    "No next page token for latest matching search run "
                    f"{latest_run.id}; no new search performed."
                )
                return SnippetCrawlResult(
                    run_id=None,
                    item_count=0,
                    video_records=(),
                    skipped=True,
                    skipped_run_id=latest_run.id,
                )
            page_token = latest_run.next_page_token
            page = latest_run.page + 1

    response = youtube_search.fetch_search_response(
        args, api_key, page_token=page_token
    )

    with core.session_scope() as session:
        run = youtube_search_runs.create_search_run(
            session,
            query=query,
            published_after=args.published_after,
            published_before=args.published_before,
            fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
            request_hash=request_hash,
            page=page,
            response=response,
        )
        video_records = videos.create_videos_from_search_response(
            session,
            search_id=run.id,
            response=response,
        )
        run_id = run.id
        item_count = run.item_count

    return SnippetCrawlResult(
        run_id=run_id,
        item_count=item_count,
        video_records=video_records,
    )

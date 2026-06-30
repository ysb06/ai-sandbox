from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from ytcrawl.db import core, videos, youtube_search_runs
from ytcrawl.downloader.youtube import download as download_youtube
from ytcrawl.search import youtube as youtube_search


def run_crawl_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
) -> int:
    query = youtube_search.resolve_query(args)
    preset = youtube_search.resolve_preset(args)
    request_hash = youtube_search.build_request_hash(
        query=query,
        preset=preset,
        published_after=args.published_after,
        published_before=args.published_before,
        fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
    )
    page = 1
    page_token = None

    engine = core.create_engine_for_url(db_url)
    core.create_all(engine)

    # 데이터베이스에서 이전 검색 실행을 확인하여 다음 페이지 토큰을 가져옵니다.
    with Session(engine) as session:
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
                return 0
            page_token = latest_run.next_page_token
            page = latest_run.page + 1

    response = youtube_search.fetch_search_response(args, api_key, page_token=page_token)

    # YouTube 검색 실행 및 비디오 레코드를 데이터베이스에 저장합니다.
    with Session(engine) as session:
        run = youtube_search_runs.create_search_run(
            session,
            query=query,
            preset=preset,
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
        session.commit()

    # Todo: YouTube 비디오 별 상세 정보를 가져와서 데이터베이스에 저장합니다. 

    # YouTube 비디오를 다운로드하고 데이터베이스에 경로를 업데이트합니다.
    failures = 0
    successes = 0
    output_dir = str(args.output_dir)
    with Session(engine) as session:
        for record in video_records:
            video_pk = record.id
            video_id = record.video_id
            if not video_id:
                failures += 1
                print(
                    f"Skipping video row {video_pk}: missing video_id.",
                    file=sys.stderr,
                )
                continue

            try:
                downloaded_path = Path(download_youtube(video_id, output_dir)).resolve()
            except Exception as exc:  # noqa: BLE001 - keep crawling remaining videos.
                failures += 1
                print(f"Failed to download {video_id}: {exc}", file=sys.stderr)
                continue

            videos.update_video_path(session, id=video_pk, path=str(downloaded_path))
            successes += 1
        session.commit()

    print(
        f"Saved {item_count} videos from search run {run_id}; "
        f"downloaded {successes}, failed {failures}."
    )
    return 1 if failures else 0

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ytcrawl.db import core, videos, videos_detail, youtube_search_runs
from ytcrawl.downloader.youtube import download as download_youtube
from ytcrawl.search import youtube as youtube_search
from ytcrawl.search import youtube_detail


@dataclass(frozen=True)
class SnippetCrawlResult:
    run_id: int | None
    item_count: int
    video_records: tuple[videos.VideoRecord, ...]
    skipped: bool = False
    skipped_run_id: int | None = None


def _group_video_record_ids_by_video_id(
    video_records: tuple[videos.VideoRecord, ...],
) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for record in video_records:
        if record.video_id:
            grouped.setdefault(record.video_id, []).append(record.id)
    return grouped


def _map_video_detail_items_by_video_id(
    responses: list[dict],
) -> dict[str, dict]:
    detail_items: dict[str, dict] = {}
    for response in responses:
        for item in response.get("items", []):
            video_id = item.get("id")
            if video_id:
                detail_items[video_id] = item
    return detail_items


def crawl_youtube_snippet(
    engine: Engine,
    args: argparse.Namespace,
    api_key: str,
) -> SnippetCrawlResult:
    # 프리셋은 어디까지나 query 생성을 도와주는 형태
    page = 1
    page_token = None

    query = youtube_search.resolve_query(args)
    request_hash = youtube_search.build_request_hash(
        query=query,
        published_after=args.published_after,
        published_before=args.published_before,
        fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
    )

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

    # YouTube 검색 실행 및 비디오 레코드를 데이터베이스에 저장합니다.
    with Session(engine) as session:
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
        session.commit()

    return SnippetCrawlResult(
        run_id=run_id,
        item_count=item_count,
        video_records=video_records,
    )


def crawl_youtube_details(
    engine: Engine,
    api_key: str,
    video_records: tuple[videos.VideoRecord, ...] | None = None,
    search_run_id: int | None = None,
):
    if video_records is None:
        if search_run_id is None:
            raise ValueError("video_records or search_run_id is required.")
        with Session(engine) as session:
            video_records = videos.find_video_records_for_search(
                session,
                search_id=search_run_id,
            )

    # YouTube 비디오 별 상세 정보를 가져와서 데이터베이스에 저장합니다.
    detail_successes = 0
    detail_failures = 0
    record_ids_by_video_id = _group_video_record_ids_by_video_id(video_records)
    if record_ids_by_video_id:
        try:
            detail_responses = youtube_detail.fetch_video_detail_responses(
                list(record_ids_by_video_id),
                api_key,
            )
        except Exception as exc:  # noqa: BLE001 - keep downloading remaining videos.
            detail_failures += sum(
                len(record_ids) for record_ids in record_ids_by_video_id.values()
            )
            print(f"Failed to fetch video details: {exc}", file=sys.stderr)
        else:
            detail_items_by_video_id = _map_video_detail_items_by_video_id(
                detail_responses
            )
            for video_id, record_ids in record_ids_by_video_id.items():
                detail_item = detail_items_by_video_id.get(video_id)
                if detail_item is None:
                    detail_failures += len(record_ids)
                    print(
                        f"Missing video detail for video_id {video_id}.",
                        file=sys.stderr,
                    )
                    continue

                for video_pk in record_ids:
                    with Session(engine) as session:
                        try:
                            videos_detail.create_or_update_video_detail(
                                session,
                                video_ref_id=video_pk,
                                item=detail_item,
                            )
                            session.commit()
                            detail_successes += 1
                        except Exception as exc:  # noqa: BLE001 - keep processing rows.
                            session.rollback()
                            detail_failures += 1
                            print(
                                f"Failed to save video detail for video row "
                                f"{video_pk}: {exc}",
                                file=sys.stderr,
                            )

    return (detail_successes, detail_failures)


def crawl_youtube_videos(
    engine: Engine,
    output_dir: str,
    video_records: tuple[videos.VideoRecord, ...] | None = None,
    video_ids: list[str] | None = None,
):
    if video_records is None:
        with Session(engine) as session:
            if video_ids is None:
                video_records = videos.find_video_records_without_path(session)
            else:
                video_records = videos.find_video_records_by_video_ids(
                    session,
                    video_ids=video_ids,
                )

    record_ids_by_video_id = _group_video_record_ids_by_video_id(video_records)
    missing_video_id_count = len(video_records) - sum(
        len(record_ids) for record_ids in record_ids_by_video_id.values()
    )
    for record in video_records:
        if not record.video_id:
            print(
                f"Skipping video row {record.id}: missing video_id.",
                file=sys.stderr,
            )

    # YouTube 비디오를 다운로드하고 데이터베이스에 경로를 업데이트합니다.
    failures = missing_video_id_count
    successes = 0
    for video_id, record_ids in record_ids_by_video_id.items():
        try:
            downloaded_path = Path(
                download_youtube(video_id, output_dir, overwrite=False)
            ).resolve()
        except Exception as exc:  # noqa: BLE001 - keep crawling remaining videos.
            failures += len(record_ids)
            print(f"Failed to download {video_id}: {exc}", file=sys.stderr)
            continue

        with Session(engine) as session:
            for video_pk in record_ids:
                videos.update_video_path(
                    session, id=video_pk, path=str(downloaded_path)
                )
            session.commit()
        successes += len(record_ids)

    return (successes, failures)


def run_crawl_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
) -> int:
    engine = core.create_engine_for_url(db_url)
    core.create_all(engine)

    snippet_result = crawl_youtube_snippet(engine, args, api_key)
    if snippet_result.skipped:
        return 0

    detail_successes, detail_failures = crawl_youtube_details(
        engine,
        api_key,
        video_records=snippet_result.video_records,
    )
    download_successes, download_failures = crawl_youtube_videos(
        engine,
        str(args.output_dir),
        video_records=snippet_result.video_records,
    )

    print(
        f"Saved {snippet_result.item_count} videos from search run "
        f"{snippet_result.run_id}; "
        f"details saved {detail_successes}, "
        f"detail failed {detail_failures}; "
        f"downloaded {download_successes}, "
        f"failed {download_failures}."
    )
    return 1 if detail_failures or download_failures else 0
